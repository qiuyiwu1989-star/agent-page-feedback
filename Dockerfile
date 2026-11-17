# 可选：用容器运行。数据（数据库与附件）放在 /data，记得挂载卷。
#   docker build -t agent-page-feedback .
#   docker run -d -p 127.0.0.1:18810:18810 -v $PWD/data:/data -e FB_TEAM_CODE=口令 -e FB_ADMIN_CODE=管理员口令 agent-page-feedback
FROM python:3.12-slim
WORKDIR /app
COPY server ./server
COPY client ./client
ENV FB_DB=/data/fb.db FB_HOST=0.0.0.0 FB_PORT=18810
EXPOSE 18810
CMD ["python3", "-I", "server/fb_server.py"]
