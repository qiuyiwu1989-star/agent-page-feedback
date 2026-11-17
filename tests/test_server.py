"""服务端测试：python3 -m unittest discover tests
启动一个临时服务（临时数据库、随机端口），把接口完整走一遍。只用标准库。"""
import base64, json, os, socket, subprocess, sys, tempfile, time, unittest, urllib.error, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
DATA_PNG = 'data:image/png;base64,' + base64.b64encode(PNG).decode()


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class ServerTest(unittest.TestCase):
    EXTRA_ENV = {}

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.port = free_port()
        env = dict(os.environ, FB_TEAM_CODE='team-code', FB_ADMIN_CODE='admin-code', FB_DB=os.path.join(cls.tmp.name, 'fb.db'), FB_PORT=str(cls.port), FB_SITE='https://example.com/', **cls.EXTRA_ENV)
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
        it = json.loads(self.req(f'items/{it["id"]}/status', {'status': 'done', 'note': '已把标题改为 40px'}, code='admin-code', name='王老师', cid='boss')[1])['item']
        self.assertEqual(it['status'], 'done'); self.assertIn('已改好', it['replies'][-1]['body'])   # 管理员可以直接定为已改好
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

    # ---------- 验收流程 ----------
    def agent(self, path, body): return self.req(path, body, name='Claude', cid='agent')
    def boss(self, path, body): return self.req(path, body, code='admin-code', name='王老师', cid='boss')
    def item_of(self, res): self.assertEqual(res[0], 200, res[1]); return json.loads(res[1])['item']

    def test_agent_done_becomes_review(self):
        it = self.new_item()
        self.assertEqual(self.agent(f'items/{it["id"]}/status', {'status': 'done'})[0], 400)   # 不写改了什么，不能提交验收
        st, b, _ = self.agent(f'items/{it["id"]}/status', {'status': 'done', 'note': '标题改为 40px'}); j = json.loads(b)
        self.assertEqual(j['item']['status'], 'review'); self.assertIn('验收', j['info'])   # 「我改好了」不等于「认可了」
        self.assertEqual(j['item']['replies'][-1]['kind'], 'review')
        self.assertEqual(self.agent(f'items/{it["id"]}/status', {'status': 'approved'})[0], 400)   # 不能自己给自己通过

    def test_review_accept_live(self):
        it = self.new_item()
        it = self.item_of(self.agent(f'items/{it["id"]}/review', {'note': '按钮改成蓝色', 'images': [DATA_PNG]}))
        rv = it['replies'][-1]; self.assertEqual(it['status'], 'review'); self.assertEqual(rv['meta']['stage'], 'live'); self.assertEqual(rv['files'][0]['kind'], 'after')
        self.assertEqual(self.agent(f'items/{it["id"]}/accept', {})[0], 403)   # 团队口令不能验收
        self.assertEqual(self.agent(f'items/{it["id"]}/reject', {'reason': '不行'})[0], 403)
        it = self.item_of(self.boss(f'items/{it["id"]}/accept', {'note': '可以'}))
        self.assertEqual(it['status'], 'done'); self.assertEqual(it['replies'][-1]['kind'], 'accept')   # 已在线上的改动，通过即完成
        self.assertEqual(self.boss(f'items/{it["id"]}/accept', {})[0], 409)   # 不在待验收状态

    def test_review_preview_accept_then_publish(self):
        it = self.new_item()
        self.assertEqual(self.agent(f'items/{it["id"]}/review', {'note': 'x', 'preview_url': 'javascript:alert(1)'})[0], 400)
        self.assertEqual(self.agent(f'items/{it["id"]}/review', {'note': 'x', 'preview_url': '//evil.example/x'})[0], 400)
        self.assertEqual(self.agent(f'items/{it["id"]}/publish', {})[0], 409)   # 没通过不能标记上线
        it = self.item_of(self.agent(f'items/{it["id"]}/review', {'note': '首屏重排', 'preview_url': 'https://staging.example.com/docs/'}))
        self.assertEqual(it['replies'][-1]['meta'], {'preview_url': 'https://staging.example.com/docs/', 'stage': 'preview'})
        it = self.item_of(self.boss(f'items/{it["id"]}/accept', {}))
        self.assertEqual(it['status'], 'approved')   # 预览环境的改动：通过后等上线
        self.assertIn('首屏重排', self.req('export.md')[1].decode())   # 已通过的进入 agent 的待办
        it = self.item_of(self.agent(f'items/{it["id"]}/status', {'status': 'done', 'note': '已发布到正式站'}))
        self.assertEqual(it['status'], 'done'); self.assertEqual(it['replies'][-1]['kind'], 'publish')

    def test_reject_needs_reason_and_becomes_correction(self):
        it = self.new_item(body='联系方式放到页脚')
        self.agent(f'items/{it["id"]}/review', {'note': '加在了页头右上角', 'preview_url': '/preview/a.html'})
        self.assertEqual(self.boss(f'items/{it["id"]}/reject', {'reason': '  '})[0], 400)   # 打回必须写理由
        it = self.item_of(self.boss(f'items/{it["id"]}/reject', {'reason': '我说的是页脚，不是页头'}))
        self.assertEqual(it['status'], 'open'); self.assertEqual(it['replies'][-1]['kind'], 'reject')
        st, b, ct = self.req('corrections.jsonl'); self.assertEqual(st, 200); self.assertIn('ndjson', ct)
        rows = [json.loads(x) for x in b.decode().splitlines()]; row = [r for r in rows if r['来源'].endswith(f'#{it["id"]}')][0]
        self.assertEqual((row['任务'], row['我做的'], row['他的原话'], row['谁做的'], row['谁打回的'], row['预览']),
                         ('联系方式放到页脚', '加在了页头右上角', '我说的是页脚，不是页头', 'Claude', '王老师', '/preview/a.html'))
        # 第二轮：重新提交、通过后再打回（已通过的也可以打回）
        self.agent(f'items/{it["id"]}/review', {'note': '挪到了页脚', 'preview_url': '/preview/b.html'})
        self.boss(f'items/{it["id"]}/accept', {})
        it = self.item_of(self.boss(f'items/{it["id"]}/reject', {'reason': '字号太小'}))
        rows = [json.loads(x) for x in self.req('corrections.jsonl')[1].decode().splitlines() if f'#{it["id"]}' in x]
        self.assertEqual([r['我做的'] for r in rows], ['加在了页头右上角', '挪到了页脚'])   # 每次打回对应当时那一版

    def test_ping_reports_acceptance(self):
        self.assertTrue(json.loads(self.req('ping')[1])['acceptance'])

    def test_decisions(self):
        st, b, _ = self.req('decisions', {'scope': 'v1-vs-v2', 'id': '学分:课程A', 'choice': 'zhang', 'note': '以培养方案为准'})
        self.assertEqual(st, 200)
        d = json.loads(self.req('decisions?scope=v1-vs-v2')[1]); self.assertEqual(d['decisions']['学分:课程A']['choice'], 'zhang')


class AcceptanceOffTest(ServerTest):
    """FB_ACCEPTANCE=0：回到原来的做法，谁都可以直接标「已改好」。"""
    EXTRA_ENV = {'FB_ACCEPTANCE': '0'}

    def test_agent_done_becomes_review(self):
        it = self.new_item()
        it = self.item_of(self.agent(f'items/{it["id"]}/status', {'status': 'done', 'note': '改好了'}))
        self.assertEqual(it['status'], 'done'); self.assertIn('已改好', it['replies'][-1]['body'])

    def test_review_preview_accept_then_publish(self): pass   # 该流程只在开启验收时存在
    def test_ping_reports_acceptance(self):
        self.assertFalse(json.loads(self.req('ping')[1])['acceptance'])


if __name__ == '__main__':
    unittest.main()
