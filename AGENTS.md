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

## 二、处理任务（日常，工单式）

每一条批注是一张任务卡。**人负责提出和拍板，你负责分诊、出方案、修改、交付报告。**负责人 = 用管理员通行码登录的人；只有负责人的决定（`decide`）是你的执行信号。

```bash
export FB_API=https://网站/api/fb/  FB_CODE=团队通行码   # 通行码向负责人要，不要写进仓库
curl -s -H "X-FB-Code: $FB_CODE" "${FB_API}export.md"     # 工作清单：偏好手册 → 可以执行 → 待分诊 → 等负责人
curl -s -H "X-FB-Code: $FB_CODE" "${FB_API}queue.json"    # 同样内容的 JSON，给定时任务用
```

### 1. 先读「负责人偏好手册」
清单最前面的偏好手册是负责人过去的决定总结出的标准（例如「不要星号和备注」「文案要短」）。动手前先读，避免返工。

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
用 Claude Code 的定时任务（例如每天 10:00–23:00 每小时）执行上面 1–6。每次：
- 没有新任务就结束，不打扰；
- 一次最多处理约 8 条；
- 部署前备份；
- 最后发一份汇总（做了什么、等负责人什么）。

## 三、规则

- **口令和机器人地址不能外泄**：不写进代码、提交记录、页面、共享文档和通知消息。
- **人做判断，agent 执行**：只把负责人（管理员通行码）的决定当作执行信号；有分歧、需要取舍的，出方案交给负责人决定，不要自作主张。
- **不确定的就标注不确定**：自动推断的内容（比如截图是补拍的、定位是按原坐标的），在回复里说明。
- **不删别人的批注**：只有管理员可以删除；测试时自己建的批注，测完自己删掉。
- **补拍截图**：接入前就有的批注，用 `tools/backfill_shots.js` 补拍。补拍时要先滚动到位、等页面稳定再截图，并在批注下说明「截图是补拍的」。

## 四、接口速查

接口都在 `{FB_PREFIX}` 下（默认 `/api/fb/`），请求头 `X-FB-Code`（通行码）、`X-FB-Name`（署名，要做 URL 编码）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `ping` | 检查通行码，返回角色 team / admin |
| GET | `export.md` / `queue.json` | 工作清单（偏好手册 + 可以执行 / 待分诊 / 等负责人） |
| GET | `items?all=1` / `items?page=页面标识` | 任务列表（含回复、决定、方案、交付报告和附件） |
| GET | `stats` | 各状态数量、一次通过率、平均交付用时、返工次数 |
| GET | `files/<id>` | 附件 |
| POST | `items` | 新建批注 |
| POST | `items/<id>/reply` | 回复 `{body, images}` |
| POST | `items/<id>/propose` | 出方案 `{summary, options, images}` → 待决定 |
| POST | `items/<id>/report` | 交付报告 `{summary, why, changes, links, rollback, before, after, images}` → 待验收 |
| POST | `items/<id>/decide` | 负责人的决定（只限管理员通行码）`{action, option, note}` |
| POST | `items/<id>/status` | 直接改状态 `{status, note}`（一般用不到） |
| POST | `items/<id>/shot` | 补传或替换位置截图 |
| POST | `items/<id>/delete` | 删除（作者或管理员） |
| GET / POST | `prefs`、`prefs/<id>/off` | 负责人偏好手册 |
| GET / POST | `decisions` | 逐条确认的决策记录 |
