"""给 agent（包括定时任务）用的命令行：读队列、回复、出方案、记偏好、发汇总、发布、回滚。只用标准库。

环境变量：
  FB_API   接口地址，例如 https://example.com/api/fb/
  FB_KEY   agent 口令（FB_AGENT_CODE）。在 Claude Code 云端环境里，口令存在环境的密钥里、由网络代理自动加到请求头
           X-FB-Key 上，这时不用设置 FB_KEY（这里会发一个占位值，代理会替换）。也可以用 FB_CODE（成员口令，但不能发布、发汇总）
  FB_NAME  署名，默认 Claude
  FB_SITE_URL  网站根地址（按文件发布时用来取文件），例如 https://example.com/site/

用法：
  python3 tools/fb.py check                     看本轮有没有要做的事：有 → 退出码 0 并打印数量；没有 → 退出码 10
  python3 tools/fb.py md                        工作清单（Markdown）
  python3 tools/fb.py queue                     工作清单（JSON）
  python3 tools/fb.py reply 编号 "内容"
  python3 tools/fb.py propose 编号 方案.json    {"summary": "...", "options": [{"key": "A", "title": "...", "desc": "...", "link": "..."}]}
  python3 tools/fb.py pref "一句话标准" [--item 编号]
  python3 tools/fb.py digest "标题" 内容.md [--button "文字=地址"]...
  python3 tools/fb.py deploy [--ref 提交号] [--item 编号]... [--note 说明]       等发布结束，打印发布编号（回滚编号）

  按文件发布（网站不是 git 仓库、直接在服务器上改文件时）：
  python3 tools/fb.py fetch 工作目录 页面.html css/a.css ...    从正式站取最新版本，记下指纹（改之前一定先取）
  （在工作目录里修改这些文件，或新增文件）
  python3 tools/fb.py deploy --dir 工作目录 --item 编号 [--delete 路径]   只发布改过的文件；有人同时改过同一个文件会报冲突
  python3 tools/fb.py rollback 发布编号 [--note 说明]                          整站恢复到这次发布之前
  python3 tools/fb.py deploys                   最近的发布登记
"""
import argparse, base64, hashlib, io, json, os, sys, tarfile, time, urllib.error, urllib.parse, urllib.request

API = os.environ.get('FB_API', '').rstrip('/') + '/'
KEY = os.environ.get('FB_KEY') or os.environ.get('FB_CODE') or 'injected-by-proxy'
NAME = os.environ.get('FB_NAME', 'Claude')


