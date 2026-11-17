"""agent-page-feedback · 页面批注服务

让团队成员在任意网页上点选位置、写修改意见（自动附位置截图），意见存在服务器上，
多人共享、讨论；AI agent 通过同一套接口读取待办清单、修改后回写「已改好」，形成可复盘的记录。

只用 Python 标准库 + SQLite，单文件，无第三方依赖。

配置（环境变量）：
  FB_TEAM_CODE    必填。团队口令：可以批注、回复、改状态、读取附件
  FB_ADMIN_CODE   选填。管理员口令：另外可以删除任何人的批注、修改任何批注
  FB_AGENT_CODE   选填。agent 专用口令：权限同成员，另外可以发布（deploy）、回滚、发汇总（digest）；不能拍板。
                  给定时任务用，只存在云端环境的密钥里
  FB_AGENT_ENROLL 选填。一次性登记码：带着它请求 POST agent-enroll，服务器把请求头 X-FB-Key 里的口令（至少 16 位）记为 agent 口令
                  （只存指纹）。用于口令由云端环境的密钥自动加到请求头、谁也不用看到口令本身的情况。登记码用过一次就失效
  FB_DEPLOY_CMD   选填。发布脚本（固定路径，例如 /home/ubuntu/zjuid-feedback/deploy.sh）。设置后才有 deploy 接口；
                  脚本从环境变量读 FB_DEPLOY_ACTION（deploy / rollback）、FB_DEPLOY_ID、FB_DEPLOY_REF、FB_ROLLBACK_ID，
                  服务器和对象存储的密钥只放在服务器上，agent 拿不到。样例见 deploy/deploy.sh.example
  FB_DEPLOY_TIMEOUT  发布脚本最长运行秒数，默认 900
  FB_DB           数据库路径，默认 ./data/fb.db（附件存在同目录的 files/ 下）
  FB_HOST         监听地址，默认 127.0.0.1（建议放在 nginx 等反向代理后面）
  FB_PORT         端口，默认 18810
  FB_PREFIX       接口路径前缀，默认 /api/fb/（反向代理把这个路径原样转发过来）
  FB_SITE         网站根地址，例如 https://example.com/ ，用于导出清单里的页面链接（选填）
  FB_CLIENT_DIR   前端文件目录，默认与本文件同级的 ../client；设置后在 {PREFIX}client/ 下提供
                  fb-loader.js、feedback.js、fb-media.js、feedback.html（汇总页）等，项目只需加一行 <script>
  FB_NOTIFY_URL   选填。有新批注、新回复、负责人的决定、agent 的方案和交付报告时 POST 通知（飞书群机器人地址，或任意接收 JSON 的地址）
  FB_NOTIFY_KIND  feishu（默认，飞书卡片）或 json（原样 JSON）
  FB_SUMMARY_URL  选填。通知卡片里「任务汇总」按钮的地址，默认 FB_SITE 下的 {PREFIX}client/feedback.html
  FB_AI_NAMES     选填。哪些署名算 agent（逗号分隔，默认 Claude,Codex）。agent 的普通回复和改状态不推送，方案和交付报告照常推送

工作流（v2，工单式）：
  待处理 open → agent 分诊：
    明确的 → 负责人点「可以做」→ 可以做 approved → agent 修改 → 交付报告 → 待验收 review → 负责人「通过」→ 已完成 done
                                                                           ↳ 负责人「再改」/「改回来」→ 可以做 approved
    不明确的 → agent 出方案（A / B）→ 待决定 question → 负责人「选方案」→ 可以做 approved
  负责人 = 用管理员口令登录的人。只有管理员口令能做决定（decide），这是 agent 的执行信号。

请求头：X-FB-Code 或 X-FB-Key（口令，两个名字等价）、X-FB-Name（姓名，encodeURIComponent 编码）、X-FB-Cid（浏览器随机 id，用来认「自己的批注」）。
署名为 Claude / Codex 等 agent 名称时，汇总页显示为 AI。

接口（都在 {PREFIX} 下，除 client/ 外都要口令）：
  GET  ping                      检查口令，返回 {ok, role}
  GET  items?page=&status=&all=1 列表（含回复与附件）
  POST items                     新建批注 {page, url, title, selector, quote, rx, ry, px, py, vw, vh, device, kind, priority, body, shot, images, marks}
                                 marks：[{t: region, x, y, w, h}]（页面上框选的区域，页面像素）和 [{t: pen / rect, x, y, w, h}]（在截图上画的，按截图宽高的比例）
  POST items/<id>/reply          回复 {body, images}
  POST items/<id>/status         改状态 {status, note}；status 见 STATUSES
  POST items/<id>/decide         负责人的决定（只限管理员口令）{action, option, note}
                                 action: approve 可以做 / choose 选方案 / accept 通过 / rework 再改 / revert 改回来 / wontfix 不改 / reopen 重新打开
  POST items/<id>/propose        agent 出方案 {summary, options: [{key, title, desc, link}], images} → 状态变为「待决定」
  POST items/<id>/report         agent 交付报告 {summary, why, changes: [..], links: [{name, url}], rollback, before, after, images} → 状态变为「待验收」
  GET  prefs / POST prefs        负责人偏好手册 {text, item}；POST prefs/<id>/off 停用一条（管理员）
  GET  queue.json                给 agent 的任务队列：偏好手册 + 可以执行 / 待分诊 / 等负责人 / 待验收
  GET  queue.json 里每条待分诊任务带 triaged（agent 已回复过、之后没有人再说话），summary 里是本轮要做的数量
  POST digest                    agent 发汇总到负责人的飞书群（只限 agent / 管理员口令）{title, markdown, buttons: [{text, url}]}；
                                 飞书地址只在服务器上。汇总也存进数据库（digests），GET digests 查看
  POST deploy                    发布（只限 agent / 管理员口令）{ref, items: [编号], note, bundle} → {deploy: {id, status: running}}；同一时间只跑一个
                                 bundle：按文件发布的发布包（tar.gz 的 base64，不超过 20 MB），传给脚本 FB_DEPLOY_BUNDLE，见 tools/deploy_files.py
  GET  deploys / deploys/<id>    发布登记（编号、谁、哪些任务、版本、状态、输出末尾）
  POST deploys/<id>/rollback     把整个网站恢复到第 <id> 次发布之前（只限 agent / 管理员口令；会新登记一次 rollback）
  GET  stats                     统计：各状态数量、一次通过率、平均交付用时、返工次数
  POST items/<id>/edit           改内容（作者或管理员）{body, kind, priority}
  POST items/<id>/delete         删除（作者或管理员；软删除）
  POST items/<id>/shot           补传或替换批注位置截图 {shot}
  GET  files/<id>                读取附件（PNG / JPEG / WebP / GIF，按文件内容判断类型）
  GET  export.md                 给 agent 的工作清单（Markdown）：偏好手册 → 可以执行 → 待分诊 → 等负责人；?status= 可指定状态
  GET  export.json               全部数据
  GET  decisions?scope=          逐条确认类的决策记录 {decisions: {key: {choice, note, author, updated}}, log}
  POST decisions                 记录一条决策 {scope, id, choice, note}；choice: zhang / sun / other / discuss / ''（撤销）
                                 （choice 的取值可以按项目改，见 CHOICES）
  GET  client/<file>             前端文件（不要口令）
"""
import base64, hashlib, json, os, re, secrets, sqlite3, threading, time, hmac
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
STATUSES = {'open': '待处理', 'question': '待决定', 'approved': '可以做', 'doing': '处理中', 'review': '待验收', 'done': '已完成', 'wontfix': '不改'}
ACTIONS = {'approve': ('可以做', 'approved'), 'choose': ('选方案', 'approved'), 'accept': ('通过', 'done'), 'rework': ('再改', 'approved'),
           'revert': ('改回来', 'approved'), 'wontfix': ('不改', 'wontfix'), 'reopen': ('重新打开', 'open')}
