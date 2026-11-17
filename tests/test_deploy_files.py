"""按文件发布的端到端测试：临时网站目录 + 静态服务 + 批注服务（FB_DEPLOY_CMD=tools/deploy_files.py），
用 tools/fb.py fetch / deploy --dir / rollback 走一遍，包括「别人同时改了同一个文件」的冲突。"""
import functools, json, os, socket, subprocess, sys, tempfile, threading, time, unittest, urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    s = socket.socket(); s.bind(('127.0.0.1', 0)); p = s.getsockname()[1]; s.close(); return p


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a): pass


class DeployFilesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); t = cls.tmp.name
        cls.site = os.path.join(t, 'site'); os.makedirs(os.path.join(cls.site, 'css'))
        cls.put('index.html', '<h1>首页</h1>'); cls.put('css/a.css', 'h1{font-size:32px}'); cls.put('old.html', '旧页面')
        cls.web = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=cls.site))
        threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        cls.port = free_port()
        env = dict(os.environ, FB_TEAM_CODE='t', FB_AGENT_CODE='agent-key-1234567', FB_DB=os.path.join(t, 'data', 'fb.db'), FB_PORT=str(cls.port),
                   FB_DEPLOY_CMD=f'{sys.executable} -I {ROOT}/tools/deploy_files.py', FB_SITE_ROOT=cls.site)
        cls.proc = subprocess.Popen([sys.executable, '-I', os.path.join(ROOT, 'server', 'fb_server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(f'http://127.0.0.1:{cls.port}/api/fb/client/fb-loader.js', timeout=1).close(); break
            except Exception: time.sleep(.1)
        cls.env = dict(os.environ, FB_API=f'http://127.0.0.1:{cls.port}/api/fb/', FB_KEY='agent-key-1234567', FB_SITE_URL=f'http://127.0.0.1:{cls.web.server_port}/')
        for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'): cls.env.pop(k, None)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate(); cls.proc.wait(5); cls.web.shutdown(); cls.web.server_close(); cls.tmp.cleanup()

    @classmethod
    def put(cls, rel, text):
        with open(os.path.join(cls.site, rel), 'w', encoding='utf-8') as f: f.write(text)

    def get(self, rel):
        p = os.path.join(self.site, rel)
        if not os.path.exists(p): return None
        with open(p, encoding='utf-8') as f: return f.read()

    def fb(self, *args):
        r = subprocess.run([sys.executable, '-I', os.path.join(ROOT, 'tools', 'fb.py'), *args], env=self.env, capture_output=True, text=True, timeout=60)
        return r.returncode, r.stdout + r.stderr

    def edit(self, work, rel, text):
        os.makedirs(os.path.dirname(os.path.join(work, rel)), exist_ok=True)
        with open(os.path.join(work, rel), 'w', encoding='utf-8') as f: f.write(text)

    def test_deploy_conflict_rollback(self):
        w = os.path.join(self.tmp.name, 'w1')
        self.assertEqual(self.fb('fetch', w, 'index.html', 'css/a.css', 'old.html')[0], 0)
        self.edit(w, 'css/a.css', 'h1{font-size:40px}'); os.makedirs(os.path.join(w, 'js')); self.edit(w, 'js/new.js', 'console.log(1)')
        code, out = self.fb('deploy', '--dir', w, '--item', '7', '--delete', 'old.html', '--note', '标题放大')
        self.assertEqual(code, 0, out); self.assertIn('回滚编号：deploy#', out)
        d1 = int(out.rsplit('deploy#', 1)[1].split()[0])
        self.assertEqual(self.get('css/a.css'), 'h1{font-size:40px}'); self.assertEqual(self.get('js/new.js'), 'console.log(1)')
        self.assertIsNone(self.get('old.html')); self.assertEqual(self.get('index.html'), '<h1>首页</h1>')   # 没改的不动

        # 冲突：agent 取了 index.html 之后，别人改了它 → 什么都不改
        w2 = os.path.join(self.tmp.name, 'w2'); self.fb('fetch', w2, 'index.html')
        self.put('index.html', '<h1>首页（别人刚改的）</h1>')
        self.edit(w2, 'index.html', '<h1>首页（agent 改的）</h1>')
        code, out = self.fb('deploy', '--dir', w2, '--item', '8')
        self.assertNotEqual(code, 0); self.assertIn('冲突', out); self.assertEqual(self.get('index.html'), '<h1>首页（别人刚改的）</h1>')

        # 回滚第一次发布：改的换回、新增的删掉、删掉的放回
        code, out = self.fb('rollback', str(d1)); self.assertEqual(code, 0, out)
        self.assertEqual(self.get('css/a.css'), 'h1{font-size:32px}'); self.assertIsNone(self.get('js/new.js')); self.assertEqual(self.get('old.html'), '旧页面')

        # 回滚冲突：发布之后又被别人改过，就不回滚
        w3 = os.path.join(self.tmp.name, 'w3'); self.fb('fetch', w3, 'css/a.css'); self.edit(w3, 'css/a.css', 'h1{color:red}')
        code, out = self.fb('deploy', '--dir', w3); d3 = int(out.rsplit('deploy#', 1)[1].split()[0])
        self.put('css/a.css', 'h1{color:blue}')
        code, out = self.fb('rollback', str(d3)); self.assertNotEqual(code, 0); self.assertIn('冲突', out); self.assertEqual(self.get('css/a.css'), 'h1{color:blue}')

        # 没改动、路径跳出网站目录
        w4 = os.path.join(self.tmp.name, 'w4'); self.fb('fetch', w4, 'index.html')
        self.assertIn('没有改动', self.fb('deploy', '--dir', w4)[1])
        r = urllib.request.Request(self.env['FB_API'] + 'deploy', data=json.dumps({'bundle': 'bm90IGd6aXA='}).encode(), headers={'X-FB-Key': 'agent-key-1234567', 'Content-Type': 'application/json'})
        with self.assertRaises(urllib.error.HTTPError): urllib.request.urlopen(r)


if __name__ == '__main__':
    unittest.main()


class SourceModeTest(DeployFilesTest):
    """有源文件目录（FB_SITE_SOURCE + deploy-map.json）：先改源文件，再复制到网站；生成器产出的页面不能改。"""
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); t = cls.tmp.name
        cls.site = os.path.join(t, 'site'); cls.src = os.path.join(t, 'src')
        for d in (os.path.join(cls.site, 'ui21'), cls.src): os.makedirs(d)
        for rel, txt in (('ui21/ui21.css', 'h1{}'), ('plan.html', '<p>计划</p>'), ('gen.html', '<p>生成器产出</p>')): cls.put(rel, txt)
        for rel, txt in (('ui21.css', 'h1{}'), ('plan.html', '<p>计划</p>'), ('build.py', 'print(1)')):
            with open(os.path.join(cls.src, rel), 'w', encoding='utf-8') as f: f.write(txt)
        with open(os.path.join(cls.src, 'deploy-map.json'), 'w') as f:
            json.dump({'version': 1, 'files': {'ui21.css': 'ui21/ui21.css', 'plan.html': 'plan.html'},
                       'dirs': {'plan/syllabi/': {'to': 'plan/syllabi/', 'match': '*.json', 'recursive': False}},
                       'keep_if_exists': {'course-media.js': {'to': 'course-media.js', 'why': '可能直接在服务器上填过'}},
                       'not_deployed': {'build_*.py': '脚本'}}, f)
        cls.web = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=cls.site)); threading.Thread(target=cls.web.serve_forever, daemon=True).start()
        cls.port = free_port()
        env = dict(os.environ, FB_TEAM_CODE='t', FB_AGENT_CODE='agent-key-1234567', FB_DB=os.path.join(t, 'data', 'fb.db'), FB_PORT=str(cls.port),
                   FB_DEPLOY_CMD=f'{sys.executable} -I {ROOT}/tools/deploy_files.py', FB_SITE_ROOT=cls.site, FB_SITE_SOURCE=cls.src)
        cls.proc = subprocess.Popen([sys.executable, '-I', os.path.join(ROOT, 'server', 'fb_server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            try: urllib.request.urlopen(f'http://127.0.0.1:{cls.port}/api/fb/client/fb-loader.js', timeout=1).close(); break
            except Exception: time.sleep(.1)
        cls.env = dict(os.environ, FB_API=f'http://127.0.0.1:{cls.port}/api/fb/', FB_KEY='agent-key-1234567')
        for k in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'): cls.env.pop(k, None)

    def src_text(self, rel):
        with open(os.path.join(self.src, rel), encoding='utf-8') as f: return f.read()

    def test_deploy_conflict_rollback(self):
        w = os.path.join(self.tmp.name, 's1')
        code, out = self.fb('fetch', w, 'ui21/ui21.css', 'plan.html'); self.assertEqual(code, 0, out); self.assertIn('源文件', out)
        self.edit(w, 'ui21/ui21.css', 'h1{color:red}')
        code, out = self.fb('deploy', '--dir', w, '--item', '3'); self.assertEqual(code, 0, out)
        d1 = int(out.rsplit('deploy#', 1)[1].split()[0])
        self.assertEqual(self.src_text('ui21.css'), 'h1{color:red}'); self.assertEqual(self.get('ui21/ui21.css'), 'h1{color:red}')   # 源文件和网站都改了
        # 生成器产出的页面：不能直接改
        code, out = self.fb('fetch', os.path.join(self.tmp.name, 's2'), 'gen.html'); self.assertNotEqual(code, 0); self.assertIn('生成器', out)
        # 源文件有还没发布的改动 → 冲突
        w3 = os.path.join(self.tmp.name, 's3'); self.fb('fetch', w3, 'plan.html')
        with open(os.path.join(self.src, 'plan.html'), 'w', encoding='utf-8') as f: f.write('<p>计划（别人改了源文件还没发布）</p>')
        self.fb('fetch', w3, 'plan.html'); self.edit(w3, 'plan.html', '<p>计划 v2</p>')
        code, out = self.fb('deploy', '--dir', w3); self.assertNotEqual(code, 0); self.assertIn('不一致', out); self.assertEqual(self.get('plan.html'), '<p>计划</p>')
        # 目录规则：新文件按 match 发布；以网站为准的文件不改
        w4 = os.path.join(self.tmp.name, 's4'); os.makedirs(os.path.join(self.site, 'plan', 'syllabi'), exist_ok=True); os.makedirs(os.path.join(self.src, 'plan', 'syllabi'), exist_ok=True)
        self.fb('fetch', w4, 'plan/syllabi/CS1.json'); self.edit(w4, 'plan/syllabi/CS1.json', '{}')
        code, out = self.fb('deploy', '--dir', w4); self.assertEqual(code, 0, out); self.assertEqual(self.src_text('plan/syllabi/CS1.json'), '{}'); self.assertEqual(self.get('plan/syllabi/CS1.json'), '{}')
        code, out = self.fb('fetch', w4, 'plan/syllabi/x.txt'); self.assertNotEqual(code, 0)
        self.put('course-media.js', 'x'); code, out = self.fb('fetch', w4, 'course-media.js'); self.assertNotEqual(code, 0); self.assertIn('以网站上的为准', out)
        # 回滚：源文件和网站都恢复
        code, out = self.fb('rollback', str(d1)); self.assertEqual(code, 0, out)
        self.assertEqual(self.src_text('ui21.css'), 'h1{}'); self.assertEqual(self.get('ui21/ui21.css'), 'h1{}')
