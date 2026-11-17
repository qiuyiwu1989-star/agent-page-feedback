"""给 agent（包括定时任务）用的命令行：读队列、回复、出方案、记偏好、发汇总、发布、回滚。只用标准库。

环境变量：
  FB_API   接口地址，例如 https://example.com/api/fb/
  FB_KEY   agent 口令（FB_AGENT_CODE）。在 Claude Code 云端环境里，口令存在环境的密钥里、由网络代理自动加到请求头
           X-FB-Key 上，这时不用设置 FB_KEY（这里会发一个占位值，代理会替换）。也可以用 FB_CODE（成员口令，但不能发布、发汇总）
  FB_NAME  署名，默认 Claude

用法：
  python3 tools/fb.py check                     看本轮有没有要做的事：有 → 退出码 0 并打印数量；没有 → 退出码 10
  python3 tools/fb.py md                        工作清单（Markdown）
  python3 tools/fb.py queue                     工作清单（JSON）
  python3 tools/fb.py reply 编号 "内容"
  python3 tools/fb.py propose 编号 方案.json    {"summary": "...", "options": [{"key": "A", "title": "...", "desc": "...", "link": "..."}]}
  python3 tools/fb.py pref "一句话标准" [--item 编号]
  python3 tools/fb.py digest "标题" 内容.md [--button "文字=地址"]...
  python3 tools/fb.py deploy [--ref 提交号] [--item 编号]... [--note 说明]       等发布结束，打印发布编号（回滚编号）
  python3 tools/fb.py rollback 发布编号 [--note 说明]                          整站恢复到这次发布之前
  python3 tools/fb.py deploys                   最近的发布登记
"""
import argparse, json, os, sys, time, urllib.error, urllib.parse, urllib.request

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
    if a.cmd in ('deploy', 'rollback'):
        d = call('deploy', {'ref': a.ref, 'items': a.item, 'note': a.note}) if a.cmd == 'deploy' else call(f'deploys/{a.deploy}/rollback', {'note': a.note})
        d = wait(d['deploy']['id'])
        print(d['output'][-1500:])
        if d['status'] != 'ok': sys.exit(f'第 {d["id"]} 次{"发布" if a.cmd == "deploy" else "回滚"}失败（{d["status"]}）')
        print(f'第 {d["id"]} 次发布成功。回滚编号：deploy#{d["id"]}' if a.cmd == 'deploy' else f'第 {d["id"]} 次（回滚）成功：整站已恢复到第 {a.deploy} 次发布之前')


if __name__ == '__main__':
    main()
