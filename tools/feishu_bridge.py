#!/usr/bin/env python3
"""飞书桥：用飞书应用的「长连接」（WebSocket）把一个或几个任务中心接进飞书——在飞书里拍板、在飞书里提需求。

- 有新的「待你决定」「待你验收」，发一张带按钮的卡片给负责人（私聊或绑定的群），卡片上标明是哪个项目。
- 负责人在卡片上点「选 A」「先不做」「通过」，经长连接回到这里，记成那个任务中心里的正式决定（decide）。
  只有绑定过的负责人点才算数；别人点会提示没有拍板权限；已经在别处定过的旧卡片不会重复记。
- 负责人给机器人发一句话（不是下面的命令），先弹一张确认卡（开在哪个项目 / 不用），点了才开需求；回复某张卡片，这句话就算在那张工单上（要再改 / 按我写的做 / 只是留言）：
  「好设计：首页卡片小一点」→ 开在「好设计」；「让 Codex 把……」「交给 Codex：……」→ 负责方 Codex；没说交给谁就交给 Claude。
  负责人本人提的需求，定时处理的 agent 下一轮就会接。
- 命令：「绑定 六位码」（登记为负责人）、「决策台」（把还没定的事发一遍）、「进度」（各项目还有多少事）、
  在群里 @机器人「在这里提醒」（以后卡片发到这个群）、「帮助」。

不需要公网回调地址，也不用改任务中心；飞书应用（自建应用）要开长连接、加两个订阅：
  回调 card.action.trigger（卡片回传交互），事件 im.message.receive_v1（接收消息）。

依赖：pip install websocket-client（很小；长连接协议在本文件里实现，不用飞书官方 SDK——它一启动就要 200MB 以上内存）。
配置（放在只有服务账号能读的文件里，不要进仓库）：
  FEISHU_APP_ID / FEISHU_APP_SECRET   飞书应用凭据
  FEISHU_PROJECTS  项目清单文件（JSON 数组），每项：
                   {"key": "hs", "name": "好设计", "alias": ["好设计", "haoshejiguan"], "api": "http://127.0.0.1:18811/api/fb/",
                    "admin_code": "负责人口令", "site_url": "https://haoshejiguan.com/api/fb/"}
                   第一项是默认项目。只有一个项目时，也可以不用文件，改用 FB_API / FB_ADMIN_CODE / FB_SITE_URL / FB_PROJECT_NAME。
  FB_OWNER_NAME  决定和需求上的署名，默认「负责人」，记为「名字（飞书）」
  FEISHU_STATE   状态文件（负责人、群、发过的卡片），默认和本文件同目录的 feishu-state.json
  FEISHU_POLL    多少秒看一次有没有新的事，默认 60
负责人口令只用来把负责人在飞书上的点击和需求记进任务中心，不会发到飞书。
"""
import json, os, random, re, sys, threading, time, urllib.parse, urllib.request

APP_ID, APP_SECRET = os.environ.get('FEISHU_APP_ID', ''), os.environ.get('FEISHU_APP_SECRET', '')
OWNER = os.environ.get('FB_OWNER_NAME', '负责人')
STATE = os.environ.get('FEISHU_STATE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feishu-state.json')
POLL = max(15, int(os.environ.get('FEISHU_POLL', '60')))
LOCK = threading.Lock()
FEISHU = 'https://open.feishu.cn/open-apis/'

def log(*a): print(time.strftime('%m-%d %H:%M:%S'), *a, flush=True)

def load_projects():
    f = os.environ.get('FEISHU_PROJECTS')
    if f:
        with open(f, encoding='utf-8') as fh: ps = json.load(fh)
    else:
        ps = [{'key': 'main', 'name': os.environ.get('FB_PROJECT_NAME', '任务中心'), 'api': os.environ.get('FB_API', 'http://127.0.0.1:8900/api/fb/'),
               'admin_code': os.environ.get('FB_ADMIN_CODE', ''), 'site_url': os.environ.get('FB_SITE_URL', '')}]
    for p in ps:
        p['api'] = p['api'].rstrip('/') + '/'; p['site_url'] = (p.get('site_url') or '').rstrip('/') + '/' if p.get('site_url') else ''
        p.setdefault('alias', [p['name']])
    return ps
