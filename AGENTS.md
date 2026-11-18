# 给 AI agent 的说明

你（Claude、Codex 或其他 agent）可能会被要求做两件事：**把这套批注能力接入一个新项目**，或者**在已经接入的项目里处理批注**。两件事都按下面做。

## 一、把批注接入一个新项目

前提：项目有一台能运行 Python 3.9+ 的服务器，网站前面有 nginx（或其他能转发路径的反向代理）。

1. **放代码**：把本仓库的 `server/`、`client/` 复制到服务器，例如 `/opt/agent-page-feedback/`。
2. **生成口令**：团队口令和管理员口令各一个，用随机字符串。写进 `/opt/agent-page-feedback/.env`（格式见 `.env.example`），权限设为 600。
   - **不要**把口令写进代码仓库、共享文档或聊天记录以外的地方；口令只交给项目负责人。
3. **运行服务**：用 `deploy/agent-page-feedback.service.example` 建一个 systemd 服务，或者用 `Dockerfile` 运行。数据目录要持久化，并加入备份。
4. **转发路径**：按 `deploy/nginx.conf.example` 把 `/api/fb/` 转给服务，`client_max_body_size 25m`，然后执行 `nginx -t` 再重载。
   - 网站在子路径下时，接口也放在子路径下，并设置 `FB_PREFIX`。
5. **页面里加一行**：在网站的公共页头或页脚模板里加
   `<script src="/api/fb/client/fb-loader.js" defer></script>`。
   - 网站在子路径下时，加上 `data-base="/子路径/"`。
   - 页面标题有统一后缀时，加上 `data-title-suffix`。
   - 如果网站是由生成器产出的，要改生成器的模板，不要只改产出的文件，否则下次生成会被覆盖。
6. **验证**：
   - 打开 `https://网站/?fb=1`，输入团队口令和一个测试姓名。
   - 点「批注」，点一个位置，确认表单里出现「位置截图」，然后提交。
   - 打开 `/api/fb/client/feedback.html`，确认能看到这条批注和截图。
   - 删除这条测试批注。
   - 再用一个**没有登录过的浏览器**打开网站，确认没有加载任何批注文件。
7. **交给负责人**：链接 `https://网站/?fb=1`、团队口令、管理员口令、汇总页地址。口令只发给负责人本人。
8. **可选**：负责人有飞书群机器人地址的话，设置 `FB_NOTIFY_URL`，新批注会自动推送。

## 〇、最省事：用 MCP 连上任务中心

任务中心本身是一个 MCP 服务（`{FB_PREFIX}mcp`）。连上以后，你会先收到这一页的规则摘要（像 skill 一样），然后用工具干活：
`briefing`（偏好手册 + 工作清单）、`list_tasks`、`get_task`、`corrections`、`claim`、`reply`、`propose`、`report`、`crosscheck`、`create_request`、`assign`、`add_preference`、`send_digest`。
没有拍板工具——拍板只属于负责人。

```bash
# Claude Code（口令只填在自己电脑上）
claude mcp add --transport http tasks https://网站/api/fb/mcp --header "X-FB-Code: 口令" --header "X-FB-Name: Claude"
```
```toml
# Codex：~/.codex/config.toml
[mcp_servers.tasks]
url = "https://网站/api/fb/mcp"
http_headers = { "X-FB-Code" = "口令", "X-FB-Name" = "Codex" }
```
任务中心页面上的「给 agent 接入」会显示带网站地址的这两段。发布网站文件仍然用 `tools/fb.py`（见第 7 节）。

## 二、处理任务（日常，工单式）

每一条批注是一张任务卡。**人负责提出和拍板，你负责分诊、出方案、修改、交付报告。**负责人 = 用管理员通行码登录的人；只有负责人的决定（`decide`）是你的执行信号。

```bash
export FB_API=https://网站/api/fb/  FB_CODE=团队通行码   # 通行码向负责人要，不要写进仓库
curl -s -H "X-FB-Code: $FB_CODE" "${FB_API}export.md"     # 工作清单：偏好手册 → 可以执行 → 待分诊 → 等负责人
curl -s -H "X-FB-Code: $FB_CODE" "${FB_API}queue.json"    # 同样内容的 JSON，给定时任务用
```

### 1. 先读「负责人偏好手册」
清单最前面的偏好手册是负责人过去的决定总结出的标准（例如「不要星号和备注」「文案要短」）。动手前先读，避免返工。