AGENT = os.environ.get('FB_AGENT_CODE', '')
ENROLL = os.environ.get('FB_AGENT_ENROLL', '')
AGENT_HASHES = set()   # 登记过的 agent 口令指纹（sha256）
DEPLOY_CMD = os.environ.get('FB_DEPLOY_CMD', '').strip()
DEPLOY_TIMEOUT = int(os.environ.get('FB_DEPLOY_TIMEOUT', '900'))
AI_NAMES = {x.strip().lower() for x in os.environ.get('FB_AI_NAMES', 'Claude,Codex').split(',') if x.strip()}
SUMMARY_URL = os.environ.get('FB_SUMMARY_URL', '')
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
        c.executescript('''CREATE TABLE IF NOT EXISTS digests (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, markdown TEXT, buttons TEXT, author TEXT, sent INTEGER, created REAL);
        CREATE TABLE IF NOT EXISTS deploys (id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT, ref TEXT, items TEXT, note TEXT, rollback_of INTEGER,
          author TEXT, status TEXT, output TEXT, created REAL, finished REAL);''')
        c.execute('CREATE TABLE IF NOT EXISTS agent_keys (hash TEXT PRIMARY KEY, enroll TEXT, created REAL)')
        AGENT_HASHES.update(r[0] for r in c.execute('SELECT hash FROM agent_keys'))
        # 服务重启时还在跑的发布，结果不明
        c.execute("UPDATE deploys SET status='unknown', finished=? WHERE status='running'", (time.time(),))
        c.executescript('''CREATE TABLE IF NOT EXISTS prefs (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, item INTEGER, author TEXT, created REAL, active INTEGER DEFAULT 1);''')
        # 旧库升级：缺的列补上（v1 → v2）
        for t, col, typ in (('items', 'url', 'TEXT'), ('items', 'arole', 'TEXT'), ('items', 'signal', 'TEXT'), ('items', 'signal_note', 'TEXT'),
                            ('items', 'signal_by', 'TEXT'), ('items', 'signal_at', 'REAL'), ('replies', 'role', 'TEXT'), ('replies', 'meta', 'TEXT'), ('items', 'marks', 'TEXT')):
            if col not in [r[1] for r in c.execute(f'PRAGMA table_info({t})')]: c.execute(f'ALTER TABLE {t} ADD COLUMN {col} {typ}')