PROJECTS = load_projects()
PBY = {p['key']: p for p in PROJECTS}
MULTI = len(PROJECTS) > 1

# ---------- 状态 ----------
def load():
    try:
        with open(STATE, encoding='utf-8') as f: s = json.load(f)
    except (OSError, ValueError): s = {}
    s.setdefault('owners', {}); s.setdefault('chat', ''); s.setdefault('sent', {})
    s.setdefault('msg', {}); s.setdefault('pending', {}); s.setdefault('chat_proj', {})
    if not s.get('bind_code'): s['bind_code'] = '%06d' % random.SystemRandom().randint(0, 999999)
    return s
def save(s):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(s, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600); os.replace(tmp, STATE)
S = load(); save(S)

# ---------- 任务中心 ----------
def fb(p, path, body=None):
    h = {'Content-Type': 'application/json', 'X-FB-Code': p['admin_code'], 'X-FB-Name': urllib.parse.quote(OWNER + '（飞书）')}
    r = urllib.request.Request(p['api'] + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=15) as x: return json.loads(x.read())
    except urllib.error.HTTPError as e:
        try: msg = json.loads(e.read()).get('error')
        except ValueError: msg = ''
        raise RuntimeError(msg or f'任务中心返回 {e.code}')

# ---------- 飞书开放接口 ----------
_TOK = {'v': '', 'exp': 0}
def token():
    if time.time() < _TOK['exp'] - 120: return _TOK['v']
    r = urllib.request.Request(FEISHU + 'auth/v3/tenant_access_token/internal', data=json.dumps({'app_id': APP_ID, 'app_secret': APP_SECRET}).encode(), headers={'Content-Type': 'application/json'})
    d = json.loads(urllib.request.urlopen(r, timeout=15).read())
    if d.get('code'): raise RuntimeError('飞书凭据不对：' + d.get('msg', ''))
    _TOK.update(v=d['tenant_access_token'], exp=time.time() + d.get('expire', 7200)); return _TOK['v']
def send(chat, card):
    body = {'receive_id': chat, 'msg_type': 'interactive', 'content': json.dumps(card, ensure_ascii=False)}
    r = urllib.request.Request(FEISHU + 'im/v1/messages?receive_id_type=chat_id', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json; charset=utf-8', 'Authorization': 'Bearer ' + token()})
    try: d = json.loads(urllib.request.urlopen(r, timeout=15).read())
    except urllib.error.HTTPError as e: d = json.loads(e.read() or b'{}')
    if d.get('code'): raise RuntimeError(f'发消息失败：{d.get("code")} {d.get("msg")}')
    return d['data']['message_id']
def say(chat, text): return send(chat, {'elements': [{'tag': 'markdown', 'content': text}]})

# ---------- 卡片 ----------
def clip(s, n): s = (s or '').strip(); return s if len(s) <= n else s[:n] + '…'
def item_link(p, iid): return (p['site_url'] + f'client/feedback.html#item-{iid}') if p['site_url'] else ''
def tag(p): return f'【{p["name"]}】' if MULTI else ''
def head(t, tpl): return {'title': {'tag': 'plain_text', 'content': clip(t, 90)}, 'template': tpl}
def btn(text, value=None, url=None, kind='default'):
    b = {'tag': 'button', 'text': {'tag': 'plain_text', 'content': text}, 'type': kind}
    if url: b['url'] = url
    else: b['value'] = value
    return b
def ask_card(p, it):
    opts, rec = it.get('options') or [], it.get('rec') or ''
    md = [' · '.join(x for x in (it.get('area'), f'#{it["id"]}', it.get('proposer') or it.get('author')) if x)]
    if it.get('summary'): md.append(clip(it['summary'], 900))
    for o in opts: md.append(f'**{o["key"]}. {o.get("title", "")}**' + ('　✅ 推荐' if o['key'] == rec else '') + (f'\n{clip(o.get("desc"), 300)}' if o.get('desc') else '') + (f'\n[看详情]({o["link"]})' if (o.get('link') or '').startswith('http') else ''))
    if it.get('assignee'): md.append(f'定了以后交给：{it["assignee"]}')
    acts = [btn(f'选 {o["key"]}', {'p': p['key'], 'a': 'choose', 'id': it['id'], 'o': o['key']}, kind='primary' if o['key'] == rec else 'default') for o in opts]
    acts.append(btn('先不做', {'p': p['key'], 'a': 'wontfix', 'id': it['id']}, kind='danger'))
    if item_link(p, it['id']): acts.append(btn('写意见 / 看全部', url=item_link(p, it['id'])))
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head(f'{tag(p)}待你决定 · ' + (it.get('title') or f'#{it["id"]}'), 'purple'),
            'elements': [{'tag': 'markdown', 'content': '\n\n'.join(md)}, {'tag': 'action', 'actions': acts}]}
