#!/usr/bin/env python3
"""飞书桥：用飞书应用的「长连接」（WebSocket）把任务中心接进飞书——在飞书里直接拍板。

- 有新的「待你决定」「待你验收」，发一张带按钮的卡片到绑定的群（或负责人私聊）。
- 负责人在卡片上点「选 A」「先不做」「通过」，经长连接回到这里，转成任务中心里的正式决定（decide）。
  只有绑定过的负责人点才算数；别人点会提示没有拍板权限。
- 私聊机器人：「绑定 六位码」把自己登记为负责人（六位码只在服务器日志和状态文件里）；
  在群里 @机器人 说「在这里提醒」，以后卡片发到这个群；说「决策台」或「待定」，把还没定的事发一遍。

不需要公网回调地址，也不用改任务中心；只需要飞书应用（自建应用）开了长连接、加了两个订阅：
  回调 card.action.trigger（卡片回传交互），事件 im.message.receive_v1（接收消息）。

依赖：pip install websocket-client（很小；长连接协议在本文件里实现，不用飞书官方 SDK——它一启动就要 200MB 以上内存）。配置（环境变量，放在只有服务账号能读的文件里，不要进仓库）：
  FEISHU_APP_ID / FEISHU_APP_SECRET   飞书应用凭据
  FB_API         任务中心接口，例如 http://127.0.0.1:8900/api/fb/
  FB_ADMIN_CODE  负责人口令（只用来把负责人在飞书上的点击记成决定；不会发到飞书）
  FB_OWNER_NAME  决定上的署名，默认「负责人」，记为「名字（飞书）」
  FB_SITE_URL    任务中心的公网地址前缀，例如 https://example.com/api/fb/（卡片上的「在决策台看」）
  FEISHU_STATE   状态文件（负责人、群、发过的卡片），默认和本文件同目录的 feishu-state.json
  FEISHU_POLL    多少秒看一次有没有新的事，默认 60
"""
import json, os, random, sys, threading, time, urllib.parse, urllib.request

APP_ID, APP_SECRET = os.environ.get('FEISHU_APP_ID', ''), os.environ.get('FEISHU_APP_SECRET', '')
API = os.environ.get('FB_API', 'http://127.0.0.1:8900/api/fb/')
ADMIN = os.environ.get('FB_ADMIN_CODE', '')
OWNER = os.environ.get('FB_OWNER_NAME', '负责人')
SITE = os.environ.get('FB_SITE_URL', '').rstrip('/') + '/' if os.environ.get('FB_SITE_URL') else ''
STATE = os.environ.get('FEISHU_STATE') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'feishu-state.json')
POLL = max(15, int(os.environ.get('FEISHU_POLL', '60')))
LOCK = threading.Lock()
FEISHU = 'https://open.feishu.cn/open-apis/'

def log(*a): print(time.strftime('%m-%d %H:%M:%S'), *a, flush=True)

# ---------- 状态 ----------
def load():
    try:
        with open(STATE, encoding='utf-8') as f: s = json.load(f)
    except (OSError, ValueError): s = {}
    s.setdefault('owners', {}); s.setdefault('chat', ''); s.setdefault('sent', {})
    if not s.get('bind_code'): s['bind_code'] = '%06d' % random.SystemRandom().randint(0, 999999)
    return s
def save(s):
    tmp = STATE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f: json.dump(s, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600); os.replace(tmp, STATE)
S = load(); save(S)

# ---------- 任务中心 ----------
def fb(path, body=None):
    h = {'Content-Type': 'application/json', 'X-FB-Code': ADMIN, 'X-FB-Name': urllib.parse.quote(OWNER + '（飞书）')}
    r = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, headers=h)
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
def desk_link(iid): return (SITE + f'client/feedback.html#item-{iid}') if SITE else ''
def head(t, tpl): return {'title': {'tag': 'plain_text', 'content': clip(t, 90)}, 'template': tpl}
def btn(text, value=None, url=None, kind='default'):
    b = {'tag': 'button', 'text': {'tag': 'plain_text', 'content': text}, 'type': kind}
    if url: b['url'] = url
    else: b['value'] = value
    return b
