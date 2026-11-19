"""服务端测试：python3 -m unittest discover tests
启动一个临时服务（临时数据库、随机端口），把接口完整走一遍。只用标准库。"""
import base64, json, os, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.error, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==')
DATA_PNG = 'data:image/png;base64,' + base64.b64encode(PNG).decode()


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


PUSHED = []   # 假的飞书群：收到的推送
class Catcher(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        PUSHED.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.port = free_port()
        cls.hook = HTTPServer(('127.0.0.1', 0), Catcher); threading.Thread(target=cls.hook.serve_forever, daemon=True).start()
        # 假的发布脚本：把收到的环境变量记到文件里；版本号是 bad 时失败
        cls.log = os.path.join(cls.tmp.name, 'deploy.log'); script = os.path.join(cls.tmp.name, 'deploy.py')
        with open(script, 'w') as f:
            f.write('import os, sys, time\n'
                    'e = os.environ\n'
                    'open(%r, "a").write("|".join([e["FB_DEPLOY_ACTION"], e["FB_DEPLOY_ID"], e["FB_DEPLOY_REF"], e["FB_ROLLBACK_ID"], str("FB_TEAM_CODE" in e)]) + "\\n")\n'
                    'time.sleep(float(e["FB_DEPLOY_REF"][5:]) if e["FB_DEPLOY_REF"].startswith("slow-") else 0)\n'
                    'print("done", e["FB_DEPLOY_ID"]); sys.exit(1 if e["FB_DEPLOY_REF"] == "bad" else 0)\n' % cls.log)
        env = dict(os.environ, FB_TEAM_CODE='team-code', FB_ADMIN_CODE='admin-code', FB_AGENT_CODE='agent-key', FB_AGENT_ENROLL='enroll-once', FB_STUCK_MIN='0', FB_DB=os.path.join(cls.tmp.name, 'fb.db'), FB_PORT=str(cls.port), FB_SITE='https://example.com/',
                   FB_NOTIFY_URL=f'http://127.0.0.1:{cls.hook.server_port}/hook', FB_DEPLOY_CMD=f'{sys.executable} -I {script}')
        cls.proc = subprocess.Popen([sys.executable, '-I', os.path.join(ROOT, 'server', 'fb_server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f'http://127.0.0.1:{cls.port}/api/fb/'
        for _ in range(50):
            try: urllib.request.urlopen(cls.base + 'client/fb-loader.js', timeout=1); break
            except Exception: time.sleep(.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5); cls.hook.shutdown(); cls.tmp.cleanup()

    def req(self, path, body=None, code='team-code', name='张三', cid='c1', key=None):
        h = {'Content-Type': 'application/json', 'X-FB-Code': code, 'X-FB-Name': urllib.request.quote(name), 'X-FB-Cid': cid}
        if key is not None: h.pop('X-FB-Code'); h['X-FB-Key'] = key
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with urllib.request.urlopen(r) as x: return x.status, x.read(), x.headers.get('Content-Type', '')
        except urllib.error.HTTPError as e: return e.code, e.read(), e.headers.get('Content-Type', '')

    def new_item(self, **kw):
        d = dict(page='docs/index.html', url='https://example.com/docs/index.html', title='文档首页', selector='#hero h1', quote='欢迎', body='标题太小', kind='style', priority='high')
        who = {k: kw.pop(k) for k in ('name', 'cid', 'code') if k in kw}
        d.update(kw); st, b, _ = self.req('items', d, **who); self.assertEqual(st, 200, b); return json.loads(b)['item']

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

    def test_stats_ignore_deleted(self):
        before = json.loads(self.req('stats')[1])
        iid = self.new_item()['id']
        self.req(f'items/{iid}/report', {'summary': '试一下'}, name='Claude')
        self.req(f'items/{iid}/decide', {'action': 'rework', 'note': '不对'}, code='admin-code', name='负责人')
        mid = json.loads(self.req('stats')[1]); self.assertEqual((mid['reworks'], mid['reports']), (before['reworks'] + 1, before['reports'] + 1))
        self.req(f'items/{iid}/delete', {}, code='admin-code')
        after = json.loads(self.req('stats')[1]); self.assertEqual((after['reworks'], after['reports']), (before['reworks'], before['reports']))

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

    def lastlog(self):
        with open(self.log) as f: return f.read().splitlines()[-1].split('|')

    def wait_deploy(self, did):
        for _ in range(100):
            d = json.loads(self.req(f'deploys/{did}')[1])['deploy']
            if d['status'] != 'running': return d
            time.sleep(.05)
        self.fail('发布没有结束')

    def test_assignee_and_check(self):
        """负责方：字段或「【交给 X】」；queue.json?for= 只看自己的；改负责方要提出人或负责人；交叉核对不算拍板。"""
        a = self.new_item(body='【交给 Codex】重建首页生成器', page='_requests', url='', title='需求', name='Claude', cid='claude')
        b = self.new_item(body='按钮颜色', assignee='向为老师')
        c = self.new_item(body='没有负责方')
        self.assertEqual((a['assignee'], b['assignee'], c['assignee']), ('Codex', '向为老师', None))
        q = lambda f: {x['id'] for k in ('act', 'triage', 'waiting', 'review') for x in json.loads(self.req('queue.json?for=' + urllib.request.quote(f))[1])[k]}
        self.assertIn(a['id'], q('Codex')); self.assertNotIn(c['id'], q('Codex')); self.assertNotIn(b['id'], q('Codex'))
        self.assertIn(c['id'], q('Claude,-')); self.assertNotIn(a['id'], q('Claude,-'))
        self.assertIn(a['id'], q('codex'))                                        # 不分大小写
        md = self.req('export.md?for=Codex')[1].decode(); self.assertIn('负责方 Codex', md); self.assertIn('需求（不依附页面）', md); self.assertNotIn('按钮颜色', md)
        # 改负责方
        self.assertEqual(self.req(f'items/{a["id"]}/assign', {'assignee': 'Claude'}, cid='someone')[0], 403)
        it = json.loads(self.req(f'items/{a["id"]}/assign', {'assignee': 'Claude', 'note': '生成器先不动'}, code='admin-code', name='负责人')[1])['item']
        self.assertEqual(it['assignee'], 'Claude'); self.assertIn('把负责方改为「Claude」', it['replies'][-1]['body'])
        # 交叉核对
        st0 = it['status']
        it = json.loads(self.req(f'items/{a["id"]}/check', {'body': '按「怎么算做完」逐条核对，第 2 条没做到', 'result': 'fail'}, name='Codex', cid='codex')[1])['item']
        self.assertEqual(it['replies'][-1]['kind'], 'check'); self.assertEqual(it['replies'][-1]['meta']['result'], 'fail'); self.assertEqual(it['status'], st0)
        self.assertEqual(self.req(f'items/{a["id"]}/check', {'body': ''}, name='Codex')[0], 400)
        self.assertIn('【核对有问题】', self.req('export.md')[1].decode())

    def mcp(self, msg, code='team-code', name='Codex'):
        r = urllib.request.Request(self.base + 'mcp', data=json.dumps(msg).encode(), headers={'Content-Type': 'application/json', 'X-FB-Code': code, 'X-FB-Name': name})
        try:
            with urllib.request.urlopen(r) as x: b = x.read(); return x.status, (json.loads(b) if b else None)
        except urllib.error.HTTPError as e: return e.code, None

    def call(self, tool, args, **kw):
        st, r = self.mcp({'jsonrpc': '2.0', 'id': 9, 'method': 'tools/call', 'params': {'name': tool, 'arguments': args}}, **kw)
        self.assertEqual(st, 200); return r['result']['isError'], r['result']['content'][0]['text']

    def test_only_owner_closes_as_wontfix(self):
        """「不改」是负责人的决定：成员和 agent 都不能用改状态绕过去。"""
        iid = self.new_item(body='不改的守卫')['id']
        self.assertEqual(self.req(f'items/{iid}/status', {'status': 'wontfix', 'note': '我觉得不用改'}, name='Claude')[0], 403)
        self.assertEqual(self.req(f'items/{iid}/status', {'status': 'wontfix'}, key='agent-key', name='Claude')[0], 403)
        self.assertEqual(json.loads(self.req(f'items/{iid}/decide', {'action': 'wontfix'}, code='admin-code', name='负责人')[1])['item']['status'], 'wontfix')

    def test_presence_and_activity(self):
        """在场：谁来过、谁在做哪张、是否可能卡住；动态：所有工单上的事按时间倒序。"""
        iid = self.new_item(body='在场测试')['id']
        self.req(f'items/{iid}/decide', {'action': 'approve'}, code='admin-code', name='负责人')
        self.req(f'items/{iid}/status', {'status': 'doing', 'note': 'Codex-2 处理中'}, name='Codex-2', cid='c2')
        self.req('queue.json', name='Codex-2')
        pr = json.loads(self.req('presence')[1])
        me = next(p for p in pr['people'] if p['name'] == 'Codex-2')
        self.assertIn(iid, me.get('doing', [])); self.assertEqual(me['what'], '看清单')
        d = next(x for x in pr['doing'] if x['item'] == iid); self.assertEqual(d['who'], 'Codex-2'); self.assertTrue(d['stuck'])   # 测试里 FB_STUCK_MIN=0
        ev = json.loads(self.req('activity?limit=20')[1])['events']
        self.assertEqual([e['kind'] for e in ev if e['item'] == iid][:3], ['status', 'decision', 'new'])

    def test_mentions_and_inbox(self):
        """@ 进对方收件箱（@Codex 也到 Codex-1）；交给我且可以做的也在；标已读只标自己的。"""
        iid = self.new_item(body='首页标题 @邱懿武 看一下', name='张三')['id']
        self.req(f'items/{iid}/reply', {'body': '@Codex 能帮忙查一下生成器吗？@张三 先别动'}, name='Claude')
        box = json.loads(self.req('inbox', name='Codex-1')[1])
        m = [x for x in box['items'] if x['type'] == 'mention' and x['item'] == iid]
        self.assertEqual(len(m), 1); self.assertFalse(m[0]['read']); self.assertIn('生成器', m[0]['text']); self.assertGreaterEqual(box['unread'], 1)
        q = json.loads(self.req('queue.json?for=Codex')[1]); self.assertIn(iid, [x['item'] for x in q['mentioned']]); self.assertGreaterEqual(q['summary']['mentioned'], 1)
        # 别人标不了 Codex 的已读；Codex-1 自己标
        self.assertEqual(json.loads(self.req('inbox/read', {'items': [iid]}, name='张三')[1])['read'], 1)   # 张三只标掉了 @张三 的那条
        self.assertFalse(next(x for x in json.loads(self.req('inbox', name='Codex-1')[1])['items'] if x['type'] == 'mention' and x['item'] == iid)['read'])
        self.req('inbox/read', {'items': [iid]}, name='Codex-1')
        self.assertTrue(next(x for x in json.loads(self.req('inbox', name='Codex-1')[1])['items'] if x['type'] == 'mention' and x['item'] == iid)['read'])
        # 负责人：自己的 @ + 等决定 / 验收；交给某人且可以做的进他的收件箱
        self.assertIn(iid, [x['item'] for x in json.loads(self.req('inbox', code='admin-code', name='邱懿武')[1])['items'] if x['type'] == 'mention'])
        j = self.new_item(body='交给向为', assignee='向为')['id']; self.req(f'items/{j}/decide', {'action': 'approve'}, code='admin-code', name='负责人')
        self.assertIn(j, [x['item'] for x in json.loads(self.req('inbox', name='向为')[1])['items'] if x['type'] == 'todo'])
        # 自己 @ 自己不算；邮箱样式的 a@b 不算
        k = self.new_item(body='联系 me@example.com，@张三 自己记一下', name='张三')['id']
        self.assertNotIn(k, [x['item'] for x in json.loads(self.req('inbox', name='张三')[1])['items'] if x['type'] == 'mention'])
        self.assertNotIn(k, [x['item'] for x in json.loads(self.req('inbox', name='example.com')[1])['items']])

    def test_roster(self):
        """参与方名单：在工单里出现过的自动列出；只有负责人能登记、停用；带工作情况。"""
        iid = self.new_item(body='名单测试', assignee='Codex-7', name='王老师')['id']
        self.req(f'items/{iid}/decide', {'action': 'approve'}, code='admin-code', name='负责人')
        ppl = {p['name']: p for p in json.loads(self.req('roster')[1])['people']}
        self.assertIn('王老师', ppl); self.assertFalse(ppl['王老师']['registered']); self.assertEqual(ppl['Codex-7']['kind'], 'agent'); self.assertGreaterEqual(ppl['Codex-7']['approved'], 1)
        self.assertEqual(self.req('roster', {'name': '王老师', 'label': '老师'})[0], 403)                       # 成员不能改
        self.assertEqual(self.req('roster', {'name': 'bad name!'}, code='admin-code')[0], 400)
        st, b, _ = self.req('roster', {'name': '王老师', 'kind': 'human', 'label': '老师', 'scope': '课程页内容审核'}, code='admin-code', name='负责人'); self.assertEqual(st, 200)
        w = next(p for p in json.loads(b)['people'] if p['name'] == '王老师'); self.assertTrue(w['registered']); self.assertEqual(w['scope'], '课程页内容审核')
        self.req('roster', {'name': '王老师', 'label': '老师', 'scope': '课程页'}, code='admin-code')            # 再登记 = 修改，不重复
        self.assertEqual(sum(1 for p in json.loads(self.req('roster')[1])['people'] if p['name'] == '王老师'), 1)
        st, b, _ = self.req('roster/' + urllib.request.quote('王老师') + '/off', {}, code='admin-code'); self.assertEqual(st, 200)
        self.assertFalse(next(p for p in json.loads(b)['people'] if p['name'] == '王老师')['registered'])
        # MCP 能读
        err, t = self.call('roster', {}); self.assertFalse(err); self.assertIn('Codex-7', t)

    def test_mcp(self):
        """MCP：连上就拿到规则；工具按同样的权限转调接口；没有拍板工具。"""
        self.assertIn(self.mcp({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}, code='wrong')[0], (401, 429))   # 口令错（前面的测试可能已经触发了限流）
        st, r = self.mcp({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-06-18'}})
        self.assertEqual(r['result']['protocolVersion'], '2025-06-18'); ins = r['result']['instructions']
        self.assertIn('只把负责人', ins); self.assertIn('署名：Codex', ins); self.assertIn('不能拍板', ins)
        self.assertEqual(self.mcp({'jsonrpc': '2.0', 'method': 'notifications/initialized'})[0], 202)
        names = {t['name'] for t in self.mcp({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'})[1]['result']['tools']}
        self.assertIn('claim', names); self.assertIn('report', names); self.assertFalse(any('decide' in n for n in names))
        # 开需求 → 认领 → 交付 → 别人核对；状态变化和直接调接口一样
        err, t = self.call('create_request', {'title': 'MCP 测试需求', 'body': '怎么算做完：有一个按钮', 'assignee': 'Codex'}, name='Claude'); self.assertFalse(err, t)
        iid = json.loads(t)['item']['id']
        err, t = self.call('list_tasks', {'for': 'Codex'}); self.assertIn(iid, [x['id'] for x in json.loads(t)['triage']])
        err, t = self.call('claim', {'id': iid}); self.assertEqual(json.loads(t)['item']['status'], 'doing'); self.assertIn('Codex 处理中', json.loads(t)['item']['replies'][-1]['body'])
        err, t = self.call('report', {'id': iid, 'summary': '加了按钮', 'changes': ['1. 有按钮 ✓'], 'rollback': 'deploy#3'}); self.assertEqual(json.loads(t)['item']['status'], 'review')
        err, t = self.call('crosscheck', {'id': iid, 'result': 'pass', 'body': '逐条核对通过'}, name='Claude'); self.assertEqual(json.loads(t)['item']['replies'][-1]['kind'], 'check')
        err, t = self.call('get_task', {'id': iid}); self.assertEqual(json.loads(t)['assignee'], 'Codex')
        err, t = self.call('briefing', {'for': 'Codex'}); self.assertIn('MCP 测试需求', t)
        # 权限照旧：成员口令发不了汇总；没有的工具报错
        err, t = self.call('send_digest', {'title': 'x', 'markdown': 'y'}); self.assertTrue(err)
        st, r = self.mcp({'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'decide', 'arguments': {}}}); self.assertIn('error', r)

    def test_marks(self):
        """框选区域和截图上的圈画：存下来，清单里告诉 agent；乱七八糟的输入被丢掉。"""
        marks = [{'t': 'region', 'x': 100, 'y': 2400.4, 'w': 600, 'h': 320}, {'t': 'pen', 'x': .1, 'y': .2, 'w': .3, 'h': .1}, {'t': 'evil', 'x': 1}, 'junk']
        it = self.new_item(body='这一块整体太挤', marks=marks, shot=DATA_PNG)
        self.assertEqual([m['t'] for m in it['marks']], ['region', 'pen'])
        md = self.req('export.md')[1].decode()
        self.assertIn('框选区域', md); self.assertIn('100,2400 起 600×320', md); self.assertIn('红笔圈出了 1 处', md)
        self.assertEqual(self.new_item(body='没有标记')['marks'], [])

    def test_upgrade_old_database(self):
        """v1 的旧数据库：启动时自动补列，旧数据照常读出。"""
        import sqlite3
        db = os.path.join(self.tmp.name, 'old.db'); c = sqlite3.connect(db)
        c.executescript('''CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, page TEXT NOT NULL, title TEXT, selector TEXT, quote TEXT,
          rx REAL, ry REAL, px REAL, py REAL, vw INTEGER, vh INTEGER, device TEXT, kind TEXT, priority TEXT, body TEXT NOT NULL,
          author TEXT, cid TEXT, status TEXT DEFAULT 'open', created REAL, updated REAL, deleted INTEGER DEFAULT 0);
          CREATE TABLE replies (id INTEGER PRIMARY KEY AUTOINCREMENT, item INTEGER NOT NULL, author TEXT, cid TEXT, body TEXT, kind TEXT DEFAULT 'reply', created REAL);
          INSERT INTO items (page, body, author, status, created, updated) VALUES ('index.html', '旧批注', '王五', 'open', 1, 1);
          INSERT INTO items (page, body, author, status, created, updated) VALUES ('handoff', '【交给 Codex】重建生成器', 'Claude', 'open', 2, 2);''')
        c.commit(); c.close()
        port = free_port(); env = dict(os.environ, FB_TEAM_CODE='t', FB_DB=db, FB_PORT=str(port))
        p = subprocess.Popen([sys.executable, '-I', os.path.join(ROOT, 'server', 'fb_server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/fb/queue.json', headers={'X-FB-Code': 't'}), timeout=1) as r: q = json.loads(r.read()); break
                except Exception: time.sleep(.1)
            self.assertEqual(q['triage'][0]['body'], '旧批注'); self.assertEqual(q['triage'][0]['marks'], [])
            self.assertEqual(q['triage'][1]['assignee'], 'Codex')                 # 过渡写法自动识别成负责方
        finally: p.terminate(); p.wait(5)

    def test_agent_enroll(self):
        """一次性登记：口令由云端代理加到请求头，谁也不用看到；登记码只能用一次；只存指纹。"""
        def enroll(tok, key):
            r = urllib.request.Request(self.base + 'agent-enroll', data=b'{}', headers={'X-FB-Enroll': tok, 'X-FB-Key': key})
            try:
                with urllib.request.urlopen(r) as x: return x.status
            except urllib.error.HTTPError as e: return e.code
        self.assertEqual(enroll('wrong', 'k' * 20), 401)
        self.assertEqual(enroll('enroll-once', 'short'), 400)
        self.assertEqual(self.req('ping', key='owner-made-up-key-123')[0], 401)
        self.assertEqual(enroll('enroll-once', 'owner-made-up-key-123'), 200)
        self.assertEqual(json.loads(self.req('ping', key='owner-made-up-key-123')[1])['role'], 'agent')
        self.assertEqual(enroll('enroll-once', 'another-key-0000000'), 409)   # 用过就失效
        self.assertEqual(self.req('ping', key='another-key-0000000')[0], 401)
        import sqlite3
        with sqlite3.connect(os.path.join(self.tmp.name, 'fb.db')) as c: stored = [r[0] for r in c.execute('SELECT hash FROM agent_keys')]
        self.assertNotIn('owner-made-up-key-123', stored)                      # 数据库里只有指纹

    def test_agent_key(self):
        """agent 口令可以用 X-FB-Key 请求头（云端环境的密钥只能存这类名字）；权限同成员，不能拍板。"""
        self.assertEqual(json.loads(self.req('ping', key='agent-key')[1])['role'], 'agent')
        self.assertEqual(json.loads(self.req('ping', key='team-code')[1])['role'], 'team')
        self.assertEqual(self.req('ping', key='nope')[0], 401)
        iid = self.new_item(body='agent 不能拍板')['id']
        self.assertEqual(self.req(f'items/{iid}/decide', {'action': 'approve'}, key='agent-key', name='Claude')[0], 403)
        self.assertEqual(self.req(f'items/{iid}/reply', {'body': '打算把字号改为 18px'}, key='agent-key', name='Claude')[0], 200)

    def test_queue_triage_flags(self):
        """agent 回复过、之后没人说话的待分诊任务算已分诊，定时任务不用再看；有人再说话就重新算新的。"""
        iid = self.new_item(body='分诊标记')['id']
        q = json.loads(self.req('queue.json')[1]); t = {x['id']: x['triaged'] for x in q['triage']}
        self.assertFalse(t[iid]); n0 = q['summary']['triage_new']
        time.sleep(.01); self.req(f'items/{iid}/reply', {'body': '打算这样改，请负责人点「可以做」'}, name='Claude')
        q = json.loads(self.req('queue.json')[1]); self.assertTrue({x['id']: x['triaged'] for x in q['triage']}[iid]); self.assertEqual(q['summary']['triage_new'], n0 - 1)
        time.sleep(.01); self.req(f'items/{iid}/reply', {'body': '补充：手机上也有这个问题'}, name='李四')
        q = json.loads(self.req('queue.json')[1]); self.assertFalse({x['id']: x['triaged'] for x in q['triage']}[iid])

    def test_digest(self):
        self.assertEqual(self.req('digest', {'title': '汇总', 'markdown': '内容'})[0], 403)   # 成员口令不行
        self.assertEqual(self.req('digest', {'title': '', 'markdown': '内容'}, key='agent-key')[0], 400)
        n = len(PUSHED)
        st, b, _ = self.req('digest', {'title': '本轮汇总 10:00', 'markdown': '- 做了 #3\n- 等你决定 #5', 'buttons': [{'text': '任务中心', 'url': 'https://example.com/fb'}, {'text': '坏的', 'url': 'javascript:x'}]}, key='agent-key', name='Claude')
        self.assertEqual(st, 200, b); self.assertTrue(json.loads(b)['sent'])
        for _ in range(50):
            if len(PUSHED) > n: break
            time.sleep(.05)
        cardj = json.dumps(PUSHED[-1], ensure_ascii=False)
        self.assertIn('本轮汇总 10:00', cardj); self.assertIn('任务中心', cardj); self.assertNotIn('javascript', cardj)
        self.assertNotIn('agent-key', cardj)
        self.assertEqual(json.loads(self.req('digests')[1])['digests'][0]['title'], '本轮汇总 10:00')

    def test_deploy_and_rollback(self):
        self.assertEqual(self.req('deploy', {'ref': 'main'})[0], 403)                          # 成员口令不能发布
        self.assertEqual(self.req('deploy', {'ref': 'main; rm -rf /'}, key='agent-key')[0], 400)   # 版本号只能是安全字符
        st, b, _ = self.req('deploy', {'ref': 'abc123', 'items': [7, 8], 'note': '改首页'}, key='agent-key', name='Claude'); self.assertEqual(st, 200, b)
        d = json.loads(b)['deploy']; self.assertEqual(d['status'], 'running')
        d = self.wait_deploy(d['id']); self.assertEqual(d['status'], 'ok'); self.assertIn(f'done {d["id"]}', d['output']); self.assertEqual(json.loads(d['items']), [7, 8])
        line = self.lastlog()
        self.assertEqual(line, ['deploy', str(d['id']), 'abc123', '', 'False'])                # 脚本拿不到口令
        # 回滚：新登记一次 rollback，告诉脚本恢复到哪次发布之前
        rb = self.wait_deploy(json.loads(self.req(f'deploys/{d["id"]}/rollback', {'note': '负责人说改回来'}, key='agent-key', name='Claude')[1])['deploy']['id'])
        self.assertEqual((rb['action'], rb['rollback_of'], rb['status']), ('rollback', d['id'], 'ok'))
        self.assertEqual(self.lastlog()[:4], ['rollback', str(rb['id']), '', str(d['id'])])
        self.assertEqual(self.req(f'deploys/{rb["id"]}/rollback', {}, key='agent-key')[0], 404)  # 只能回滚「发布」
        # 失败会推送
        n = len(PUSHED); bad = self.wait_deploy(json.loads(self.req('deploy', {'ref': 'bad'}, code='admin-code', name='负责人')[1])['deploy']['id'])
        self.assertEqual(bad['status'], 'failed')
        for _ in range(50):
            if len(PUSHED) > n: break
            time.sleep(.05)
        self.assertIn('发布失败', json.dumps(PUSHED[-1], ensure_ascii=False))
        # 同一时间只跑一个
        slow = json.loads(self.req('deploy', {'ref': 'slow-0.6'}, key='agent-key')[1])['deploy']
        self.assertEqual(self.req('deploy', {'ref': 'x'}, key='agent-key')[0], 409)
        self.wait_deploy(slow['id'])
        self.assertTrue(json.loads(self.req('deploys')[1])['enabled'])

if __name__ == '__main__':
    unittest.main()