def review_card(p, it):
    rp = it.get('report') or {}
    md = [' · '.join(x for x in (it.get('area'), f'#{it["id"]}', rp.get('by')) if x), '**交付：** ' + clip(rp.get('summary'), 900)]
    links = [l for l in rp.get('links') or [] if (l.get('url') or '').startswith('http')]
    if links: md.append('　'.join(f'[{l.get("name") or "看结果"}]({l["url"]})' for l in links[:4]))
    acts = [btn('通过', {'p': p['key'], 'a': 'accept', 'id': it['id']}, kind='primary')]
    if item_link(p, it['id']): acts.append(btn('要再改（写意见）', url=item_link(p, it['id'])))
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head(f'{tag(p)}待你验收 · ' + (it.get('title') or f'#{it["id"]}'), 'green'),
            'elements': [{'tag': 'markdown', 'content': '\n\n'.join(md)}, {'tag': 'action', 'actions': acts}]}
def done_card(title, text, tpl='grey'):
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head(title, tpl), 'elements': [{'tag': 'markdown', 'content': text}]}

# ---------- 定时：把新的事发出去 ----------
def todo(p):
    d = fb(p, 'desk?days=30')
    out = [('q', it['id'], it.get('proposed_at') or it['updated'], it) for it in d['open']]
    out += [('r', it['id'], it['report']['at'], it) for it in d['decided'] if it['status'] == 'review' and it.get('report')]
    return out
def remember(mid, pkey, kind, iid):
    """记下每张卡片的消息编号 → 哪个项目哪张工单，负责人回复这张卡片时就知道说的是哪件事。只留最近 800 张。"""
    S['msg'][mid] = [pkey, kind, iid]
    if len(S['msg']) > 800:
        for k in list(S['msg'])[:len(S['msg']) - 800]: S['msg'].pop(k, None)
def push_new(chat=None, force=False):
    chat = chat or S['chat']
    if not chat: return 0
    n = 0
    for p in PROJECTS:
        try: items = todo(p)
        except Exception as e: log(p['name'], '看任务中心失败', e); continue
        for kind, iid, at, it in items:
            key = f'{p["key"]}:{kind}:{iid}:{int(at)}'
            if key in S['sent'] and not force: continue
            try:
                mid = send(chat, ask_card(p, it) if kind == 'q' else review_card(p, it)); n += 1
                with LOCK: S['sent'][key] = int(time.time()); remember(mid, p['key'], kind, iid); save(S)
            except Exception as e: log('发卡片失败', p['name'], iid, e)
    return n
def poller():
    while True:
        try:
            if S['chat']: n = push_new(); n and log('发出', n, '张卡片')
        except Exception as e: log('发卡片出错', e)
        time.sleep(POLL)

