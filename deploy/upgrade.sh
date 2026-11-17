#!/usr/bin/env bash
# 一键升级已经在运行的批注服务（systemd）。在解压后的新版目录里运行：
#   sudo bash deploy/upgrade.sh 服务名            例如 sudo bash deploy/upgrade.sh zjuid-feedback
#   sudo bash deploy/upgrade.sh 服务名 --force    服务器上的文件改过也照样覆盖（先看清楚报告）
#   sudo bash deploy/upgrade.sh 服务名 --enroll   不生成 agent 口令，改为生成一次性登记码（口令由负责人自己定、存进云端环境，
#                                                  第一次请求 POST agent-enroll 时登记指纹；谁也不用看到口令）
#
# 它会：
#   1. 从 systemd 找到这个服务的程序位置、配置文件、数据库；
#   2. 检查服务器上的程序是不是原版（v1 / v2）。改过的话先停下来、列出不同，不覆盖；
#   3. 备份程序和数据库；
#   4. 换上新版，配置里没有 agent 口令就生成一个；
#   5. 重启并检查。检查不通过就自动恢复备份；
#   6. 顺便列出网站是怎么提供的（nginx 配置、网站目录、是不是 git 仓库），用来写发布脚本。不打印任何口令。
set -euo pipefail
NEW="$(cd "$(dirname "$0")/.." && pwd)"
SVC="${1:-}"; FORCE=""; ENROLL_MODE=""
for a in "${@:2}"; do case "$a" in --force) FORCE=--force;; --enroll) ENROLL_MODE=1;; esac; done
say() { printf '\n== %s\n' "$*"; }
[ -n "$SVC" ] || { echo "用法：sudo bash deploy/upgrade.sh 服务名"; systemctl list-units --type=service --all --no-legend | grep -i -E 'feedback|fb' || true; exit 2; }
[ "$(id -u)" = 0 ] || { echo "请用 sudo 运行"; exit 2; }

