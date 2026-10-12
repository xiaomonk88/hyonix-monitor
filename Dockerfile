FROM python:3.12-slim

# 构建时由 CI 传入（与 app/__init__.py 中的 __version__ 一致）
ARG VERSION=dev
# buildx 自动注入目标架构（amd64 / arm64）
ARG TARGETARCH

LABEL org.opencontainers.image.title="hyonix-monitor" \
      org.opencontainers.image.description="Hyonix VPS 补货监控与 Telegram 通知" \
      org.opencontainers.image.source="https://github.com/xiaomonk88/hyonix-monitor" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DISPLAY=:99 \
    DATA_DIR=/data \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 登录用浏览器：Cloudflare Turnstile 只放行有界面的真浏览器，由 Xvfb 提供虚拟显示器。
# amd64 安装 Google Chrome（实测通过率最高）；arm64 无官方 Chrome，使用 Chromium。
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb xauth fonts-liberation \
 && if [ "$TARGETARCH" = "arm64" ]; then patchright install --with-deps chromium; \
    else patchright install --with-deps chrome; fi \
 && rm -rf /var/lib/apt/lists/*

# 应用代码（模板与静态资源已本地化，运行时无需外网 CDN）
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# 数据卷：SQLite 数据库、登录 Cookie 与日志
VOLUME ["/data"]
EXPOSE 23988

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:23988/healthz').status==200 else 1)" || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
# 单进程运行：调度器与 SQLite 都要求单实例，不要增加 --workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "23988", "--proxy-headers", "--forwarded-allow-ips", "*"]