# ---------- 长连接：卡片按钮 ----------
def on_card(ev):
    who = ((ev.get('operator') or {}).get('open_id')) or ''; v = ((ev.get('action') or {}).get('value')) or {}
    def resp(kind, text, card=None):
        r = {'toast': {'type': kind, 'content': text}}
        if card: r['card'] = {'type': 'raw', 'data': card}
        return r
    if who not in S['owners']: return resp('error', '只有负责人能拍板。负责人先私聊机器人发「绑定 六位码」。')
    if v.get('a') in ('req', 'note', 'cancel'): return on_confirm(v, who, resp)
    if v.get('a') == 'batch':
        p = PBY.get(v.get('p')) or PROJECTS[0]
        try:
            ids = [x['id'] for x in fb(p, 'queue.json')['review']]
            r = fb(p, 'items/batch', {'action': 'accept', 'ids': ids}) if ids else {'done': [], 'skipped': []}
        except Exception as e: return resp('error', f'没存上：{e}')
        log('飞书一次通过', p['name'], len(r['done']))
        return resp('success', f'已通过 {len(r["done"])} 张', done_card(f'{tag(p)}一次通过 {len(r["done"])} 张', '、'.join(f'#{i}' for i in r['done'][:80]) or '没有待验收的。', 'green'))
    p = PBY.get(v.get('p')) or PROJECTS[0]
    iid, a = int(v.get('id') or 0), v.get('a')
    if not iid or a not in ('choose', 'wontfix', 'accept'): return resp('error', '这个按钮不认识')
    body = {'action': a}
    if a == 'choose': body['option'] = str(v.get('o') or '')
    try:   # 旧卡片：这件事已经在别处定过了，就不再记一次
        d = fb(p, 'desk?days=60'); live = {x['id'] for x in d['open']} if a != 'accept' else {x['id'] for x in d['decided'] if x['status'] == 'review'}
    except Exception as e: return resp('error', f'读不到任务中心：{e}')
    if iid not in live: return resp('info', '这件事已经定过了（或者又出了新方案），以任务中心里的为准。', done_card(f'{tag(p)}已经处理 · #{iid}', '这张卡片过期了。最新的在决策台上。'))
    try: it = fb(p, f'items/{iid}/decide', body)['item']
    except Exception as e: return resp('error', f'没存上：{e}')
    what = {'choose': f'选 {body.get("option")}', 'wontfix': '先不做', 'accept': '通过'}[a]
    log('飞书拍板', p['name'], iid, what)
    st = {'approved': '可以做，已进负责方的工作清单', 'done': '已完成', 'wontfix': '不做'}.get(it['status'], it['status'])
    card = done_card(f'{tag(p)}已定 · {it.get("title") or "#" + str(iid)}', f'**{S["owners"][who]}** 在飞书上点了「{what}」　{time.strftime("%m-%d %H:%M")}\n现在：{st}' + (f'\n交给：{it["assignee"]}' if it.get('assignee') and a == 'choose' else ''), 'green' if a != 'wontfix' else 'grey')
    return resp('success', f'已定：{what}', card)

# ---------- 长连接：消息 ----------
AGENTS = ('Claude', 'Codex', 'Kimi')
def parse_request(text, default=None):
    """一句话 → (项目, 负责方, 标题, 正文)。项目看开头或句子里的项目名，都没有就用这个群绑定的项目；负责方看「让 X」「交给 X」「@X」。"""
    p, t = default or PROJECTS[0], text.strip()
    for q in PROJECTS:
        for al in q['alias']:
            a = re.escape(al); m = re.match(r'\s*(?:[【\[]' + a + r'[】\]]\s*[:：，, ]?|' + a + r'\s*[:：，, ])\s*', t, re.I)
            if m: p, t = q, t[m.end():]; break
        else: continue
        break
    else:
        hits = [q for q in PROJECTS if any(al.lower() in t.lower() for al in q['alias'])]
        if len(hits) == 1: p = hits[0]
    asg = 'Claude'
    m = re.match(r'\s*(?:让|请|交给|@)\s*(' + '|'.join(AGENTS) + r')\s*[:：，, ]?\s*', t, re.I)
    if m: asg = next(a for a in AGENTS if a.lower() == m.group(1).lower()); t = t[m.end():]
    t = t.strip()
    title = re.split(r'[。！？\n]', t)[0][:40] or t[:40]
    return p, asg, title, t