def clip(v, n):
    v = '' if v is None else str(v)
    return v[:n]

def row_item(c, r):
    d = dict(r); fs = [dict(x) for x in c.execute('SELECT id, reply, kind, mime, size, author, created FROM files WHERE item=? ORDER BY created', (r['id'],))]
    d['files'] = [f for f in fs if not f['reply']]
    d['replies'] = []
    for x in c.execute('SELECT id, author, body, kind, created, role, meta FROM replies WHERE item=? ORDER BY id', (r['id'],)):
        x = dict(x); x['files'] = [f for f in fs if f['reply'] == x['id']]
        try: x['meta'] = json.loads(x['meta']) if x['meta'] else None
        except ValueError: x['meta'] = None
        x['ai'] = is_ai(x['author']); d['replies'].append(x)
    d['ai'] = is_ai(d.get('author'))
    try: d['marks'] = json.loads(d['marks']) if d.get('marks') else []
    except ValueError: d['marks'] = []
    d.pop('cid', None); return d

def clean_marks(v):
    """框选区域 / 截图上的圈画：只留类型和外框数字。"""
    out = []
    for m in (v if isinstance(v, list) else [])[:30]:
        if isinstance(m, dict) and m.get('t') in ('region', 'pen', 'rect'):
            out.append({'t': m['t'], **{k: round(num(m.get(k)), 3) for k in ('x', 'y', 'w', 'h')}})
    return json.dumps(out) if out else None

def is_ai(name): return (name or '').strip().lower() in AI_NAMES

def add_reply(c, iid, name, cid, role, body, kind, meta=None, now=None):
    return c.execute('INSERT INTO replies (item,author,cid,body,kind,created,role,meta) VALUES (?,?,?,?,?,?,?,?)',
                     (iid, name, cid, body, kind, now or time.time(), role, json.dumps(meta, ensure_ascii=False) if meta else None)).lastrowid

# 简单限流：每个 IP 每分钟最多 120 次写入（确认页有「本组一键确认」）；口令错误每 IP 每 10 分钟最多 10 次
WRITES = defaultdict(deque); FAILS = defaultdict(deque)
def limited(bucket, ip, n, win):
    q = bucket[ip]; now = time.time()
    while q and q[0] < now - win: q.popleft()
    if len(q) >= n: return True
    q.append(now); return False

