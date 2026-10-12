#!/bin/sh
# 容器入口：选择登录浏览器，并在 Xvfb 虚拟显示器中启动应用（浏览器需以有界面模式运行）。
set -e

# 未显式指定时：装了 Google Chrome（amd64 镜像）就用 Chrome，否则用 patchright 自带 Chromium
if [ -z "$BROWSER_CHANNEL" ] && [ -x /opt/google/chrome/chrome ]; then
  export BROWSER_CHANNEL=chrome
fi

exec xvfb-run -a --server-args="-screen 0 1366x768x24 -nolisten tcp" "$@"