def progress():
    rows = []
    for p in PROJECTS:
        try:
            q = fb(p, 'queue.json'); s = q['summary']
            rows.append(f'**{p["name"]}**：等你定 {s["waiting"]} · 等你验收 {s["review"]} · 可以做 / 在做 {s["act"]} · 新提的 {s["triage_new"]}')
        except Exception as e: rows.append(f'**{p["name"]}**：读不到（{e}）')
    return '\n'.join(rows)
def stash(item):
    t = '%08x' % random.SystemRandom().getrandbits(32)
    with LOCK:
        S['pending'][t] = {**item, 'at': int(time.time())}
        for k in [k for k, x in S['pending'].items() if time.time() - x['at'] > 7 * 86400]: S['pending'].pop(k, None)
        save(S)
    return t
def confirm_req_card(text, chat):
    p, asg, title, body = parse_request(text, PBY.get(S['chat_proj'].get(chat)))
    t = stash({'t': 'req', 'text': text, 'asg': asg, 'title': title, 'body': body, 'guess': p['key']})
    acts = [btn(q['name'], {'a': 'req', 't': t, 'k': q['key']}, kind='primary' if q['key'] == p['key'] else 'default') for q in PROJECTS]
    acts.append(btn('不用，只是说说', {'a': 'cancel', 't': t}))
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head('开成需求？', 'blue'),
            'elements': [{'tag': 'markdown', 'content': f'**{clip(title, 60)}**\n交给 {asg}' + ('（句子里写「让 Codex……」可以换人）' if asg == 'Claude' else '') + f'\n\n点项目名就开在那个项目（猜的是「{p["name"]}」）。'},
                         {'tag': 'action', 'actions': acts}]}
def confirm_note_card(pkey, kind, iid, text):
    p = PBY.get(pkey) or PROJECTS[0]
    t = stash({'t': 'note', 'p': pkey, 'kind': kind, 'id': iid, 'text': text})
    first = btn('要再改（按这句改）', {'a': 'note', 't': t, 'how': 'rework'}, kind='primary') if kind == 'r' else btn('按我写的做', {'a': 'note', 't': t, 'how': 'approve'}, kind='primary')
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head(f'{tag(p)}这句话算在 #{iid} 上', 'blue'),
            'elements': [{'tag': 'markdown', 'content': f'「{clip(text, 200)}」'},
                         {'tag': 'action', 'actions': [first, btn('只是留言', {'a': 'note', 't': t, 'how': 'comment'}), btn('算了', {'a': 'cancel', 't': t})]}]}
def on_confirm(v, who, resp):
    with LOCK: x = S['pending'].pop(v.get('t') or '', None); save(S)
    if not x: return resp('info', '这张确认卡已经处理过了。', done_card('已处理', '这张确认卡已经用过了。'))
    if v['a'] == 'cancel': return resp('success', '好，不开', done_card('没开需求', f'「{clip(x.get("text"), 80)}」只是记在聊天里。'))
    if x['t'] == 'req':
        p = PBY.get(v.get('k')) or PROJECTS[0]
        try: it = fb(p, 'items', {'page': '_requests', 'url': '', 'title': x['title'], 'body': x['body'] + '\n\n（负责人在飞书上提的）', 'assignee': x['asg'], 'priority': 'mid'})['item']
        except Exception as e:
            with LOCK: S['pending'][v['t']] = x; save(S)
            return resp('error', f'没开成：{e}')
        log('飞书开需求', p['name'], it['id'], x['asg'])
        link = item_link(p, it['id'])
        return resp('success', f'已开 #{it["id"]}', done_card(f'{tag(p) or ""}已开需求 #{it["id"]}', f'**{clip(x["title"], 60)}**\n交给 {x["asg"]}；定时处理的 agent 下一轮接走，做完交付会发卡片给你验收。' + (f'\n[在任务中心看]({link})' if link else ''), 'green'))
    p = PBY.get(x['p']) or PROJECTS[0]; iid, how = x['id'], v.get('how')
    try:
        if how == 'comment': fb(p, f'items/{iid}/reply', {'body': x['text']}); what = '留言'
        elif how == 'rework': fb(p, f'items/{iid}/decide', {'action': 'rework', 'note': x['text']}); what = '要再改'
        else: fb(p, f'items/{iid}/decide', {'action': 'approve', 'note': x['text']}); what = '按你写的做'
    except Exception as e: return resp('error', f'没存上：{e}')
    log('飞书回复工单', p['name'], iid, what)
    return resp('success', f'已记在 #{iid}：{what}', done_card(f'{tag(p)}已记在 #{iid}', f'{what}：「{clip(x["text"], 200)}」', 'green'))

