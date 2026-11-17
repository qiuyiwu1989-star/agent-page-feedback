"""服务端测试：python3 -m unittest discover tests
启动一个临时服务（临时数据库、随机端口），把接口完整走一遍。只用标准库。"""
import base64, json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
DATA_PNG = 'data:image/png;base64,' + base64.b64encode(PNG).decode()


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.port = free_port()
        env = dict(os.environ, FB_TEAM_CODE='team-code', FB_ADMIN_CODE='admin-code', FB_DB=os.path.join(cls.tmp.name, 'fb.db'), FB_PORT=str(cls.port), FB_SITE='https://example.com/')
        cls.proc = subprocess.Popen([sys.executable, '-I', os.path.join(ROOT, 'server', 'fb_server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f'http://127.0.0.1:{cls.port}/api/fb/'
        for _ in range(50):
            try: urllib.request.urlopen(cls.base + 'client/fb-loader.js', timeout=1); break
            except Exception: time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5); cls.tmp.cleanup()

    def req(self, path, body=None, code='team-code', name='张三', cid='c1'):
        h = {'Content-Type': 'application/json', 'X-FB-Code': code, 'X-FB-Name': urllib.request.quote(name), 'X-FB-Cid': cid}
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r) as x: return x.status, x.read(), x.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e: return e.code, e.read(), e.headers.get('Content-Type', '')

    def new_item(self, **kw):
        d = dict(page='docs/index.html', url='https://example.com/docs/index.html', title='文档首页', selector='#hero h1', quote='欢迎', body='标题太小', kind='style', priority='high')
        d.update(kw); st, b, _ = self.req('items', d); self.assertEqual(st, 200, b); return json.loads(b)['item']

    def test_auth(self):
        self.assertEqual(self.req('ping', code='wrong')[0], 401)
        self.assertEqual(json.loads(self.req('ping')[1])['role'], 'team')
        self.assertEqual(json.loads(self.req('ping', code='admin-code')[1])['role'], 'admin')

    def test_client_files_are_public(self):
        for f in ('fb-loader.js', 'feedback.js', 'fb-media.js', 'feedback.html', 'vendor/html-to-image.min.js'):
            st, _, ct = self.req('client/' + f, code='')
            self.assertEqual(st, 200, f)
        self.assertEqual(self.req('client/../server/fb_server.py', code='')[0], 404)   # 不能跳出前端目录

    def test_item_flow_with_screenshot(self):
        it = self.new_item(shot=DATA_PNG, images=[DATA_PNG])
        self.assertEqual(sorted(f['kind'] for f in it['files']), ['image', 'shot'])
        st, b, ct = self.req('files/' + it['files'][0]['id']); self.assertEqual(st, 200); self.assertEqual(b, PNG); self.assertEqual(ct, 'image/png')
        self.assertEqual(self.req('files/' + it['files'][0]['id'], code='')[0], 401)   # 附件也要口令
        it = json.loads(self.req(f'items/{it["id"]}/reply', {'body': '同意', 'images': [DATA_PNG]}, name='李四', cid='c2')[1])['item']
        self.assertEqual(len(it['replies'][0]['files']), 1)
        it = json.loads(self.req(f'items/{it["id"]}/status', {'status': 'done', 'note': '已把标题改为 40px'}, code='admin-code', name='负责人', cid='boss')[1])['item']
        self.assertEqual(it['status'], 'done'); self.assertIn('已完成', it['replies'][-1]['body'])   # 负责人可以直接定为已完成（agent 的见 test_team_cannot_bypass_owner）
        it = json.loads(self.req(f'items/{it["id"]}/shot', {'shot': DATA_PNG})[1])['item']
        self.assertEqual([f['kind'] for f in it['files']].count('shot'), 1)   # 替换而不是追加

    def test_rejects_fake_image(self):
        fake = 'data:image/png;base64,' + base64.b64encode(b'<svg onload=alert(1)>').decode()
        st, b, _ = self.req('items', dict(page='x', body='y', images=[fake])); self.assertEqual(st, 400)

    def test_delete_permissions(self):
        it = self.new_item()
        self.assertEqual(self.req(f'items/{it["id"]}/delete', {}, cid='someone-else')[0], 403)
        self.assertEqual(self.req(f'items/{it["id"]}/delete', {}, code='admin-code', cid='admin')[0], 200)

    def test_export_for_agents(self):
        self.new_item(body='按钮颜色不对', shot=DATA_PNG)
        st, b, ct = self.req('export.md'); md = b.decode()
        self.assertEqual(st, 200); self.assertIn('text/markdown', ct); self.assertIn('按钮颜色不对', md); self.assertIn('https://example.com/docs/index.html', md); self.assertIn('/api/fb/files/', md)
        self.assertIn('items', json.loads(self.req('export.json')[1]))

    def test_decisions(self):
        st, b, _ = self.req('decisions', {'scope': 'v1-vs-v2', 'id': '学分:课程A', 'choice': 'zhang', 'note': '以培养方案为准'})
        self.assertEqual(st, 200)
        d = json.loads(self.req('decisions?scope=v1-vs-v2')[1]); self.assertEqual(d['decisions']['学分:课程A']['choice'], 'zhang')

    def test_v2_workflow(self):
        """工单式流程：分诊出方案 → 负责人选方案 → 交付报告 → 再改 → 再交付 → 通过；只有管理员能做决定。"""
        it = self.new_item(body='首页数字太挤')
        iid = it['id']; self.assertEqual(it['arole'], 'team')
        # 成员不能做决定
        st, b, _ = self.req(f'items/{iid}/decide', {'action': 'approve'}); self.assertEqual(st, 403)
        # agent 出方案 → 待决定
        st, b, _ = self.req(f'items/{iid}/propose', {'summary': '两个方向', 'options': [{'key': 'A', 'title': '五列等宽'}, {'key': 'B', 'title': '两行'}], 'images': [DATA_PNG]}, name='Claude')
        it = json.loads(b)['item']; self.assertEqual(it['status'], 'question'); prop = it['replies'][-1]
        self.assertEqual(prop['kind'], 'proposal'); self.assertTrue(prop['ai']); self.assertEqual(prop['meta']['options'][0]['key'], 'A'); self.assertEqual(len(prop['files']), 1)
        # 选方案必须带 option
        st, b, _ = self.req(f'items/{iid}/decide', {'action': 'choose'}, code='admin-code', name='负责人'); self.assertEqual(st, 400)
        st, b, _ = self.req(f'items/{iid}/decide', {'action': 'choose', 'option': 'A'}, code='admin-code', name='负责人')
        it = json.loads(b)['item']; self.assertEqual(it['status'], 'approved'); self.assertEqual(it['signal'], 'choose'); self.assertEqual(it['replies'][-1]['role'], 'admin')
        # queue：在「可以执行」里
        q = json.loads(self.req('queue.json')[1]); self.assertIn(iid, [x['id'] for x in q['act']])
        # 交付报告 → 待验收，改前 / 改后截图挂在报告上
        rep = {'summary': '改成五列等宽', 'why': '负责人选了 A', 'changes': ['ui.css 第 12 行'], 'links': [{'name': '首页', 'url': 'https://example.com/'}], 'rollback': 'bk-001', 'before': DATA_PNG, 'after': DATA_PNG}
        it = json.loads(self.req(f'items/{iid}/report', rep, name='Claude')[1])['item']
        self.assertEqual(it['status'], 'review'); r = it['replies'][-1]
        self.assertEqual(r['kind'], 'report'); self.assertEqual(r['meta']['rollback'], 'bk-001'); self.assertEqual(sorted(f['kind'] for f in r['files']), ['after', 'before'])
        self.assertEqual(self.req(f'items/{iid}/report', {'summary': ''}, name='Claude')[0], 400)
        # 再改要写意见
        self.assertEqual(self.req(f'items/{iid}/decide', {'action': 'rework'}, code='admin-code', name='负责人')[0], 400)
        it = json.loads(self.req(f'items/{iid}/decide', {'action': 'rework', 'note': '数字再大一点'}, code='admin-code', name='负责人')[1])['item']
        self.assertEqual(it['status'], 'approved'); self.assertIn('数字再大一点', it['signal_note'])
        self.req(f'items/{iid}/report', dict(rep, summary='数字放大到 46px'), name='Claude')
        it = json.loads(self.req(f'items/{iid}/decide', {'action': 'accept'}, code='admin-code', name='负责人')[1])['item']
        self.assertEqual(it['status'], 'done')
        # 统计：通过 1 条，返工过所以一次通过率不算它
        s = json.loads(self.req('stats')[1]); self.assertGreaterEqual(s['accepted'], 1); self.assertGreaterEqual(s['reworks'], 1); self.assertGreaterEqual(s['reports'], 2)
        # 第二条：直接可以做 → 交付 → 通过，一次通过
        j = self.new_item(body='按钮颜色')['id']
        self.req(f'items/{j}/decide', {'action': 'approve'}, code='admin-code', name='负责人')
        self.req(f'items/{j}/report', {'summary': '改好了'}, name='Claude')
        self.req(f'items/{j}/decide', {'action': 'accept'}, code='admin-code', name='负责人')
        s = json.loads(self.req('stats')[1]); self.assertEqual(s['first_pass'], 0.5)

    def test_team_cannot_bypass_owner(self):
        iid = self.new_item()['id']
        self.assertEqual(self.req(f'items/{iid}/status', {'status': 'approved'}, name='Claude')[0], 403)   # 「可以做」只能负责人给
        self.assertEqual(self.req(f'items/{iid}/status', {'status': 'done'}, name='Claude')[0], 400)        # 不写改了什么不行
        it = json.loads(self.req(f'items/{iid}/status', {'status': 'done', 'note': '标题改为 40px'}, name='Claude')[1])['item']
        self.assertEqual(it['status'], 'review'); self.assertEqual(it['replies'][-1]['kind'], 'report')    # 按旧接口写「已完成」= 交付报告，等验收
        self.assertEqual(it['replies'][-1]['body'], '标题改为 40px')
        it = json.loads(self.req(f'items/{iid}/status', {'status': 'done'}, code='admin-code', name='负责人')[1])['item']
        self.assertEqual(it['status'], 'done')   # 负责人可以直接改

    def test_corrections_export(self):
        iid = self.new_item(body='联系方式放到页脚')['id']
        self.req(f'items/{iid}/report', {'summary': '加在了页头右上角'}, name='Claude')
        self.req(f'items/{iid}/decide', {'action': 'rework', 'note': '我说的是页脚，不是页头'}, code='admin-code', name='负责人')
        self.req(f'items/{iid}/report', {'summary': '挪到了页脚'}, name='Codex')
        self.req(f'items/{iid}/decide', {'action': 'revert', 'note': '字号太小，先改回来'}, code='admin-code', name='负责人')
        st, b, ct = self.req('corrections.jsonl'); self.assertEqual(st, 200); self.assertIn('ndjson', ct)
        rows = [json.loads(x) for x in b.decode().splitlines() if f'#{iid}"' in x]
        self.assertEqual([(r['决定'], r['我做的'], r['谁做的'], r['他的原话'], r['谁打回的']) for r in rows],
                         [('再改', '加在了页头右上角', 'Claude', '我说的是页脚，不是页头', '负责人'), ('改回来', '挪到了页脚', 'Codex', '字号太小，先改回来', '负责人')])
        self.assertEqual(rows[0]['任务'], '联系方式放到页脚')
        self.assertEqual(self.req('corrections.jsonl', code='')[0], 401)

    def test_prefs_and_export(self):
        st, b, _ = self.req('prefs', {'text': '不要星号和备注，文案要短', 'item': 1}, name='Claude'); self.assertEqual(st, 200)
        pid = json.loads(b)['pref']['id']
        self.assertIn('不要星号', json.loads(self.req('prefs')[1])['prefs'][-1]['text'])
        j = self.new_item(body='待分诊的一条')['id']
        md = self.req('export.md')[1].decode()
        self.assertIn('负责人偏好手册', md); self.assertIn('不要星号', md); self.assertIn('待分诊', md); self.assertIn(f'#{j}', md)
        self.assertEqual(self.req(f'prefs/{pid}/off', {})[0], 403)
        self.assertEqual(self.req(f'prefs/{pid}/off', {}, code='admin-code')[0], 200)
        self.assertNotIn('不要星号', self.req('export.md')[1].decode())

if __name__ == '__main__':
    unittest.main()
