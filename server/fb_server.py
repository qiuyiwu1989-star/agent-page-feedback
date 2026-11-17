"""agent-page-feedback · 页面批注服务

让团队成员在任意网页上点选位置、写修改意见（自动附位置截图），意见存在服务器上，
多人共享、讨论；AI agent 通过同一套接口读取待办清单、修改后回写「已改好」，形成可复盘的记录。

只用 Python 标准库 + SQLite，单文件，无第三方依赖。

配置（环境变量）：
  FB_TEAM_CODE    必填。团队口令：可以批注、回复、改状态、读取附件
  FB_ADMIN_CODE   选填。管理员口令：另外可以删除任何人的批注、修改任何批注
  FB_DB           数据库路径，默认 ./data/fb.db（附件存在同目录的 files/ 下）
  FB_HOST         监听地址，默认 127.0.0.1（建议放在 nginx 等反向代理后面）
  FB_PORT         端口，默认 18810
  FB_PREFIX       接口路径前缀，默认 /api/fb/（反向代理把这个路径原样转发过来）
  FB_SITE         网站根地址，例如 https://example.com/ ，用于导出清单里的页面链接（选填）
  FB_CLIENT_DIR   前端文件目录，默认与本文件同级的 ../client；设置后在 {PREFIX}client/ 下提供
                  fb-loader.js、feedback.js、fb-media.js、feedback.html（汇总页）等，项目只需加一行 <script>
  FB_NOTIFY_URL   选填。有新批注、新回复、状态变化时 POST 通知（飞书群机器人地址，或任意接收 JSON 的地址）
  FB_NOTIFY_KIND  feishu（默认，飞书卡片）或 json（原样 JSON）

请求头：X-FB-Code（口令）、X-FB-Name（姓名，encodeURIComponent 编码）、X-FB-Cid（浏览器随机 id，用来认「自己的批注」）。
署名为 Claude / Codex 等 agent 名称时，汇总页显示为 AI。

接口（都在 {PREFIX} 下，除 client/ 外都要口令）：
  GET  ping                      检查口令，返回 {ok, role}
  GET  items?page=&status=&all=1 列表（含回复与附件）
  POST items                     新建批注 {page, url, title, selector, quote, rx, ry, px, py, vw, vh, device, kind, priority, body, shot, images}
  POST items/<id>/reply          回复 {body, images}
  POST items/<id>/status         改状态 {status, note}；status: open / doing / question / done / wontfix
  POST items/<id>/edit           改内容（作者或管理员）{body, kind, priority}
  POST items/<id>/delete         删除（作者或管理员；软删除）
  POST items/<id>/shot           补传或替换批注位置截图 {shot}
  GET  files/<id>                读取附件（PNG / JPEG / WebP / GIF，按文件内容判断类型）
  GET  export.md?status=open,doing,question   给 agent 的修改清单（Markdown，按页面分组，含截图地址）
  GET  export.json               全部数据
  GET  decisions?scope=          逐条确认类的决策记录 {decisions: {key: {choice, note, author, updated}}, log}
  POST decisions                 记录一条决策 {scope, id, choice, note}；choice: zhang / sun / other / discuss / ''（撤销）
                                 （choice 的取值可以按项目改，见 CHOICES）
  GET  client/<file>             前端文件（不要口令）
"""
import base64, json, os, re, secrets, sqlite3, threading, time, hmac
from collections import defaultdict, deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('FB_DB', os.path.join(HERE, '..', 'data', 'fb.db'))
PORT = int(os.environ.get('FB_PORT', '18810'))
TEAM = os.environ.get('FB_TEAM_CODE', '')
ADMIN = os.environ.get('FB_ADMIN_CODE', '')
PREFIX = '/' + os.environ.get('FB_PREFIX', '/api/fb/').strip('/') + '/'
SITE = os.environ.get('FB_SITE', '')
HOST = os.environ.get('FB_HOST', '127.0.0.1')
CLIENT = os.path.abspath(os.environ.get('FB_CLIENT_DIR', os.path.join(HERE, '..', 'client')))
NOTIFY_URL = os.environ.get('FB_NOTIFY_URL', '')
NOTIFY_KIND = os.environ.get('FB_NOTIFY_KIND', 'feishu')
CHOICES = ('zhang', 'sun', 'other', 'discuss', '')
STATUSES = {'open': '待处理', 'doing': '处理中', 'done': '已改好', 'wontfix': '不改', 'question': '待讨论'}
KINDS = {'bug': '问题', 'suggest': '建议', 'copy': '文案', 'style': '样式', 'data': '内容有误'}
PRIOS = {'high': '高', 'mid': '中', 'low': '低'}
LOCK = threading.Lock()
FILES = os.path.join(os.path.dirname(DB), 'files')
MAX_SHOT, MAX_IMG, MAX_IMGS = 2_500_000, 4_000_000, 6
def sniff(b):
    if b[:8] == b'\x89PNG\r\n\x1a\n': return 'image/png'
    if b[:3] == b'\xff\xd8\xff': return 'image/jpeg'
    if b[:6] in (b'GIF87a', b'GIF89a'): return 'image/gif'
    if b[:4] == b'RIFF' and b[8:12] == b'WEBP': return 'image/webp'
    return None