HELP = ('直接跟我说一句话，我先弹一张卡问你开在哪个项目，点一下才开需求；点「不用」就不开。\n'
        '回复某张卡片（长按 → 回复），这句话就算在那张工单上：要再改、按你写的做，或者只是留言。\n'
        '「让 Codex ……」交给 Codex；没说交给谁就交给 Claude。\n'
        '命令：「一次通过」把待验收的整批通过；「决策台」把还没定的事发一遍；「进度」看各项目还有多少事；在群里 @我「这个群是好设计」以后这个群默认是好设计；@我「在这里提醒」以后卡片发到这个群。')
def on_message(ev):
    m = ev.get('message') or {}; who = (((ev.get('sender') or {}).get('sender_id')) or {}).get('open_id', '')
    if m.get('message_type') != 'text': return
    try: text = json.loads(m.get('content') or '{}').get('text', '')
    except ValueError: return
    text = ' '.join(w for w in text.split() if not w.startswith('@_user')).strip()
    chat, group = m.get('chat_id'), m.get('chat_type') == 'group'
    if not text: return
    try:
        norm = text.translate({ord(c): ord('0') + i for i, c in enumerate('０１２３４５６７８９')})
        if '绑定' in norm and not group:
            mm = re.search(r'(?<!\d)(\d{6})(?!\d)', norm); code = mm.group(1) if mm else ''
            log('绑定尝试', (code[:2] + '****') if code else '（没有六位数字）', '对' if code == S['bind_code'] else '不对', who[:8] + '…')
            if code != S['bind_code']: say(chat, '绑定码不对（要发「绑定」加六位数字，例如：绑定 123456）。绑定码在服务器上，向管理这台服务器的人要。'); return
            with LOCK: S['owners'][who] = OWNER; S['bind_code'] = '%06d' % random.SystemRandom().randint(0, 999999); S['chat'] = S['chat'] or chat; save(S)
            log('绑定负责人', OWNER, who[:10] + '…'); say(chat, f'好，已把你登记为负责人（{OWNER}）。以后卡片上的按钮你点了就算定了。\n\n' + HELP)
            push_new(chat); return
        if who not in S['owners']:
            if group: return          # 群里别人 @ 我：不回话，不刷屏
            say(chat, '我是任务中心的飞书桥。负责人私聊我发「绑定 六位码」（例如：绑定 123456）后，就可以在飞书里拍板、提需求。'); return
        mg = re.match(r'(?:这个群是|本群是|本群项目[:：]?\s*)(.+)$', text)
        if mg:
            name = mg.group(1).strip(); q = next((q for q in PROJECTS if any(al.lower() == name.lower() or al.lower() in name.lower() for al in q['alias'])), None)
            if not q: say(chat, '没有这个项目。现在有：' + '、'.join(p['name'] for p in PROJECTS)); return
            with LOCK: S['chat_proj'][chat] = q['key']; save(S)
            say(chat, f'好，这个群以后默认是「{q["name"]}」。'); return
        if '在这里提醒' in text or '提醒到这里' in text:
            with LOCK: S['chat'] = chat; save(S)
            say(chat, '好，以后「待你决定」「待你验收」的卡片发到这里。'); return
        if text in ('决策台', '待定', '待决定', '要我定的', '要我定'):
            n = push_new(chat, force=True); say(chat, '现在没有要你定或验收的事。' if not n else f'上面 {n} 张是还没定 / 待验收的。'); return
        if text in ('进度', '状态', '现在怎么样'): say(chat, progress()); return
        if text in ('一次通过', '全部通过', '批量通过', '一次通过一批'):
            acts, rows = [], []
            for q in PROJECTS:
                try: n = len(fb(q, 'queue.json')['review'])
                except Exception: n = 0
                if n: acts.append(btn(f'通过{q["name"]}的 {n} 张', {'a': 'batch', 'p': q['key']})); rows.append(f'**{q["name"]}**：{n} 张待验收' + (f'　[先在任务中心挑]({q["site_url"]}client/feedback.html)' if q['site_url'] else ''))
            if not acts: say(chat, '现在没有待验收的。'); return
            send(chat, {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head('一次通过一批？', 'green'),
                        'elements': [{'tag': 'markdown', 'content': '\n'.join(rows) + '\n\n点按钮就把这个项目「待验收」的全部记为通过。想挑着通过，点链接到任务中心「待你验收」一栏的「一次通过一批…」勾选。'},
                                     {'tag': 'action', 'actions': acts}]}); return
        if text in ('帮助', '?', '？', 'help') or len(text) < 4: say(chat, HELP); return
        ref = S['msg'].get(m.get('parent_id') or '') or S['msg'].get(m.get('root_id') or '')
        if ref: send(chat, confirm_note_card(ref[0], ref[1], ref[2], text)); return
        send(chat, confirm_req_card(text, chat))
    except Exception as e:
        log('处理消息失败', e)
        try: say(chat, f'没办成：{e}')
        except Exception: pass