class H(BaseHTTPRequestHandler):
    server_version = 'agent-page-feedback/2'
    def log_message(self, fmt, *a): pass

    def ip(self): return self.headers.get('X-Real-IP') or self.headers.get('X-Forwarded-For', '').split(',')[0].strip() or self.client_address[0]

    def send(self, code, obj=None, ctype='application/json; charset=utf-8', raw=None):
        b = raw if raw is not None else json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Robots-Tag', 'noindex'); self.end_headers(); self.wfile.write(b)

    def role(self):
        code = self.headers.get('X-FB-Code', '') or self.headers.get('X-FB-Key', '') or (parse_qs(urlparse(self.path).query).get('code') or [''])[0]
        if ADMIN and hmac.compare_digest(code, ADMIN): return 'admin'
        if AGENT and hmac.compare_digest(code, AGENT): return 'agent'
        if code and AGENT_HASHES and sha(code) in AGENT_HASHES: return 'agent'
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
        ip = self.ip()
        if method == 'POST' and p == 'agent-enroll': return self.enroll(ip)
        role = self.role()
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
                    cur = c.execute('INSERT INTO items (page,url,title,selector,quote,rx,ry,px,py,vw,vh,device,kind,priority,body,author,cid,status,created,updated,arole,marks) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (page, clip(d.get('url'), 1000), clip(d.get('title'), 200), clip(d.get('selector'), 600), clip(d.get('quote'), 400), num(d.get('rx')), num(d.get('ry')), num(d.get('px')), num(d.get('py')),
                         int(num(d.get('vw'))), int(num(d.get('vh'))), clip(d.get('device'), 20), d.get('kind') if d.get('kind') in KINDS else 'suggest',
                         d.get('priority') if d.get('priority') in PRIOS else 'mid', txt, name, cid, 'open', now, now, role, clean_marks(d.get('marks'))))
                    if d.get('shot'): save_files(c, cur.lastrowid, None, 'shot', [d['shot']], name, MAX_SHOT)
                    save_files(c, cur.lastrowid, None, 'image', d.get('images'), name, MAX_IMG)
                    r = c.execute('SELECT * FROM items WHERE id=?', (cur.lastrowid,)).fetchone(); out = row_item(c, r); out['mine'] = True
                    notify('新批注', out, name, txt, quote=d.get('quote'))
                    return self.send(200, {'item': out})
                m = re.fullmatch(r'items/(\d+)/(reply|status|edit|delete|shot|decide|propose|report)', p)
                if method == 'POST' and m:
                    iid, act = int(m.group(1)), m.group(2); r = c.execute('SELECT * FROM items WHERE id=? AND deleted=0', (iid,)).fetchone()
                    if not r: return self.send(404, {'error': '这条批注不存在'})
                    d = self.body(); now = time.time(); own = bool(cid) and r['cid'] == cid
                    if act == 'reply':
                        txt = clip(d.get('body'), 4000).strip()
                        if not txt and not d.get('images'): return self.send(400, {'error': '回复是空的'})
                        rid = add_reply(c, iid, name, cid, role, txt, 'reply', now=now)
                        save_files(c, iid, rid, 'image', d.get('images'), name, MAX_IMG)
                    elif act == 'decide':
                        if role != 'admin': return self.send(403, {'error': '只有负责人（管理员口令）可以做决定'})
                        a = d.get('action')
                        if a not in ACTIONS: return self.send(400, {'error': '决定不对'})
                        label, st = ACTIONS[a]; opt = clip(d.get('option'), 40).strip(); note = clip(d.get('note'), 2000).strip()
                        if a == 'choose' and not opt: return self.send(400, {'error': '请选一个方案'})
                        if a == 'rework' and not note: return self.send(400, {'error': '请写一下要怎么再改'})
                        text = label + (f' {opt}' if opt else '') + (f'：{note}' if note else '')
                        add_reply(c, iid, name, cid, role, text, 'decision', {'action': a, 'option': opt, 'note': note}, now)
                        c.execute('UPDATE items SET status=?, signal=?, signal_note=?, signal_by=?, signal_at=?, updated=? WHERE id=?', (st, a, (opt + ' ' + note).strip(), name, now, now, iid))
                    elif act == 'propose':
                        opts = [{'key': clip(o.get('key'), 8), 'title': clip(o.get('title'), 120), 'desc': clip(o.get('desc'), 1000), 'link': clip(o.get('link'), 500)} for o in (d.get('options') or [])[:6] if isinstance(o, dict)]
                        summary = clip(d.get('summary'), 4000).strip()
                        if not summary and not opts: return self.send(400, {'error': '方案是空的'})
                        rid = add_reply(c, iid, name, cid, role, summary, 'proposal', {'options': opts}, now)
                        save_files(c, iid, rid, 'image', d.get('images'), name, MAX_IMG)
                        c.execute('UPDATE items SET status=?, updated=? WHERE id=?', ('question', now, iid))
                    elif act == 'report':
                        summary = clip(d.get('summary'), 4000).strip()
                        if not summary: return self.send(400, {'error': '请写明改了什么'})
                        meta = {'why': clip(d.get('why'), 2000), 'changes': [clip(x, 400) for x in (d.get('changes') or [])[:20]],
                                'links': [{'name': clip(l.get('name'), 80), 'url': clip(l.get('url'), 500)} for l in (d.get('links') or [])[:8] if isinstance(l, dict)],
                                'rollback': clip(d.get('rollback'), 200)}
                        rid = add_reply(c, iid, name, cid, role, summary, 'report', meta, now)
                        for k in ('before', 'after'):
                            if d.get(k): save_files(c, iid, rid, k, [d[k]], name, MAX_SHOT)
                        save_files(c, iid, rid, 'image', d.get('images'), name, MAX_IMG)
                        c.execute('UPDATE items SET status=?, updated=? WHERE id=?', ('review', now, iid))
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
                        add_reply(c, iid, name, cid, role, f'把状态改为「{STATUSES[st]}」' + (f'：{note}' if note else ''), 'status', now=now)
                    elif act == 'edit':
                        if not (own or role == 'admin'): return self.send(403, {'error': '只能改自己写的批注'})
                        txt = clip(d.get('body'), 4000).strip() or r['body']
                        c.execute('UPDATE items SET body=?, kind=?, priority=?, updated=? WHERE id=?', (txt, d.get('kind') if d.get('kind') in KINDS else r['kind'], d.get('priority') if d.get('priority') in PRIOS else r['priority'], now, iid))
                    elif act == 'delete':
                        if not (own or role == 'admin'): return self.send(403, {'error': '只能删除自己写的批注'})
                        c.execute('UPDATE items SET deleted=1, updated=? WHERE id=?', (now, iid))
                        return self.send(200, {'ok': True})
                    out = row_item(c, c.execute('SELECT * FROM items WHERE id=?', (iid,)).fetchone()); out['mine'] = own
                    last = out['replies'][-1] if out['replies'] else None
                    if last and act in ('reply', 'status', 'decide', 'propose', 'report'):
                        what = {'reply': '新回复', 'status': '状态改为「' + STATUSES.get(out['status'], '') + '」', 'decide': '负责人的决定', 'propose': '方案待你决定', 'report': '交付报告 · 待你验收'}[act]
                        notify(what, out, name, last['body'], force=act in ('propose', 'report'))
                    return self.send(200, {'item': out})
                if p == 'prefs':
                    if method == 'GET': return self.send(200, {'prefs': [dict(r) for r in c.execute('SELECT * FROM prefs WHERE active=1 ORDER BY id')]})
                    d = self.body(); txt = clip(d.get('text'), 500).strip()
                    if not txt: return self.send(400, {'error': '内容是空的'})
                    pid = c.execute('INSERT INTO prefs (text,item,author,created) VALUES (?,?,?,?)', (txt, int(num(d.get('item'))) or None, name, time.time())).lastrowid
                    return self.send(200, {'pref': dict(c.execute('SELECT * FROM prefs WHERE id=?', (pid,)).fetchone())})
                pm = re.fullmatch(r'prefs/(\d+)/off', p)
                if method == 'POST' and pm:
                    if role != 'admin': return self.send(403, {'error': '只有负责人可以停用'})
                    c.execute('UPDATE prefs SET active=0 WHERE id=?', (int(pm.group(1)),)); return self.send(200, {'ok': True})
                if method == 'GET' and p == 'queue.json':
                    rows = [row_item(c, r) for r in c.execute("SELECT * FROM items WHERE deleted=0 AND status NOT IN ('done','wontfix') ORDER BY id")]
                    g = lambda *ss: [r for r in rows if r['status'] in ss]
                    for r in rows: r['triaged'] = triaged(r)
                    act, tri = g('approved', 'doing'), g('open')
                    return self.send(200, {'generated': time.strftime('%Y-%m-%d %H:%M'), 'prefs': [r['text'] for r in c.execute('SELECT text FROM prefs WHERE active=1 ORDER BY id')],
                                           'summary': {'act': len(act), 'triage_new': sum(1 for r in tri if not r['triaged']), 'waiting': len(g('question')), 'review': len(g('review'))},
                                           'act': act, 'triage': tri, 'waiting': g('question'), 'review': g('review')})
                if p in ('digest', 'digests', 'deploy') or p.startswith('deploys'):
                    return self.ops(c, method, p, role, name)
                if method == 'GET' and p == 'stats':
                    return self.send(200, stats(c))
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
                    st = (qs.get('status') or 'approved,doing,open,question,review').split(',')
                    rows = [row_item(c, r) for r in c.execute('SELECT * FROM items WHERE deleted=0 AND status IN (%s) ORDER BY page, id' % ','.join('?' * len(st)), st)]
                    prefs = [r['text'] for r in c.execute('SELECT text FROM prefs WHERE active=1 ORDER BY id')]
                    return self.send(200, raw=export_md(rows, st, prefs).encode(), ctype='text/markdown; charset=utf-8')
        except ValueError as e:
            return self.send(400, {'error': str(e)})
        return self.send(404, {'error': 'not found'})

    def enroll(self, ip):
        """一次性登记 agent 口令：X-FB-Enroll 是服务器上的登记码，X-FB-Key 是要登记的口令（只存指纹）。"""
        tok, key = self.headers.get('X-FB-Enroll', ''), self.headers.get('X-FB-Key', '')
        if limited(FAILS, ip, 10, 600): return self.send(429, {'error': '尝试次数太多，请 10 分钟后再试'})
        if not ENROLL or not hmac.compare_digest(tok, ENROLL): return self.send(401, {'error': '登记码不对'})
        if len(key) < 16: return self.send(400, {'error': 'agent 口令至少 16 位'})
        with LOCK, db() as c:
            if c.execute('SELECT 1 FROM agent_keys WHERE enroll=?', (sha(ENROLL),)).fetchone(): return self.send(409, {'error': '这个登记码已经用过了'})
            c.execute('INSERT OR REPLACE INTO agent_keys (hash, enroll, created) VALUES (?,?,?)', (sha(key), sha(ENROLL), time.time()))
        AGENT_HASHES.add(sha(key)); return self.send(200, {'ok': True, 'role': 'agent'})

    def ops(self, c, method, p, role, name):
        """agent 的汇总推送与发布登记。只限 agent / 管理员口令。"""
        if method == 'GET' and p in ('digest', 'digests'):
            return self.send(200, {'digests': [dict(r) for r in c.execute('SELECT * FROM digests ORDER BY id DESC LIMIT 200')]})
        if method == 'GET' and p == 'deploys':
            return self.send(200, {'deploys': [dict(r) for r in c.execute('SELECT * FROM deploys ORDER BY id DESC LIMIT 200')], 'enabled': bool(DEPLOY_CMD)})
        dm = re.fullmatch(r'deploys/(\d+)', p)
        if method == 'GET' and dm:
            r = c.execute('SELECT * FROM deploys WHERE id=?', (int(dm.group(1)),)).fetchone()
            return self.send(200, {'deploy': dict(r)}) if r else self.send(404, {'error': '没有这次发布'})
        if method != 'POST': return self.send(404, {'error': 'not found'})
        if role not in ('agent', 'admin'): return self.send(403, {'error': '只有 agent 口令或管理员口令可以发汇总、发布'})
        d = self.body(); now = time.time()
        if p == 'digest':
            title = clip(d.get('title'), 120).strip(); md = clip(d.get('markdown'), 12000).strip()
            if not title or not md: return self.send(400, {'error': '汇总要有标题和内容'})
            btns = [{'text': clip(b.get('text'), 30), 'url': clip(b.get('url'), 500)} for b in (d.get('buttons') or [])[:4]
                    if isinstance(b, dict) and str(b.get('url', '')).startswith(('https://', 'http://'))]
            sent = bool(NOTIFY_URL)
            did = c.execute('INSERT INTO digests (title,markdown,buttons,author,sent,created) VALUES (?,?,?,?,?,?)', (title, md, json.dumps(btns, ensure_ascii=False), name, int(sent), now)).lastrowid
            if sent: push(card(title, md, [(b['text'], b['url']) for b in btns], 'blue'))
            return self.send(200, {'ok': True, 'id': did, 'sent': sent})
        if p == 'deploy' or re.fullmatch(r'deploys/\d+/rollback', p):
            if not DEPLOY_CMD: return self.send(400, {'error': '服务器上没有设置发布脚本（FB_DEPLOY_CMD）'})
            if c.execute("SELECT 1 FROM deploys WHERE status='running'").fetchone(): return self.send(409, {'error': '上一次发布还在进行，稍后再试'})
            note = clip(d.get('note'), 1000).strip()
            if p == 'deploy':
                ref = clip(d.get('ref'), 100).strip()
                if ref and not re.fullmatch(r'[A-Za-z0-9._/-]+', ref): return self.send(400, {'error': '版本号只能包含字母、数字和 . _ / -'})
                items = [int(num(x)) for x in (d.get('items') or [])[:50] if int(num(x)) > 0]
                action, rb, bundle = 'deploy', None, None
                if d.get('bundle'):
                    try: bundle = base64.b64decode(str(d['bundle']), validate=True)
                    except ValueError: return self.send(400, {'error': '发布包不是 base64'})
                    if len(bundle) > 20_000_000 or bundle[:2] != b'\x1f\x8b': return self.send(400, {'error': '发布包要是 tar.gz，不超过 20 MB'})
            else:
                rb = int(p.split('/')[1]); old = c.execute("SELECT * FROM deploys WHERE id=? AND action='deploy'", (rb,)).fetchone()
                if not old: return self.send(404, {'error': '没有这次发布'})
                action, ref, items, bundle = 'rollback', '', json.loads(old['items'] or '[]'), None
            did = c.execute('INSERT INTO deploys (action,ref,items,note,rollback_of,author,status,output,created) VALUES (?,?,?,?,?,?,?,?,?)',
                            (action, ref, json.dumps(items), note, rb, name, 'running', '', now)).lastrowid
            path = ''
            if bundle:
                bd = os.path.join(os.path.dirname(DB), 'deploy-bundles'); os.makedirs(bd, exist_ok=True); path = os.path.join(bd, f'{did}.tar.gz')
                with open(path, 'wb') as fo: fo.write(bundle)
            threading.Thread(target=run_deploy, args=(did, action, ref, rb, name, path), daemon=True).start()
            return self.send(200, {'deploy': dict(c.execute('SELECT * FROM deploys WHERE id=?', (did,)).fetchone())})
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

