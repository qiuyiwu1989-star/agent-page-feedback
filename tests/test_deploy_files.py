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