# ---------- 长连接协议（和飞书官方 SDK 的 ws 客户端一致：protobuf 帧 + 心跳） ----------
def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7f; n >>= 7
        if n: out.append(b | 0x80)
        else: out.append(b); return bytes(out)
def _field(num, wt, v):
    k = _varint(num << 3 | wt)
    if wt == 0: return k + _varint(v)
    return k + _varint(len(v)) + v
def frame_enc(f):
    out = b''.join(_field(n, 0, f.get(k, 0)) for n, k in ((1, 'seq'), (2, 'log'), (3, 'service'), (4, 'method')))
    for hk, hv in f.get('headers', []): out += _field(5, 2, _field(1, 2, hk.encode()) + _field(2, 2, str(hv).encode()))
    for n, k in ((6, 'enc'), (7, 'ptype')):
        if f.get(k): out += _field(n, 2, f[k].encode())
    if f.get('payload') is not None: out += _field(8, 2, f['payload'])
    if f.get('lognew'): out += _field(9, 2, f['lognew'].encode())
    return out
def _fields(b):
    i, n = 0, len(b)
    def vint():
        nonlocal i
        r, sh = 0, 0
        while True:
            x = b[i]; i += 1; r |= (x & 0x7f) << sh; sh += 7
            if not x & 0x80: return r
    while i < n:
        key = vint(); num, wt = key >> 3, key & 7
        if wt == 0: yield num, vint()
        elif wt == 2: ln = vint(); yield num, b[i:i + ln]; i += ln
        elif wt == 1: yield num, b[i:i + 8]; i += 8
        elif wt == 5: yield num, b[i:i + 4]; i += 4
        else: raise ValueError('protobuf 格式不认识')
def frame_dec(b):
    f = {'headers': [], 'payload': b''}
    names = {1: 'seq', 2: 'log', 3: 'service', 4: 'method', 6: 'enc', 7: 'ptype', 9: 'lognew'}
    for num, v in _fields(b):
        if num == 5:
            h = dict(_fields(v)); f['headers'].append((h.get(1, b'').decode(), h.get(2, b'').decode()))
        elif num == 8: f['payload'] = v
        elif num in (6, 7, 9): f[names[num]] = v.decode()
        elif num in names: f[names[num]] = v
    return f