### 负责方：只做自己的
每张工单可以有「负责方」（`assignee`）：Claude、Codex、某位老师……
- **只处理负责方是自己、或者没有负责方的工单**；别人的跳过（定时任务用 `FB_FOR=Claude,-`，Codex 用 `FB_FOR=Codex`）：
  `GET queue.json?for=Codex`、`GET export.md?for=Claude,-`。
- 给别人开工单（例如发现要改生成器源码，交给 Codex）：`POST items {page: "_requests", title, body, assignee: "Codex"}`，正文写清「怎么算做完」。也可以在任务中心点「新建需求」。
- 正文第一行写「【交给 X】」的旧写法会自动识别成负责方 X。
- agent 之间的委托也要负责人点「可以做」才开工（负责人已确认过的清单，工单里会注明）。

### 交叉核对
一个 agent 交付后，另一个 agent 按「怎么算做完」逐条核对，结果写进工单：
`POST items/<编号>/check {"body": "逐条核对的结果", "result": "pass" 或 "fail"}`（或 `python3 tools/fb.py crosscheck 编号 pass "……"`）。
只给负责人参考，不改状态，不算验收。

### 认领：先改「处理中」再动手
一个项目可能有几个 agent（定时任务、别的会话）。开始处理某条之前，先把它改成「处理中」并署名，别人看到「处理中」就跳过：
```bash
curl -s -X POST -H "X-FB-Key: $FB_KEY" -H "X-FB-Name: Claude" -H "Content-Type: application/json" \
  -d '{"status":"doing","note":"定时任务处理中"}' "${FB_API}items/编号/status"
```
清单里「可以执行」的任务如果已经是「处理中」、并且署名不是你，就跳过。

### 2. 「可以执行」：负责人已确认，直接做
- 状态 `approved`，清单里写着负责人信号：可以做 / 选方案 X / 再改（附意见）/ 改回来。
- 做之前先截「改前」图，改完截「改后」图（`tools/snap.js`），在电脑和手机宽度下检查。
- 「改回来」：用上一次交付报告里的回滚编号恢复原样，同样交付报告。

### 3. 「待分诊」：新提出的，先判断
- **负责人本人提的、意思明确的**（清单里标「（负责人）」）：可以直接做，做完交付报告。
- **别人提的、意思明确的**：回复一句打算怎么改，等负责人点「可以做」。
- **不明确、需要取舍的**：出方案，状态会变成「待决定」：
  ```bash
  curl -s -X POST -H "X-FB-Code: $FB_CODE" -H "X-FB-Name: Claude" -H "Content-Type: application/json" \
    -d '{"summary":"两个方向","options":[{"key":"A","title":"……","desc":"……","link":"预览地址"},{"key":"B","title":"……"}]}' "${FB_API}items/编号/propose"
  ```
  能做成可预览的页面就做（放在不影响正式页面的地址），负责人看了更好选。

### 4. 「等负责人」：不要动
待决定（`question`）、待验收（`review`）的任务在等负责人，不要改。

### 5. 交付报告
改完不要直接标「已完成」，而是交付报告，状态变成「待验收」，负责人点「通过」才算完成：
```bash
python3 tools/deliver.py 编号 --summary "改了什么" --why "为什么" --change "文件 / 位置 1" --change "……" \
  --link "页面=https://…" --rollback "回滚编号" --before before.jpg --after after.jpg
```
- 回滚编号：部署前的备份编号、提交号等，能据此恢复原样即可。
- 报告会推送到负责人的通知群（如果配置了）。

### 6. 把负责人的标准记进偏好手册
负责人「再改」「选 A 不选 B」时，如果能总结出一条通用标准，就记下来（一句话，写清适用范围）：
```bash
curl -s -X POST -H "X-FB-Code: $FB_CODE" -H "X-FB-Name: Claude" -H "Content-Type: application/json" \
  -d '{"text":"首页各板块之间的间距统一在 200–280px","item":43}' "${FB_API}prefs"
```