def save_files(c, item, reply, kind, urls, author, limit):
    out = []
    for u in (urls or [])[:MAX_IMGS]:
        m = re.match(r'data:image/[a-z+.-]+;base64,(.+)$', str(u), re.S)
        if not m: continue
        try: b = base64.b64decode(m.group(1), validate=False)
        except Exception: continue
        mime = sniff(b)
        if not mime or len(b) > limit: raise ValueError('图片太大或格式不支持（只支持 PNG / JPEG / WebP / GIF）')
        fid = secrets.token_hex(12); os.makedirs(FILES, exist_ok=True)
        with open(os.path.join(FILES, fid), 'wb') as fo: fo.write(b)
        c.execute('INSERT INTO files (id,item,reply,kind,mime,size,author,created) VALUES (?,?,?,?,?,?,?,?)', (fid, item, reply, kind, mime, len(b), author, time.time()))
        out.append(fid)
    return out

def db():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row; return c

def init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY AUTOINCREMENT, page TEXT NOT NULL, title TEXT, selector TEXT, quote TEXT,
          rx REAL, ry REAL, px REAL, py REAL, vw INTEGER, vh INTEGER, device TEXT, kind TEXT, priority TEXT, body TEXT NOT NULL,
          author TEXT, cid TEXT, status TEXT DEFAULT 'open', created REAL, updated REAL, deleted INTEGER DEFAULT 0, url TEXT);
        CREATE TABLE IF NOT EXISTS replies (id INTEGER PRIMARY KEY AUTOINCREMENT, item INTEGER NOT NULL, author TEXT, cid TEXT, body TEXT,
          kind TEXT DEFAULT 'reply', created REAL);
        CREATE INDEX IF NOT EXISTS ix_items_page ON items(page);
        CREATE INDEX IF NOT EXISTS ix_rep_item ON replies(item);
        CREATE TABLE IF NOT EXISTS decisions (scope TEXT, key TEXT, choice TEXT, note TEXT, author TEXT, updated REAL, PRIMARY KEY (scope, key));
        CREATE TABLE IF NOT EXISTS decision_log (id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT, key TEXT, choice TEXT, note TEXT, author TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS files (id TEXT PRIMARY KEY, item INTEGER, reply INTEGER, kind TEXT, mime TEXT, size INTEGER, author TEXT, created REAL);
        CREATE INDEX IF NOT EXISTS ix_files_item ON files(item);''')
        if 'url' not in [r[1] for r in c.execute('PRAGMA table_info(items)')]: c.execute('ALTER TABLE items ADD COLUMN url TEXT')   # 旧库升级

def clip(v, n):
    v = '' if v is None else str(v)
    return v[:n]

def row_item(c, r):
    d = dict(r); fs = [dict(x) for x in c.execute('SELECT id, reply, kind, mime, size, author, created FROM files WHERE item=? ORDER BY created', (r['id'],))]
    d['files'] = [f for f in fs if not f['reply']]
    d['replies'] = [dict(x, files=[f for f in fs if f['reply'] == x['id']]) for x in c.execute('SELECT id, author, body, kind, created FROM replies WHERE item=? ORDER BY id', (r['id'],))]
    d.pop('cid', None); return d

# 简单限流：每个 IP 每分钟最多 120 次写入（确认页有「本组一键确认」）；口令错误每 IP 每 10 分钟最多 10 次
WRITES = defaultdict(deque); FAILS = defaultdict(deque)
def limited(bucket, ip, n, win):
    q = bucket[ip]; now = time.time()
    while q and q[0] < now - win: q.popleft()
    if len(q) >= n: return True
    q.append(now); return False

class H(BaseHTTPRequestHandler):
    server_version = 'agent-page-feedback/1'
    def log_message(self, fmt, *a): pass

    def ip(self): return self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For', '').split(',')[0].strip() or self.client_address[0]

    def send(self, code, obj=None, ctype='application/json; charset=utf-8', raw=None):
        b = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Robots-Tag', 'noindex'); self.end_headers(); self.wfile.write(b)

    def role(self):
        code = self.headers.get('X-FB-Code', '') or (parse_qs(urlparse(self.path).query).get('code') or [''])[0]
        if ADMIN and hmac.compare_digest(code, ADMIN): return 'admin'
        if TEAM and hmac.compare_digest(code, TEAM): return 'team'
        return None

    def who(self):
        return clip(unquote(self.headers.get('X-FB-Name', '')).strip(), 40) or '匿名', clip(self.headers.get('X-FB-Cid', ''), 64)

    def body(self):
        n = int(self.headers.get('Content-Length') or 0)
        if n > 30_000_000: raise ValueError('内容太长（图片总共不超过约 20 MB）')
        return json.loads(self.rfile.read(n) or b'{}')

    def route(self, method):
        u = urlparse(self.path)
        if not u.path.startswith(PREFIX): return self.send(404, {'error': 'not found'})
        p = u.path[len(PREFIX):].strip('/'); qs = {k: v[0] for k, v in parse_qs(u.query).items()}
        if method == 'GET' and (p == 'client' or p.startswith('client/')): return self.static(p[7:] or 'feedback.html')
        ip = self.ip(); role = self.role()
        if role is None:
            if limited(FAILS, ip, 10, 600): return self.send(429, {'error': '尝试次数太多，请 10 分钟后再试'})
            return self.send(401, {'error': '口令不对'})
        name, cid = self.who()
        if method == 'POST' and limited(WRITES, ip, 120, 60): return self.send(429, {'error': '操作太频繁，稍等一下'})
        try:
            with LOCK, db() as c:
                if method == 'GET' and p == 'ping': return self.send(200, {'ok': True, 'role': role})
                if method == 'GET' and p == 'items':
                    sql, a = 'SELECT * FROM items WHERE deleted=0', []
                    if qs.get('page') and not qs.get('all'): sql += ' AND page=?'; a.append(qs['page'])
                    if qs.get('status'): st = qs['status'].split(','); sql += ' AND status IN (%s)' % ','.join('?' * len(st)); a += st
                    rows = [row_item(c, r) for r in c.execute(sql + ' ORDER BY id DESC LIMIT 2000', a)]
                    mine = {r['id'] for r in c.execute('SELECT id FROM items WHERE cid=? AND cid<>""', (cid,))} if cid else set()
                    for r in rows: r['mine'] = r['id'] in mine
                    return self.send(200, {'items': rows, 'role': role})
                if method == 'POST' and p == 'items':
                    d = self.body(); txt = clip(d.get('body'), 4000).strip(); page = clip(d.get('page'), 300).strip()
                    if not txt or not page: return self.send(400, {'error': '请写下意见'})
                    now = time.time()
                    cur = c.execute('INSERT INTO items (page,url,title,selector,quote,rx,ry,px,py,vw,vh,device,kind,priority,body,author,cid,status,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (page, clip(d.get('url'), 1000), clip(d.get('title'), 200), clip(d.get('selector'), 600), clip(d.get('quote'), 400), num(d.get('rx')), num(d.get('ry')), num(d.get('px')), num(d.get('py')),
                         int(num(d.get('vw'))), int(num(d.get('vh'))), clip(d.get('device'), 20), d.get('kind') if d.get('kind') in KINDS else 'suggest',
                         d.get('priority') if d.get('priority') in PRIOS else 'mid', txt, name, cid, 'open', now, now))
                    if d.get('shot'): save_files(c, cur.lastrowid, None, 'shot', [d['shot']], name, MAX_SHOT)
                    save_files(c, cur.lastrowid, None, 'image', d.get('images'), name, MAX_IMG)
                    r = c.execute('SELECT * FROM items WHERE id=?', (cur.lastrowid,)).fetchone(); out = row_item(c, r); out['mine'] = True
                    notify('新批注', out, f'{name}：{txt}')
                    return self.send(200, {'item': out})
                m = re.fullmatch(r'items/(\d+)/(reply|status|edit|delete|shot)', p)
                if method == 'POST' and m:
                    iid, act = int(m.group(1)), m.group(2); r = c.execute('SELECT * FROM items WHERE id=? AND deleted=0', (iid,)).fetchone()
                    if not r: return self.send(404, {'error': '这条批注不存在'})
                    d = self.body(); now = time.time(); own = bool(cid) and r['cid'] == cid
                    if act == 'reply':
                        txt = clip(d.get('body'), 4000).strip()
                        if not txt and not d.get('images'): return self.send(400, {'error': '回复是空的'})
                        rc = c.execute('INSERT INTO replies (item,author,cid,body,kind,created) VALUES (?,?,?,?,?,?)', (iid, name, cid, txt, 'reply', now))
                        save_files(c, iid, rc.lastrowid, 'image', d.get('images'), name, MAX_IMG)
                    elif act == 'shot':
                        old = [x['id'] for x in c.execute("SELECT id FROM files WHERE item=? AND kind='shot'", (iid,))]
                        if not save_files(c, iid, None, 'shot', [d.get('shot')], name, MAX_SHOT): return self.send(400, {'error': '没有收到截图'})
                        for fid in old:
                            c.execute('DELETE FROM files WHERE id=?', (fid,))
                            try: os.remove(os.path.join(FILES, fid))
                            except OSError: pass
                    elif act == 'status':
                        st = d.get('status')
                        if st not in STATUSES: return self.send(400, {'error': '状态不对'})
                        note = clip(d.get('note'), 2000).strip()
                        c.execute('UPDATE items SET status=?, updated=? WHERE id=?', (st, now, iid))
                        c.execute('INSERT INTO replies (item,author,cid,body,kind,created) VALUES (?,?,?,?,?,?)', (iid, name, cid, f'把状态改为「{STATUSES[st]}」' + (f'：{note}' if note else ''), 'status', now))
                    elif act == 'edit':
                        if not (own or role == 'admin'): return self.send(403, {'error': '只能改自己写的批注'})
                        txt = clip(d.get('body'), 4000).strip() or r['body']
                        c.execute('UPDATE items SET body=?, kind=?, priority=?, updated=? WHERE id=?', (txt, d.get('kind') if d.get('kind') in KINDS else r['kind'], d.get('priority') if d.get('priority') in PRIOS else r['priority'], now, iid))
                    elif act == 'delete':
                        if not (own or role == 'admin'): return self.send(403, {'error': '只能删除自己写的批注'})
                        c.execute('UPDATE items SET deleted=1, updated=? WHERE id=?', (now, iid))
                        return self.send(200, {'ok': True})
                    out = row_item(c, c.execute('SELECT * FROM items WHERE id=?', (iid,)).fetchone()); out['mine'] = own
                    if act in ('reply', 'status'): notify('新回复' if act == 'reply' else '状态更新', out, out['replies'][-1]['author'] + '：' + out['replies'][-1]['body'] if out['replies'] else '')
                    return self.send(200, {'item': out})
                if p == 'decisions':
                    scope = (qs.get('scope') or '') if method == 'GET' else ''
                    if method == 'GET':
                        if not re.fullmatch(r'[a-z0-9-]{1,40}', scope): return self.send(400, {'error': 'scope 不对'})
                        ds = {r['key']: {'choice': r['choice'], 'note': r['note'], 'author': r['author'], 'updated': r['updated']} for r in c.execute('SELECT * FROM decisions WHERE scope=?', (scope,))}
                        log = [dict(r) for r in c.execute('SELECT key, choice, note, author, created FROM decision_log WHERE scope=? ORDER BY id DESC LIMIT 500', (scope,))]
                        return self.send(200, {'decisions': ds, 'log': log, 'role': role})
                    d = self.body(); scope = clip(d.get('scope'), 40); key = clip(d.get('id'), 200); ch = d.get('choice') or ''
                    if not re.fullmatch(r'[a-z0-9-]{1,40}', scope) or not key: return self.send(400, {'error': '参数不对'})
                    if ch not in CHOICES: return self.send(400, {'error': '选项不对'})
                    note = clip(d.get('note'), 2000).strip(); now = time.time()
                    if ch or note: c.execute('INSERT OR REPLACE INTO decisions (scope,key,choice,note,author,updated) VALUES (?,?,?,?,?,?)', (scope, key, ch, note, name, now))
                    else: c.execute('DELETE FROM decisions WHERE scope=? AND key=?', (scope, key))
                    c.execute('INSERT INTO decision_log (scope,key,choice,note,author,created) VALUES (?,?,?,?,?,?)', (scope, key, ch, note, name, now))
                    return self.send(200, {'ok': True, 'decision': {'choice': ch, 'note': note, 'author': name, 'updated': now}})
                fm = re.fullmatch(r'files/([0-9a-f]{24})', p)
                if method == 'GET' and fm:
                    r = c.execute('SELECT mime FROM files WHERE id=?', (fm.group(1),)).fetchone()
                    if not r: return self.send(404, {'error': '附件不存在'})
                    b = open(os.path.join(FILES, fm.group(1)), 'rb').read()
                    self.send_response(200); self.send_header('Content-Type', r['mime']); self.send_header('Content-Length', str(len(b)))
                    self.send_header('Cache-Control', 'private, max-age=86400'); self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(b); return
                if method == 'GET' and p == 'export.json':
                    rows = [row_item(c, r) for r in c.execute('SELECT * FROM items WHERE deleted=0 ORDER BY page, id')]
                    return self.send(200, {'generated': time.strftime('%Y-%m-%d %H:%M'), 'statuses': STATUSES, 'kinds': KINDS, 'items': rows})
                if method == 'GET' and p == 'export.md':
                    st = (qs.get('status') or 'open,doing,question').split(',')
                    rows = [row_item(c, r) for r in c.execute('SELECT * FROM items WHERE deleted=0 AND status IN (%s) ORDER BY page, id' % ','.join('?' * len(st)), st)]
                    return self.send(200, raw=export_md(rows, st).encode(), ctype='text/markdown; charset=utf-8')
        except ValueError as e:
            return self.send(400, {'error': str(e)})
        return self.send(404, {'error': 'not found'})

    MIME = {'.js': 'text/javascript; charset=utf-8', '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png'}
    def static(self, rel):
        f = os.path.abspath(os.path.join(CLIENT, rel))
        if not f.startswith(CLIENT + os.sep) or not os.path.isfile(f): return self.send(404, {'error': 'not found'})
        b = open(f, 'rb').read(); self.send_response(200)
        self.send_header('Content-Type', self.MIME.get(os.path.splitext(f)[1], 'application/octet-stream')); self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-cache'); self.send_header('X-Content-Type-Options', 'nosniff'); self.end_headers(); self.wfile.write(b)

    def do_GET(self): self.route('GET')
    def do_POST(self): self.route('POST')

def page_url(r):
    return r.get('url') or (SITE + r['page'] if SITE else r['page'])

def notify(what, item, text):
    """有新批注 / 回复 / 状态变化时发通知（后台线程，失败不影响主流程）。不在消息里放口令。"""
    if not NOTIFY_URL: return
    title = f'{what} · #{item["id"]} {item.get("title") or item["page"]}'
    body = f'{text[:300]}\n页面：{page_url(item)}\n状态：{STATUSES.get(item["status"], item["status"])}'
    payload = ({'msg_type': 'interactive', 'card': {'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': 'blue'}, 'elements': [{'tag': 'markdown', 'content': body}]}}
               if NOTIFY_KIND == 'feishu' else {'event': what, 'title': title, 'text': text, 'item': {k: item.get(k) for k in ('id', 'page', 'url', 'title', 'status', 'kind', 'priority', 'author', 'body')}})
    def go():
        try:
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(NOTIFY_URL, data=json.dumps(payload, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json'}), timeout=10).read()
        except Exception: pass
    threading.Thread(target=go, daemon=True).start()

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def export_md(rows, st):
    ts = lambda t: time.strftime('%m-%d %H:%M', time.localtime(t))
    out = [f'# 网站修改意见清单（{time.strftime("%Y-%m-%d %H:%M")}）', '',
           f'- 范围：状态为 {"、".join(STATUSES.get(s, s) for s in st)} 的批注，共 {len(rows)} 条',
           '- 处理完请把对应条目改为「已改好」并写明改了什么（汇总页 feedback.html，或 POST items/<id>/status）', '']
    by = defaultdict(list)
    for r in rows: by[r['page']].append(r)
    order = {'high': 0, 'mid': 1, 'low': 2}
    for page, rs in by.items():
        out += [f'## {rs[0]["title"] or page}', f'页面：{page_url(rs[0])}', '']
        for r in sorted(rs, key=lambda r: (order.get(r['priority'], 1), r['id'])):
            out.append(f'### #{r["id"]} [{KINDS.get(r["kind"], r["kind"])} · 优先级{PRIOS.get(r["priority"], "中")} · {STATUSES.get(r["status"], r["status"])}] {r["author"]} {ts(r["created"])}')
            if r['quote']: out.append(f'> 位置：{r["quote"]}')
            out.append(f'- 定位：`{r["selector"] or "（页面整体）"}`　屏幕 {r["vw"]}×{r["vh"]}（{r["device"] or "桌面"}）')
            if r.get('files'): out.append('- 附件（读取需口令）：' + '、'.join(f'{"截图" if f["kind"] == "shot" else "图片"} {PREFIX}files/{f["id"]}' for f in r['files']))
            out += ['', r['body'], '']
            for x in r['replies']:
                out.append(f'- {x["author"]}（{ts(x["created"])}）：{x["body"]}')
            if r['replies']: out.append('')
    return '\n'.join(out) + '\n'

if __name__ == '__main__':
    if not TEAM: raise SystemExit('缺少 FB_TEAM_CODE（团队口令），例如：FB_TEAM_CODE=换成你的口令 python3 server/fb_server.py')
    init()
    print(f'agent-page-feedback 在 http://{HOST}:{PORT}{PREFIX} 运行；汇总页 {PREFIX}client/feedback.html', flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