def call(path, body=None, raw=False):
    if API == '/': sys.exit('请设置 FB_API，例如 https://example.com/api/fb/')
    h = {'X-FB-Key': KEY, 'X-FB-Name': urllib.parse.quote(NAME)}
    if body is not None: h['Content-Type'] = 'application/json'
    req = urllib.request.Request(API + path, data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: b = r.read()
    except urllib.error.HTTPError as e: sys.exit(f'失败 {e.code}：{e.read().decode("utf-8", "replace")[:300]}')
    return b.decode() if raw else json.loads(b)


BASE = '.fb-base.json'


def server_file(rel):
    """先问批注服务（有源文件目录时取的是源文件）；服务器不支持时返回 None，改从正式站取。"""
    h = {'X-FB-Key': KEY, 'X-FB-Name': urllib.parse.quote(NAME)}
    try:
        with urllib.request.urlopen(urllib.request.Request(API + 'site-file?path=' + urllib.parse.quote(rel), headers=h), timeout=60) as r: return json.loads(r.read())
    except urllib.error.HTTPError as e:
        msg = e.read().decode('utf-8', 'replace')
        if e.code == 404 or (e.code == 400 and ('没有设置发布脚本' in msg or '不支持取文件' in msg)): return None   # 服务器不支持：改从正式站取
        try: msg = json.loads(msg).get('error', msg)
        except ValueError: pass
        sys.exit(f'取不到 {rel}：{msg}')


def fetch(work, paths):
    site = os.environ.get('FB_SITE_URL', '').rstrip('/') + '/'
    os.makedirs(work, exist_ok=True); bf = os.path.join(work, BASE); base = json.load(open(bf)) if os.path.exists(bf) else {}
    for rel in paths:
        rel = rel.strip('/'); dst = os.path.join(work, rel)
        sf = server_file(rel) if API != '/' else None
        if sf is not None:
            if sf['b64'] is None: base[rel] = None; print(f'  {rel}：还没有这个文件（会作为新文件）'); continue
            b = base64.b64decode(sf['b64']); os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
            with open(dst, 'wb') as f: f.write(b)
            base[rel] = sf['sha']; print(f'  {rel}：{len(b)} 字节（{"源文件" if sf["source"] else "网站"}）'); continue
        if site == '/': sys.exit('服务器不支持取文件，请设置 FB_SITE_URL 从正式站取，例如 https://example.com/site/')
        try:
            with urllib.request.urlopen(urllib.request.Request(site + urllib.parse.quote(rel), headers={'Cache-Control': 'no-cache'}), timeout=60) as r: b = r.read()
        except urllib.error.HTTPError as e:
            if e.code != 404: sys.exit(f'取不到 {rel}：{e.code}')
            base[rel] = None; print(f'  {rel}：正式站上没有（会作为新文件）'); continue
        os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
        with open(dst, 'wb') as f: f.write(b)
        base[rel] = hashlib.sha256(b).hexdigest(); print(f'  {rel}：{len(b)} 字节')
    with open(bf, 'w') as f: json.dump(base, f, indent=1)


def bundle(work, deletes):
    """工作目录里和取来时不一样的文件（以及新文件）打成发布包。"""
    bf = os.path.join(work, BASE); base = json.load(open(bf)) if os.path.exists(bf) else {}
    files = {}
    for dp, dn, fn in os.walk(work):
        for n in fn:
            p = os.path.join(dp, n); rel = os.path.relpath(p, work).replace(os.sep, '/')
            if rel == BASE: continue
            b = open(p, 'rb').read()
            if rel in base and base[rel] == hashlib.sha256(b).hexdigest(): continue   # 没改
            files[rel] = b
    dels = {d.strip('/'): base.get(d.strip('/')) for d in deletes}
    for d, h in dels.items():
        if not h: sys.exit(f'要删除的 {d} 没有先 fetch，不知道它现在是哪一版')
    if not files and not dels: sys.exit('工作目录里没有改动')
    man = {'files': {rel: {'base': base.get(rel)} for rel in files}, 'delete': dels}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as t:
        for name, b in [('manifest.json', json.dumps(man, ensure_ascii=False).encode())] + [('files/' + r, b) for r, b in files.items()]:
            ti = tarfile.TarInfo(name); ti.size = len(b); ti.mtime = int(time.time()); t.addfile(ti, io.BytesIO(b))
    for rel in files: print(f'  {"修改" if base.get(rel) else "新增"} {rel}')
    for rel in dels: print(f'  删除 {rel}')
    return base64.b64encode(buf.getvalue()).decode()


def wait(did):
    for _ in range(600):
        d = call(f'deploys/{did}')['deploy']
        if d['status'] != 'running': return d
        time.sleep(3)
    sys.exit(f'第 {did} 次发布 30 分钟还没结束，请人工检查')


def main():
    ap = argparse.ArgumentParser(description='agent-page-feedback 命令行'); sub = ap.add_subparsers(dest='cmd', required=True)
    for c in ('check', 'md', 'queue', 'deploys'): sub.add_parser(c)
    p = sub.add_parser('reply'); p.add_argument('item', type=int); p.add_argument('text')
    p = sub.add_parser('propose'); p.add_argument('item', type=int); p.add_argument('file')
    p = sub.add_parser('pref'); p.add_argument('text'); p.add_argument('--item', type=int)
    p = sub.add_parser('digest'); p.add_argument('title'); p.add_argument('file'); p.add_argument('--button', action='append', default=[])
    p = sub.add_parser('deploy'); p.add_argument('--ref', default=''); p.add_argument('--item', type=int, action='append', default=[]); p.add_argument('--note', default='')
    p.add_argument('--dir', help='按文件发布：工作目录'); p.add_argument('--delete', action='append', default=[])
    p = sub.add_parser('fetch'); p.add_argument('dir'); p.add_argument('paths', nargs='+')
    p = sub.add_parser('rollback'); p.add_argument('deploy', type=int); p.add_argument('--note', default='')
    a = ap.parse_args()

    if a.cmd == 'check':
        s = call('queue.json')['summary']; todo = s['act'] + s['triage_new']
        print(json.dumps(s, ensure_ascii=False)); sys.exit(0 if todo else 10)
    if a.cmd == 'md': print(call('export.md', raw=True)); return
    if a.cmd == 'queue': print(json.dumps(call('queue.json'), ensure_ascii=False, indent=1)); return
    if a.cmd == 'deploys':
        for d in call('deploys')['deploys'][:20]:
            print(f'#{d["id"]} {d["action"]} {d["status"]} ref={d["ref"] or "-"} 任务={d["items"]} {d["author"]} {time.strftime("%m-%d %H:%M", time.localtime(d["created"]))} {d["note"] or ""}')
        return
    if a.cmd == 'reply': print('已回复 #%d' % call(f'items/{a.item}/reply', {'body': a.text})['item']['id']); return
    if a.cmd == 'propose':
        with open(a.file, encoding='utf-8') as f: body = json.load(f)
        print('已出方案 #%d，状态：待决定' % call(f'items/{a.item}/propose', body)['item']['id']); return
    if a.cmd == 'pref': print('已记入偏好手册：' + call('prefs', {'text': a.text, 'item': a.item})['pref']['text']); return
    if a.cmd == 'digest':
        with open(a.file, encoding='utf-8') as f: md = f.read()
        btns = [{'text': b.split('=', 1)[0], 'url': b.split('=', 1)[1]} for b in a.button if '=' in b]
        r = call('digest', {'title': a.title, 'markdown': md, 'buttons': btns})
        print('汇总已发到飞书群' if r['sent'] else '汇总已存档（服务器没有设置飞书地址，没有推送）'); return
    if a.cmd == 'fetch': fetch(a.dir, a.paths); return
    if a.cmd in ('deploy', 'rollback'):
        if a.cmd == 'deploy':
            body = {'ref': a.ref, 'items': a.item, 'note': a.note}
            if a.dir: body['bundle'] = bundle(a.dir, a.delete)
            d = call('deploy', body)
        else: d = call(f'deploys/{a.deploy}/rollback', {'note': a.note})
        d = wait(d['deploy']['id'])
        print(d['output'][-1500:])
        if d['status'] != 'ok': sys.exit(f'第 {d["id"]} 次{"发布" if a.cmd == "deploy" else "回滚"}失败（{d["status"]}）')
        print(f'第 {d["id"]} 次发布成功。回滚编号：deploy#{d["id"]}' if a.cmd == 'deploy' else f'第 {d["id"]} 次（回滚）成功：已撤销第 {a.deploy} 次发布')


if __name__ == '__main__':
    main()