def ask_card(it):
    opts, rec = it.get('options') or [], it.get('rec') or ''
    md = []
    if it.get('area'): md.append(f'**{it["area"]}** · #{it["id"]} · {it.get("proposer") or it.get("author") or ""}')
    if it.get('summary'): md.append(clip(it['summary'], 900))
    for o in opts: md.append(f'**{o["key"]}. {o.get("title", "")}**' + ('　✅ 推荐' if o['key'] == rec else '') + (f'\n{clip(o.get("desc"), 300)}' if o.get('desc') else '') + (f'\n[看详情]({o["link"]})' if (o.get('link') or '').startswith('http') else ''))
    if it.get('assignee'): md.append(f'定了以后交给：{it["assignee"]}')
    acts = [btn(f'选 {o["key"]}', {'a': 'choose', 'id': it['id'], 'o': o['key']}, kind='primary' if o['key'] == rec else 'default') for o in opts]
    acts.append(btn('先不做', {'a': 'wontfix', 'id': it['id']}, kind='danger'))
    if desk_link(it['id']): acts.append(btn('写意见 / 看全部', url=desk_link(it['id'])))
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head('待你决定 · ' + (it.get('title') or f'#{it["id"]}'), 'purple'),
            'elements': [{'tag': 'markdown', 'content': '\n\n'.join(md)}, {'tag': 'action', 'actions': acts}]}
def review_card(it):
    rp = it.get('report') or {}
    md = [f'**{it.get("area") or "交付"}** · #{it["id"]} · {rp.get("by") or ""}', '**交付：** ' + clip(rp.get('summary'), 900)]
    links = [l for l in rp.get('links') or [] if (l.get('url') or '').startswith('http')]
    if links: md.append('　'.join(f'[{l.get("name") or "看结果"}]({l["url"]})' for l in links[:4]))
    acts = [btn('通过', {'a': 'accept', 'id': it['id']}, kind='primary')]
    if desk_link(it['id']): acts.append(btn('要再改（写意见）', url=desk_link(it['id'])))
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head('待你验收 · ' + (it.get('title') or f'#{it["id"]}'), 'green'),
            'elements': [{'tag': 'markdown', 'content': '\n\n'.join(md)}, {'tag': 'action', 'actions': acts}]}
def done_card(title, text, tpl='grey'):
    return {'config': {'wide_screen_mode': True, 'update_multi': True}, 'header': head(title, tpl), 'elements': [{'tag': 'markdown', 'content': text}]}

# ---------- 定时：把新的事发出去 ----------
def todo():
    d = fb('desk?days=30')
    out = [('q', it['id'], it.get('proposed_at') or it['updated'], it) for it in d['open']]
    out += [('r', it['id'], it['report']['at'], it) for it in d['decided'] if it['status'] == 'review' and it.get('report')]
    return out
def push_new(chat=None, force=False):
    chat = chat or S['chat']
    if not chat: return 0
    n = 0
    for kind, iid, at, it in todo():
        key = f'{kind}:{iid}:{int(at)}'
        if key in S['sent'] and not force: continue
        try:
            send(chat, ask_card(it) if kind == 'q' else review_card(it)); n += 1
            with LOCK: S['sent'][key] = int(time.time()); save(S)
        except Exception as e: log('发卡片失败', iid, e)
    return n
def poller():
    while True:
        try:
            if S['chat']: n = push_new(); n and log('发出', n, '张卡片')
        except Exception as e: log('看任务中心失败', e)
        time.sleep(POLL)

