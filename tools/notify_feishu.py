"""agent 每完成一项工作后，把结论和链接发到负责人的飞书群（群机器人 Webhook）。
用法：
  FEISHU_WEBHOOK=https://open.feishu.cn/open-apis/bot/v2/hook/xxxx \
  python3 tools/notify_feishu.py "标题" 内容.md ["按钮文字" "按钮链接"]...

- 内容用 Markdown（飞书卡片的 markdown 元素：**加粗**、[文字](链接)、- 列表）
- Webhook 地址只放在环境变量里，不要写进代码仓库；消息里不要放口令、密钥
- 写得详细一些：做了什么、为什么、结果怎样，每项附上页面链接（负责人反馈：要能看懂具体做了什么） """
import json, os, sys, urllib.request

url = os.environ.get('FEISHU_WEBHOOK', '').strip()
if not url or len(sys.argv) < 3: raise SystemExit(__doc__)
title, md = sys.argv[1], open(sys.argv[2], encoding='utf-8').read().strip()
btns = sys.argv[3:]
acts = [{'tag': 'button', 'text': {'tag': 'plain_text', 'content': btns[i]}, 'url': btns[i + 1], 'type': 'primary' if i == 0 else 'default'} for i in range(0, len(btns) - 1, 2)]
card = {'config': {'wide_screen_mode': True}, 'header': {'title': {'tag': 'plain_text', 'content': title}, 'template': 'blue'},
        'elements': [{'tag': 'markdown', 'content': md}] + ([{'tag': 'action', 'actions': acts}] if acts else [])}
req = urllib.request.Request(url, data=json.dumps({'msg_type': 'interactive', 'card': card}, ensure_ascii=False).encode(), headers={'Content-Type': 'application/json'})
print(urllib.request.urlopen(req, timeout=20).read().decode())
