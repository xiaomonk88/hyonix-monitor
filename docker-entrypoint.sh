#!/bin/sh
# 容器入口：启动 Xvfb 虚拟显示器（登录浏览器需以有界面模式运行），再启动应用。
# DISPLAY=:99 由镜像 ENV 设定（docker exec 也能用）；浏览器选择见 app/config.py。
# 不使用 xvfb-run：它等待 Xvfb 的就绪信号，在部分容器环境中会一直阻塞，导致应用无法启动。
set -e

# 入口运行时容器内不会有 Xvfb 在跑；容器重启后 /tmp 中可能残留旧的锁与 socket，先清理
if [ "$DISPLAY" = ":99" ]; then
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
  Xvfb :99 -screen 0 1366x768x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
  i=0
  while [ ! -e /tmp/.X11-unix/X99 ] && [ "$i" -lt 20 ]; do sleep 0.5; i=$((i + 1)); done
  if [ -e /tmp/.X11-unix/X99 ]; then
    echo "[entrypoint] Xvfb 已启动（DISPLAY=$DISPLAY）"
  else
    # 不阻塞应用启动：登录会失败并在仪表盘 / Telegram 告警中体现
    echo "[entrypoint] 警告：Xvfb 未能启动，自动登录将不可用"; cat /tmp/xvfb.log || true
  fi
fi

exec "$@"