def endpoint():
    r = urllib.request.Request('https://open.feishu.cn/callback/ws/endpoint', data=json.dumps({'AppID': APP_ID, 'AppSecret': APP_SECRET}).encode(), headers={'Content-Type': 'application/json', 'locale': 'zh'})
    d = json.loads(urllib.request.urlopen(r, timeout=15).read())
    if d.get('code'): raise RuntimeError(f'拿不到长连接地址：{d.get("code")} {d.get("msg")}')
    return d['data']['URL'], (d['data'].get('ClientConfig') or {})

def dispatch(payload):
    d = json.loads(payload)
    t = (d.get('header') or {}).get('event_type', '')
    if t == 'card.action.trigger': return on_card(d.get('event') or {})
    if t == 'im.message.receive_v1': threading.Thread(target=on_message, args=(d.get('event') or {},), daemon=True).start()
    return None

def run_ws():
    import base64, websocket
    url, conf = endpoint(); sid = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get('service_id', ['0'])[0])
    ping = max(10, int(conf.get('PingInterval') or 120)); parts = {}
    ws = websocket.create_connection(url, timeout=30); wl = threading.Lock(); alive = {'on': True}
    log('长连接已连上')
    def pinger():
        while alive['on']:
            try:
                with wl: ws.send_binary(frame_enc({'service': sid, 'method': 0, 'headers': [('type', 'ping')]}))
            except Exception: return
            time.sleep(ping)
    threading.Thread(target=pinger, daemon=True).start()
    try:
        ws.settimeout(ping * 3)
        while True:
            op, data = ws.recv_data()
            if op == 8: raise RuntimeError('飞书关闭了连接')
            if op != 2: continue
            f = frame_dec(data); h = dict(f['headers'])
            if f.get('method', 0) == 0:
                if h.get('type') == 'pong' and f['payload']:
                    try: ping = max(10, int(json.loads(f['payload']).get('PingInterval') or ping))
                    except ValueError: pass
                continue
            pl = f['payload']; total = int(h.get('sum') or 1)
            if total > 1:
                buf = parts.setdefault(h.get('message_id'), [None] * total); buf[int(h.get('seq') or 0)] = pl
                if any(x is None for x in buf): continue
                pl = b''.join(parts.pop(h.get('message_id')))
            t0 = time.time(); resp = {'code': 200}
            try:
                res = dispatch(pl) if h.get('type') == 'event' else None
                if res is not None: resp['data'] = base64.b64encode(json.dumps(res, ensure_ascii=False).encode()).decode()
            except Exception as e:
                log('处理失败', e); resp = {'code': 500}
            f['headers'].append(('biz_rt', str(int((time.time() - t0) * 1000)))); f['payload'] = json.dumps(resp).encode()
            with wl: ws.send_binary(frame_enc(f))
    finally:
        alive['on'] = False
        try: ws.close()
        except Exception: pass

def main():
    if not (APP_ID and APP_SECRET and all(p['admin_code'] for p in PROJECTS)): sys.exit('缺少 FEISHU_APP_ID / FEISHU_APP_SECRET / 项目的负责人口令')
    log('飞书桥启动；项目', '、'.join(p['name'] for p in PROJECTS), '；负责人', len(S['owners']), '位；提醒', '已设' if S['chat'] else '未设')
    if not S['owners']: log('还没有绑定负责人。负责人私聊机器人发：绑定', S['bind_code'])
    threading.Thread(target=poller, daemon=True).start()
    wait = 5
    while True:
        t0 = time.time()
        try: run_ws()
        except Exception as e: log('长连接断了：', e)
        wait = 5 if time.time() - t0 > 300 else min(wait * 2, 300)
        time.sleep(wait)

if __name__ == '__main__': main()