### 7. 定时自动处理（可选）
每天 10:00–23:00 每小时跑一轮，完整说明和指令模板见 [docs/autopilot.md](docs/autopilot.md)。要点：
- 用 agent 口令（`FB_AGENT_CODE`）：能发布、回滚、发汇总，不能拍板。云端环境里口令由网络代理加到请求头 `X-FB-Key`，不要读取或打印。
- 先 `python3 tools/fb.py check`：没有要做的事就结束，不打扰；
- 一次最多处理约 8 条；只处理负责方是自己或为空的（`FB_FOR=Claude,-`）；处理前先认领（改「处理中」）；
- 发布：网站是 git 仓库时 `python3 tools/fb.py deploy --ref 提交号 --item 编号`；直接在服务器上改文件的网站用 `fb.py fetch` 取文件、改完 `fb.py deploy --dir 工作目录 --item 编号`（有冲突检查，只备份动到的文件）。回滚编号写「deploy#发布编号」；
- 最后 `python3 tools/fb.py digest` 发汇总到负责人的飞书群（飞书地址只在服务器上）；
- 先用「演练」模式跑 1–2 天，负责人同意后再正式。

## 三、规则

- **口令和机器人地址不能外泄**：不写进代码、提交记录、页面、共享文档和通知消息。
- **人做判断，agent 执行**：只把负责人（管理员通行码）的决定当作执行信号；有分歧、需要取舍的，出方案交给负责人决定，不要自作主张。
- **不确定的就标注不确定**：自动推断的内容（比如截图是补拍的、定位是按原坐标的），在回复里说明。
- **不删别人的批注**：只有管理员可以删除；测试时自己建的批注，测完自己删掉。
- **补拍截图**：接入前就有的批注，用 `tools/backfill_shots.js` 补拍。补拍时要先滚动到位、等页面稳定再截图，并在批注下说明「截图是补拍的」。

## 四、接口速查

接口都在 `{FB_PREFIX}` 下（默认 `/api/fb/`），请求头 `X-FB-Code` 或 `X-FB-Key`（通行码）、`X-FB-Name`（署名，要做 URL 编码）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `ping` | 检查通行码，返回角色 team / agent / admin |
| GET | `export.md` / `queue.json` | 工作清单（偏好手册 + 可以执行 / 待分诊 / 等负责人）；`?for=Codex` 只看负责方是它的，`?for=Claude,-` 含没有负责方的 |
| GET | `items?all=1` / `items?page=页面标识` | 任务列表（含回复、决定、方案、交付报告和附件） |
| GET | `stats` | 各状态数量、一次通过率、平均交付用时、返工次数 |
| GET | `corrections.jsonl` | 负责人每次「再改」「改回来」一行：任务、你当时的交付、负责人的原话。开工前读一遍，同样的错不要犯第二次 |
| GET | `files/<id>` | 附件 |
| POST | `items` | 新建批注 |
| POST | `items/<id>/reply` | 回复 `{body, images}` |
| POST | `items/<id>/propose` | 出方案 `{summary, options, images}` → 待决定 |
| POST | `items/<id>/report` | 交付报告 `{summary, why, changes, links, rollback, before, after, images}` → 待验收 |
| POST | `items/<id>/decide` | 负责人的决定（只限管理员通行码）`{action, option, note}` |
| POST | `items/<id>/status` | 直接改状态 `{status, note}`（认领时改「处理中」）。不能改成「可以做」「不改」；改成「已完成」会当作交付报告，等负责人验收 |
| POST | `items/<id>/assign` | 改负责方 `{assignee, note}`（提出人或负责人） |
| POST | `items/<id>/check` | 交叉核对 `{body, result: pass / fail}`，不算验收 |
| POST | `items/<id>/shot` | 补传或替换位置截图 |
| POST | `items/<id>/delete` | 删除（作者或管理员） |
| GET / POST | `prefs`、`prefs/<id>/off` | 负责人偏好手册 |
| GET / POST | `decisions` | 逐条确认的决策记录 |
| POST | `digest` | 发汇总到负责人的飞书群 `{title, markdown, buttons}`（agent / 管理员口令） |
| POST | `deploy` | 发布 `{ref, items, note, bundle}`，返回发布编号（agent / 管理员口令） |
| GET | `site-file?path=` | 取一个文件的当前版本（有源文件时取源文件），按文件发布用 |
| GET | `deploys`、`deploys/<id>` | 发布登记 |
| POST | `deploys/<id>/rollback` | 撤销这次发布（按文件发布：只恢复它动过的文件；git 发布：整站恢复到它之前）（agent / 管理员口令） |
| GET | `presence`、`activity` | 在场（谁在做哪张、是否可能卡住）和全局动态 |
| POST | `mcp` | MCP 服务（JSON-RPC）：连上拿到规则和工具，权限和直接调接口一样 |
| POST | `agent-enroll` | 一次性登记 agent 口令（请求头 `X-FB-Enroll` 登记码 + `X-FB-Key` 口令，只存指纹） |