def notify(what, item, who, text, quote=None, force=False):
    """推送通知（后台线程，失败不影响主流程）。agent 的普通回复和改状态不推送；方案和交付报告（force）照常推送。消息里不放口令。"""
    if not NOTIFY_URL or (is_ai(who) and not force): return
    page = page_url(item); sep = '&' if '?' in page.split('#')[0] else '?'
    deep = page.split('#')[0] + sep + f'fb=1&fbid={item["id"]}' + ('#' + page.split('#', 1)[1] if '#' in page else '')
    summary = SUMMARY_URL or ((SITE.rstrip('/') + PREFIX + 'client/feedback.html') if SITE else '')
    title = f'{what} · #{item["id"]} {item.get("title") or item["page"]}'
    lines = [f'**{who}**：{(text or "（无说明）")[:600]}']
    if quote: lines.append(f'位置：「{clip(quote, 60)}」')
    lines += [f'页面：{page}', f'状态：{STATUSES.get(item["status"], item["status"])}']
    if item['status'] in ('question', 'review'): lines.append('**需要负责人：** ' + ('选一个方案，或回复意见' if item['status'] == 'question' else '看交付报告，点「通过」或「再改」'))
    btns = [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': '在页面上看'}, 'url': deep, 'type': 'primary'}]
    if summary: btns.append({'tag': 'button', 'text': {'tag': 'plain_text', 'content': '任务汇总'}, 'url': summary + f'#item-{item["id"]}', 'type': 'default'})
    tpl = {'新批注': 'orange', '交付报告 · 待你验收': 'green', '方案待你决定': 'purple', '负责人的决定': 'red'}.get(what, 'blue')
    payload = ({'msg_type': 'interactive', 'card': {'config': {'wide_screen_mode': True}, 'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': tpl},
                'elements': [{'tag': 'markdown', 'content': '\n'.join(lines)}] + ([{'tag': 'action', 'actions': btns}] if deep.startswith('http') else [])}}
               if NOTIFY_KIND == 'feishu' else {'event': what, 'title': title, 'who': who, 'text': text, 'item': {k: item.get(k) for k in ('id', 'page', 'url', 'title', 'status', 'kind', 'priority', 'author', 'body')}})
    push(payload)

