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

## 二、处理批注（日常）

```bash
export FB_API=https://网站/api/fb/  FB_CODE=团队口令   # 口令向负责人要，不要写进仓库
curl -s -H "X-FB-Code: $FB_CODE" "${FB_API}export.md"            # 未完成的批注（待处理 / 处理中 / 待讨论）
curl -s -H "X-FB-Code: $FB_CODE" -o shot.jpg "${FB_API}files/附件编号"   # 看位置截图
```

每一条：

1. **看懂再改**：读意见原文、引用文字、定位（选择器）和截图；页面地址就在清单里。意思不确定时，**不要猜**：
   ```bash
   curl -s -X POST -H "X-FB-Code: $FB_CODE" -H "X-FB-Name: Claude" -H "Content-Type: application/json" \
     -d '{"status":"question","note":"想确认：……"}' "${FB_API}items/编号/status"
   ```
2. **改完验证**：在电脑和手机宽度下都看一遍，确认没有带出别的问题。
3. **提交验收，并写清楚改了什么**：
   ```bash
   curl -s -X POST -H "X-FB-Code: $FB_CODE" -H "X-FB-Name: Claude" -H "Content-Type: application/json" \
     -d '{"note":"原因：……；改法：……；结果：……","preview_url":"https://预览地址","images":["data:image/jpeg;base64,…"]}' \
     "${FB_API}items/编号/review"
   ```
   - 你说「改好了」不算完成，要管理员点「通过」才算。不要试图自己通过：团队口令调用 `accept` 会被拒绝。
   - **改动还没上线**（在预览或测试环境）：带上 `preview_url`。管理员通过后，这条会变成「已通过」并重新出现在 `export.md` 里；这时再发布到正式站，然后
     `POST items/编号/publish {"note":"已发布，线上已核对"}`。没通过之前不要发布。
   - **改动已经在线上**：不带 `preview_url`（或写 `"stage":"live"`），通过后直接完成。
   - `images` 放改后的截图，管理员会和批注时的位置截图对照着看。能截就截，这是验收最快的依据。
   - **被打回**：状态回到「待处理」，理由在讨论记录里（清单里标着【打回】）。先读理由再改，不要原样再交一次。
   - 开工前读一遍 `GET corrections.jsonl`：里面是这个项目历次打回的原话，同样的错不要犯第二次。
   - 署名用你自己的名字（`Claude`、`Codex`），汇总页会把它显示为 AI。
   - 不改的，用 `"status":"wontfix"`，并写明理由。
   - 需要素材或别人决定的，先回复说明需要什么，状态保持「待处理」：
     `POST items/编号/reply {"body":"需要……"}`
4. **通知负责人**（如果项目约定了飞书通知）：
   ```bash
   FEISHU_WEBHOOK=… python3 tools/notify_feishu.py "标题" 内容.md "按钮" 链接
   ```
   - 内容要**详细**：每一条写明原因、改法、结果，并附上页面链接。
   - 消息里不要放口令和密钥。

## 三、规则

- **口令和机器人地址不能外泄**：不写进代码、提交记录、页面、共享文档和通知消息。
- **人做判断，agent 执行**：有分歧、需要取舍的，标「待讨论」交给人决定，不要自作主张。
- **不确定的就标注不确定**：自动推断的内容（比如截图是补拍的、定位是按原坐标的），在回复里说明。
- **不删别人的批注**：只有管理员可以删除；测试时自己建的批注，测完自己删掉。
- **补拍截图**：接入前就有的批注，用 `tools/backfill_shots.js` 补拍。补拍时要先滚动到位、等页面稳定再截图，并在批注下说明「截图是补拍的」。

## 四、接口速查

接口都在 `{FB_PREFIX}` 下（默认 `/api/fb/`），请求头 `X-FB-Code`（口令）、`X-FB-Name`（署名，要做 URL 编码）。

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `ping` | 检查口令，返回角色 team / admin |
| GET | `items?all=1` / `items?page=页面标识` | 批注列表（含回复和附件） |
| GET | `export.md` | 给 agent 的待办清单（默认含待处理、处理中、待讨论、已通过；可用 `?status=` 指定） |
| GET | `export.json` | 全部数据 |
| GET | `files/<id>` | 附件 |
| POST | `items` | 新建批注 |
| POST | `items/<id>/reply` | 回复 `{body, images}` |
| POST | `items/<id>/status` | 改状态 `{status, note}`：open / doing / question / wontfix；团队口令写 done 会变成提交验收 |
| POST | `items/<id>/review` | 提交验收 `{note, preview_url, stage, images}`，状态变为「待验收」 |
| POST | `items/<id>/accept` | 通过（仅管理员）`{note}` |
| POST | `items/<id>/reject` | 打回（仅管理员）`{reason}`，理由必填 |
| POST | `items/<id>/publish` | 已通过的改动上线后标记 `{note}` |
| GET | `corrections.jsonl` | 全部打回记录，每行一条 |
| POST | `items/<id>/shot` | 补传或替换位置截图 `{shot: "data:image/jpeg;base64,…"}` |
| POST | `items/<id>/delete` | 删除（作者或管理员） |
| GET / POST | `decisions` | 逐条确认的决策记录 |
