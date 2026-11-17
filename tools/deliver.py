"""给一条任务交付报告（状态会变成「待验收」，等负责人点「通过」）。
用法：
  FB_API=https://网站/api/fb/ FB_CODE=通行码 python3 tools/deliver.py 编号 --summary "改了什么" \
    [--why "为什么"] [--change "改动 1" --change "改动 2"] [--link "名称=地址"] [--rollback "回滚编号"] \
    [--before 改前.jpg] [--after 改后.jpg] [--image 其他图.png] [--name Claude]
只用标准库。图片按文件内容判断类型（PNG / JPEG / WebP / GIF）。"""
import argparse, base64, json, os, sys, urllib.parse, urllib.request

def data_url(path):
    b = open(path, 'rb').read()
    mime = 'image/png' if b[:4] == b'\x89PNG' else 'image/jpeg' if b[:3] == b'\xff\xd8\xff' else 'image/webp' if b[8:12] == b'WEBP' else 'image/gif' if b[:3] == b'GIF' else None
    if not mime: sys.exit(f'不是图片：{path}')
    return f'data:{mime};base64,' + base64.b64encode(b).decode()

ap = argparse.ArgumentParser(description='交付报告')
ap.add_argument('item', type=int); ap.add_argument('--summary', required=True); ap.add_argument('--why', default='')
ap.add_argument('--change', action='append', default=[]); ap.add_argument('--link', action='append', default=[])
ap.add_argument('--rollback', default=''); ap.add_argument('--before'); ap.add_argument('--after'); ap.add_argument('--image', action='append', default=[])
ap.add_argument('--name', default=os.environ.get('FB_NAME', 'Claude'))
a = ap.parse_args()
api, code = os.environ.get('FB_API', ''), os.environ.get('FB_CODE', '')
if not api or not code: sys.exit('请设置环境变量 FB_API（例如 https://example.com/api/fb/）和 FB_CODE（通行码）')
body = {'summary': a.summary, 'why': a.why, 'changes': a.change, 'rollback': a.rollback,
        'links': [{'name': l.split('=', 1)[0], 'url': l.split('=', 1)[1]} if '=' in l else {'name': '查看', 'url': l} for l in a.link],
        'images': [data_url(p) for p in a.image]}
if a.before: body['before'] = data_url(a.before)
if a.after: body['after'] = data_url(a.after)
req = urllib.request.Request(api.rstrip('/') + f'/items/{a.item}/report', data=json.dumps(body, ensure_ascii=False).encode(), method='POST',
                             headers={'Content-Type': 'application/json', 'X-FB-Code': code, 'X-FB-Name': urllib.parse.quote(a.name)})
try:
    with urllib.request.urlopen(req, timeout=60) as r: it = json.loads(r.read())['item']
except urllib.error.HTTPError as e: sys.exit(f'失败 {e.code}：{e.read().decode()[:300]}')
print(f'#{it["id"]} 已交付，状态：待验收')