def card(title, md, buttons=(), tpl='blue'):
    """汇总、发布结果等通知的卡片（飞书格式；FB_NOTIFY_KIND=json 时为原样 JSON）。"""
    if NOTIFY_KIND != 'feishu': return {'event': 'digest', 'title': title, 'text': md, 'buttons': [{'text': t, 'url': u} for t, u in buttons]}
    acts = [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': t}, 'url': u, 'type': 'primary' if i == 0 else 'default'} for i, (t, u) in enumerate(buttons)]
    return {'msg_type': 'interactive', 'card': {'config': {'wide_screen_mode': True}, 'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': tpl},
            'elements': [{'tag': 'markdown', 'content': md}] + ([{'tag': 'action', 'actions': acts}] if acts else [])}}

def push(payload):
    """后台发送，失败不影响主流程。"""
    if not NOTIFY_URL: return
    def go():
        try:
            import urllib.request
            urllib.request.urlopen(urllib.request.Request(NOTIFY_URL, data=json.dumps(payload, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json'}), timeout=10).read()
        except Exception: pass
    threading.Thread(target=go, daemon=True).start()

def sha(v): return hashlib.sha256(v.encode()).hexdigest()

def triaged(item):
    """待分诊的任务里，agent 已经回复过、之后没有人再说话的，算已分诊（等负责人点「可以做」），定时任务不用再看。"""
    last_ai = max([x['created'] for x in item['replies'] if x['ai']] or [0])
    last_human = max([item['created']] + [x['created'] for x in item['replies'] if not x['ai']])
    return last_ai > last_human

def run_deploy(did, action, ref, rb, who, bundle=''):
    """在后台运行发布脚本。脚本是服务器上固定的那一个，参数只通过环境变量传，不经过 shell。"""
    import shlex, subprocess
    env = dict(os.environ, FB_DEPLOY_ACTION=action, FB_DEPLOY_ID=str(did), FB_DEPLOY_REF=ref, FB_ROLLBACK_ID=str(rb or ''), FB_DEPLOY_BUNDLE=bundle, FB_DB=DB)
    for k in ('FB_TEAM_CODE', 'FB_ADMIN_CODE', 'FB_AGENT_CODE', 'FB_NOTIFY_URL'): env.pop(k, None)   # 脚本用不到口令
    try:
        p = subprocess.run(shlex.split(DEPLOY_CMD), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=DEPLOY_TIMEOUT)
        status, out = ('ok' if p.returncode == 0 else 'failed'), p.stdout.decode('utf-8', 'replace')
    except subprocess.TimeoutExpired as e:
        status, out = 'failed', (e.stdout or b'').decode('utf-8', 'replace') + f'\n超时（{DEPLOY_TIMEOUT} 秒）'
    except OSError as e:
        status, out = 'failed', f'脚本没能运行：{e}'
    with LOCK, db() as c:
        c.execute('UPDATE deploys SET status=?, output=?, finished=? WHERE id=?', (status, out[-6000:], time.time(), did))
    if status != 'ok':
        what = '发布' if action == 'deploy' else f'回滚到第 {rb} 次发布之前'
        if '冲突' in out[-3000:]: what += '（冲突，没有改动）'
        push(card(f'{what}失败 · 第 {did} 次', f'**{who}** 发起的{what}没有成功，正式站可能没有变化，也可能只改了一部分。\n\n输出末尾：\n```\n{out[-800:]}\n```', tpl='red'))

def stats(c):
    """一次通过率 = 通过的任务里，从没被「再改 / 改回来」过的比例；交付用时 = 提出 → 负责人「通过」。"""
    cnt = {k: 0 for k in STATUSES}
    for r in c.execute('SELECT status, COUNT(*) n FROM items WHERE deleted=0 GROUP BY status'): cnt[r['status']] = r['n']
    acc = c.execute("""SELECT i.id, i.created, MAX(r.created) t FROM items i JOIN replies r ON r.item=i.id
                       WHERE i.deleted=0 AND r.kind='decision' AND r.meta LIKE '%"action": "accept"%' GROUP BY i.id""").fetchall()
    rework = {r['item'] for r in c.execute("""SELECT DISTINCT item FROM replies WHERE kind='decision' AND (meta LIKE '%"action": "rework"%' OR meta LIKE '%"action": "revert"%')""")}
    nrw = c.execute("""SELECT COUNT(*) FROM replies WHERE kind='decision' AND (meta LIKE '%"action": "rework"%' OR meta LIKE '%"action": "revert"%')""").fetchone()[0]
    hours = [(r['t'] - r['created']) / 3600 for r in acc]
    return {'count': cnt, 'total': sum(cnt.values()), 'accepted': len(acc), 'first_pass': round(sum(1 for r in acc if r['id'] not in rework) / len(acc), 3) if acc else None,
            'avg_hours': round(sum(hours) / len(hours), 1) if hours else None, 'reworks': nrw,
            'reports': c.execute("SELECT COUNT(*) FROM replies WHERE kind='report'").fetchone()[0], 'prefs': c.execute('SELECT COUNT(*) FROM prefs WHERE active=1').fetchone()[0]}

def num(v):
    try: return float(v)
    except (TypeError, ValueError): return 0.0

def export_md(rows, st, prefs=()):
    ts = lambda t: time.strftime('%m-%d %H:%M', time.localtime(t))
    out = [f'# 工作清单（{time.strftime("%Y-%m-%d %H:%M")}）', '',
           '- 规则：只执行「可以执行」里的任务（负责人已确认）；「待分诊」的先判断——明确的写清打算怎么改并请负责人点「可以做」，不明确的出方案（POST items/<id>/propose）；「等负责人」的不要动。',
           '- 改完用 POST items/<id>/report 交付报告（改了什么、为什么、改前 / 改后截图、链接、回滚编号），状态会变成「待验收」。', '']
    if prefs: out += ['## 负责人偏好手册（动手前先读）', ''] + [f'- {t}' for t in prefs] + ['']
    groups = [('可以执行（负责人已确认）', ('approved', 'doing')), ('待分诊（新提出的）', ('open',)), ('等负责人（不要动）', ('question', 'review'))]
    order = {'high': 0, 'mid': 1, 'low': 2}
    for gname, sts in groups:
        rs = [r for r in rows if r['status'] in sts]
        if not rs: continue
        out += [f'## {gname} · {len(rs)} 条', '']
        for r in sorted(rs, key=lambda r: (order.get(r['priority'], 1), r['id'])):
            out.append(f'### #{r["id"]} [{STATUSES.get(r["status"], r["status"])} · {KINDS.get(r["kind"], r["kind"])} · 优先级{PRIOS.get(r["priority"], "中")}] {r["author"]}{"（负责人）" if r.get("arole") == "admin" else ""} {ts(r["created"])}')
            if r.get('signal'): out.append(f'- **负责人信号：{ACTIONS.get(r["signal"], (r["signal"],))[0]}**' + (f'（{r["signal_note"]}）' if r.get('signal_note') else '') + f' · {r.get("signal_by") or ""}')
            out.append(f'- 页面：{page_url(r)}')
            if r['quote']: out.append(f'- 位置：「{r["quote"]}」')
            out.append(f'- 定位：`{r["selector"] or "（页面整体）"}`　屏幕 {r["vw"]}×{r["vh"]}（{r["device"] or "桌面"}）')
            mk = r.get('marks') or []; reg = [m for m in mk if m['t'] == 'region']; drawn = [m for m in mk if m['t'] != 'region']
            if reg: out.append('- 框选区域（页面像素，左上角 x,y 和宽高）：' + '；'.join(f'{m["x"]:.0f},{m["y"]:.0f} 起 {m["w"]:.0f}×{m["h"]:.0f}' for m in reg) + '——要改的是这一整块，不只是选择器指到的元素')
            if drawn: out.append(f'- 截图上用红笔圈出了 {len(drawn)} 处（看截图里的红色标记）')
            if r.get('files'): out.append('- 附件（读取需口令）：' + '、'.join(f'{"截图" if f["kind"] == "shot" else "图片"} {PREFIX}files/{f["id"]}' for f in r['files']))
            out += ['', r['body'], '']
            for x in r['replies']:
                tag = {'decision': '【决定】', 'proposal': '【方案】', 'report': '【交付】', 'status': ''}.get(x['kind'], '')
                out.append(f'- {x["author"]}{"（负责人）" if x.get("role") == "admin" else ""}（{ts(x["created"])}）：{tag}{x["body"]}')
                if x['kind'] == 'proposal' and x.get('meta'):
                    for o in x['meta'].get('options', []): out.append(f'  - 方案 {o["key"]}：{o["title"]} {o["desc"]} {o["link"]}'.rstrip())
            if r['replies']: out.append('')
    return '\n'.join(out) + '\n'

if __name__ == '__main__':
    if not TEAM: raise SystemExit('缺少 FB_TEAM_CODE（团队口令），例如：FB_TEAM_CODE=换成你的口令 python3 server/fb_server.py')
    init()
    print(f'agent-page-feedback 在 http://{HOST}:{PORT}{PREFIX} 运行；汇总页 {PREFIX}client/feedback.html', flush=True)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