say "1. 找到服务 $SVC"
UNIT="$(systemctl cat "$SVC" 2>/dev/null)" || { echo "没有这个服务：$SVC"; exit 2; }
EXEC="$(printf '%s\n' "$UNIT" | sed -n 's/^ExecStart=//p' | tail -1)"
SERVER_FILE="$(printf '%s\n' "$EXEC" | tr ' ' '\n' | grep -m1 'fb_server\.py$' || true)"
ENVF="$(printf '%s\n' "$UNIT" | sed -n 's/^EnvironmentFile=-\{0,1\}//p' | tail -1)"
RUNAS="$(printf '%s\n' "$UNIT" | sed -n 's/^User=//p' | tail -1)"; RUNAS="${RUNAS:-root}"
[ -f "$SERVER_FILE" ] || { echo "在 ExecStart 里没找到 fb_server.py：$EXEC"; exit 2; }
getenv() { # systemd 里 EnvironmentFile 的值优先于 Environment=，这里也一样
  local v=""; [ -n "$ENVF" ] && [ -f "$ENVF" ] && v="$(grep -E "^$1=" "$ENVF" | tail -1 | cut -d= -f2- || true)"
  [ -z "$v" ] && v="$(printf '%s\n' "$UNIT" | grep -o "Environment=.*$1=[^ \"]*" | sed "s/.*$1=//" | tail -1 || true)"; printf '%s' "$v"; }
# 程序目录：server/fb_server.py 布局取上一级；fb_server.py 直接放在目录里的布局取本目录
SD="$(cd "$(dirname "$SERVER_FILE")" && pwd)"; if [ "$(basename "$SD")" = server ]; then APP="$(dirname "$SD")"; else APP="$SD"; fi
CLIENT_DIR="$(getenv FB_CLIENT_DIR)"; CLIENT_DIR="${CLIENT_DIR:-$(dirname "$SD")/client}"
DB="$(getenv FB_DB)"; DB="${DB:-$(dirname "$SD")/data/fb.db}"
PORT="$(getenv FB_PORT)"; PORT="${PORT:-18810}"; PREFIX="$(getenv FB_PREFIX)"; PREFIX="/$(printf '%s' "${PREFIX:-/api/fb/}" | sed 's#^/*##; s#/*$##')/"
echo "程序目录：$APP"; echo "程序：$SERVER_FILE"; echo "前端：$CLIENT_DIR"; echo "数据库：$DB"; echo "配置：${ENVF:-（没有 EnvironmentFile）}"; echo "端口：$PORT  前缀：$PREFIX  运行用户：$RUNAS"

say "2. 检查服务器上的程序是不是原版"
FILES="server/fb_server.py client/fb-loader.js client/feedback.js client/fb-media.js client/feedback.html client/demo.html"
live() { case "$1" in server/fb_server.py) echo "$SERVER_FILE";; client/*) echo "$CLIENT_DIR/${1#client/}";; esac; }
changed=""
for f in $FILES; do
  l="$(live "$f")"; [ -f "$l" ] || { echo "  $f：服务器上没有（会新增）"; continue; }
  h="$(sha256sum "$l" | cut -d' ' -f1)"; ok=""
  ok="$(awk -v f="$f" -v h="$h" '$2 == f && $3 == h {print $1}' "$NEW/deploy/known-hashes.txt" | tail -1)"
  [ "$(sha256sum "$NEW/$f" | cut -d' ' -f1)" = "$h" ] && ok="已是新版"
  if [ -n "$ok" ]; then echo "  $f：$( [ "$ok" = 已是新版 ] && echo 已是新版 || echo "原版（$ok）")"; else echo "  $f：**改过**"; changed="$changed $f"; fi
done
if [ -n "$changed" ] && [ "$FORCE" != "--force" ]; then
  echo; echo "服务器上这些文件和原版不一样，为了不丢掉改动，先不覆盖：$changed"
  for f in $changed; do echo "--- $f：$(wc -l < "$(live "$f")") 行，修改时间 $(date -r "$(live "$f")" '+%F %T')"; done
  echo "把上面的报告发给 Claude，确认可以覆盖后再加 --force 运行。"
  SKIP_APPLY=1
fi

if [ -z "${SKIP_APPLY:-}" ]; then
  say "3. 备份"
  TS="$(date +%Y%m%d-%H%M%S)"; BK="$APP/backup-$TS"; mkdir -p "$BK"
  cp -a "$SERVER_FILE" "$BK/fb_server.py"; cp -a "$CLIENT_DIR" "$BK/client"; [ -d "$APP/tools" ] && cp -a "$APP/tools" "$BK/tools"
  [ -n "$ENVF" ] && [ -f "$ENVF" ] && cp -a "$ENVF" "$BK/env"
  python3 - "$DB" "$BK/fb.db" <<'PY'
import sqlite3, sys
s = sqlite3.connect(sys.argv[1]); d = sqlite3.connect(sys.argv[2]); s.backup(d); d.close(); s.close()
PY
  chmod -R go-rwx "$BK"; echo "备份在 $BK"

  say "4. 换上新版"
  install -m 644 "$NEW/server/fb_server.py" "$SERVER_FILE"
  mkdir -p "$CLIENT_DIR"; cp -a "$NEW/client/." "$CLIENT_DIR/"
  mkdir -p "$APP/tools" "$APP/deploy" "$APP/docs"; cp -a "$NEW/tools/." "$APP/tools/"
  cp -a "$NEW/deploy/deploy.sh.example" "$APP/deploy/"; cp -a "$NEW/docs/autopilot.md" "$APP/docs/"
  chown "$RUNAS" "$SERVER_FILE" 2>/dev/null || true; chown -R "$RUNAS" "$CLIENT_DIR" "$APP/tools" "$APP/deploy" "$APP/docs" 2>/dev/null || true
  NEWKEY=""; NEWENROLL=""
  if [ -n "$ENROLL_MODE" ] && [ -n "$ENVF" ] && [ -f "$ENVF" ] && ! grep -qE '^FB_AGENT_(CODE|ENROLL)=.+' "$ENVF"; then
    NEWENROLL="$(python3 -c 'import secrets; print(secrets.token_urlsafe(18))')"
    printf '\n# 一次性登记码：agent 第一次请求 POST agent-enroll 时登记自己的口令（只存指纹），用过就失效\nFB_AGENT_ENROLL=%s\n' "$NEWENROLL" >> "$ENVF"; echo "已在配置里加上一次性登记码"
  elif [ -z "$ENROLL_MODE" ] && [ -n "$ENVF" ] && [ -f "$ENVF" ] && ! grep -qE '^FB_AGENT_CODE=.+' "$ENVF"; then
    NEWKEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
    printf '\n# agent 专用口令（定时任务用：能发布、回滚、发汇总，不能拍板）\nFB_AGENT_CODE=%s\n' "$NEWKEY" >> "$ENVF"; echo "已在配置里加上 agent 口令"
  fi

  say "5. 重启并检查"
  systemctl restart "$SVC"; okk=""
  TEAM="$(getenv FB_TEAM_CODE)"; AG="$(getenv FB_AGENT_CODE)"
  for i in $(seq 1 30); do
    r="$(curl -s -m 3 -H "X-FB-Code: $TEAM" "http://127.0.0.1:$PORT${PREFIX}ping" || true)"
    case "$r" in *'"ok": true'*) okk=1; break;; esac; sleep 1
  done
  if [ -n "$okk" ] && [ -n "$AG" ]; then
    case "$(curl -s -m 3 -H "X-FB-Key: $AG" "http://127.0.0.1:$PORT${PREFIX}ping" || true)" in *'"role": "agent"'*) ;; *) okk="";; esac
  fi
  q="$(curl -s -m 5 -H "X-FB-Code: $TEAM" "http://127.0.0.1:$PORT${PREFIX}queue.json" || true)"
  case "$q" in *'"summary"'*) ;; *) okk="";; esac
  if [ -z "$okk" ]; then
    echo "检查没通过，恢复备份……"
    install -m 644 "$BK/fb_server.py" "$SERVER_FILE"; rm -rf "$CLIENT_DIR"; cp -a "$BK/client" "$CLIENT_DIR"; [ -f "$BK/env" ] && cp -a "$BK/env" "$ENVF"
    systemctl restart "$SVC"; journalctl -u "$SVC" -n 30 --no-pager; echo "已恢复到升级前。"; exit 1
  fi
  echo "升级成功：服务在运行，口令可用，任务清单正常。"
  printf '%s' "$q" | python3 -c 'import json,sys; s=json.load(sys.stdin)["summary"]; print("当前任务：可以执行 %d · 待分诊（新）%d · 待决定 %d · 待验收 %d" % (s["act"], s["triage_new"], s["waiting"], s["review"]))'
fi

say "6. 网站是怎么提供的（用来写发布脚本）"
NG="$(nginx -T 2>/dev/null || true)"
printf '%s\n' "$NG" | grep -n -E '^# configuration file|server_name|location|root |alias |proxy_pass' | grep -v -E 'ssl_|#.*example' | head -80 || true
for d in $(printf '%s\n' "$NG" | grep -E '^\s*(root|alias)\s' | awk '{print $2}' | tr -d ';' | sort -u); do
  [ -d "$d" ] || continue
  g="$(git -C "$d" rev-parse --show-toplevel 2>/dev/null || true)"
  printf '  %s  文件 %s 个，最近修改 %s%s\n' "$d" "$(find "$d" -maxdepth 3 -type f | wc -l)" "$(find "$d" -maxdepth 3 -type f -printf '%TY-%Tm-%Td\n' 2>/dev/null | sort | tail -1)" "${g:+  git 仓库 $g（$(git -C "$g" remote get-url origin 2>/dev/null || echo 无远程)）}"
done
which coscli coscmd 2>/dev/null | sed 's/^/  对象存储工具：/' || true
{ ls -d /home/ubuntu/*/ 2>/dev/null || true; } | sed "s/^/  目录：/"

if [ -n "${NEWENROLL:-}" ]; then
  say "一次性登记码（用过一次就失效）"
  echo "$NEWENROLL"
fi
if [ -n "${NEWKEY:-}" ]; then
  say "agent 口令（只显示这一次）"
  echo "$NEWKEY"
  echo "请把它填进 Claude 发给你的安全表单。不要发在聊天里。"
fi