# ---------- 长连接：卡片按钮 ----------
def on_card(ev):
    who = ((ev.get('operator') or {}).get('open_id')) or ''; v = ((ev.get('action') or {}).get('value')) or {}
    def resp(kind, text, card=None):
        r = {'toast': {'type': kind, 'content': text}}
        if card: r['card'] = {'type': 'raw', 'data': card}
        return r
    if who not in S['owners']: return resp('error', '只有负责人能拍板。负责人先私聊机器人发「绑定 六位码」。')
    iid, a = int(v.get('id') or 0), v.get('a')
    if not iid or a not in ('choose', 'wontfix', 'accept'): return resp('error', '这个按钮不认识')
    body = {'action': a}
    if a == 'choose': body['option'] = str(v.get('o') or '')
    try:   # 旧卡片：这件事已经在别处定过了，就不再记一次
        d = fb('desk?days=60'); live = {x['id'] for x in d['open']} if a != 'accept' else {x['id'] for x in d['decided'] if x['status'] == 'review'}
    except Exception as e: return resp('error', f'读不到任务中心：{e}')
    if iid not in live: return resp('info', '这件事已经定过了（或者又出了新方案），以任务中心里的为准。', done_card(f'已经处理 · #{iid}', '这张卡片过期了。最新的在决策台上。'))
    try: it = fb(f'items/{iid}/decide', body)['item']
    except Exception as e: return resp('error', f'没存上：{e}')
    what = {'choose': f'选 {body.get("option")}', 'wontfix': '先不做', 'accept': '通过'}[a]
    log('飞书拍板', iid, what, S['owners'][who])
    st = {'approved': '可以做，已进负责方的工作清单', 'done': '已完成', 'wontfix': '不做'}.get(it['status'], it['status'])
    card = done_card(f'已定 · {it.get("title") or "#" + str(iid)}', f'**{S["owners"][who]}** 在飞书上点了「{what}」　{time.strftime("%m-%d %H:%M")}\n现在：{st}' + (f'\n交给：{it["assignee"]}' if it.get('assignee') and a == 'choose' else ''), 'green' if a != 'wontfix' else 'grey')
    return resp('success', f'已定：{what}', card)

# ---------- 长连接：消息 ----------
def on_message(ev):
    m = ev.get('message') or {}; who = (((ev.get('sender') or {}).get('sender_id')) or {}).get('open_id', '')
    if m.get('message_type') != 'text': return
    try: text = json.loads(m.get('content') or '{}').get('text', '')
    except ValueError: return
    text = ' '.join(w for w in text.split() if not w.startswith('@_user')).strip()
    chat = m.get('chat_id')
    try:
        if text.startswith('绑定'):
            code = text[2:].strip()
            if code != S['bind_code']: say(chat, '绑定码不对。绑定码在服务器上（飞书桥的日志 / 状态文件），向管理这台服务器的人要。'); return
            name = OWNER
            with LOCK: S['owners'][who] = name; S['bind_code'] = '%06d' % random.SystemRandom().randint(0, 999999); S['chat'] = S['chat'] or chat; save(S)
            log('绑定负责人', name, who[:10] + '…'); say(chat, f'好，已把你登记为负责人（{name}）。以后卡片上的按钮你点了就算定了。\n在群里 @我 说「在这里提醒」，卡片就发到那个群；说「决策台」，把还没定的事发一遍。'); return
        if who not in S['owners']: say(chat, '我是任务中心的飞书桥。负责人私聊我发「绑定 六位码」后，就可以在飞书里拍板。'); return
        if '在这里提醒' in text or '提醒到这里' in text:
            with LOCK: S['chat'] = chat; save(S)
            say(chat, '好，以后「待你决定」「待你验收」的卡片发到这里。'); return
        if any(k in text for k in ('决策台', '待定', '待决定', '要我定')):
            n = push_new(chat, force=True); say(chat, '现在没有要你定或验收的事。' if not n else f'上面 {n} 张是还没定 / 待验收的。'); return
        say(chat, '我能做的：「决策台」把还没定的事发一遍；在群里 @我「在这里提醒」以后卡片发到那个群。拍板直接点卡片上的按钮。')
    except Exception as e: log('回复消息失败', e)

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
    if not (APP_ID and APP_SECRET and ADMIN): sys.exit('缺少 FEISHU_APP_ID / FEISHU_APP_SECRET / FB_ADMIN_CODE')
    log('飞书桥启动；任务中心', API, '；负责人', len(S['owners']), '位；提醒群', '已设' if S['chat'] else '未设')
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
