#!/usr/bin/env bash
# Hyonix 监控 · 一键安装 / 升级脚本（Linux + Docker）
#
#   curl -fsSL https://raw.githubusercontent.com/xiaomonk88/hyonix-monitor/main/install.sh | bash
#
# 可选环境变量（写在 bash 前面，如 HYONIX_PORT=9000 bash）：
#   HYONIX_DIR      安装目录，默认 /opt/hyonix-monitor
#   HYONIX_PORT     对外端口，默认 23988（仅首次生成 .env 时使用）
#   HYONIX_VERSION  镜像版本，默认 latest（仅首次生成 .env 时使用）
#
# 再次运行即为升级：保留 .env 与 data/，拉取新镜像后重启。
set -euo pipefail

REPO="xiaomonk88/hyonix-monitor"
BRANCH="main"
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
DIR="${HYONIX_DIR:-/opt/hyonix-monitor}"

c_info() { printf '\033[36m[hyonix]\033[0m %s\n' "$*"; }
c_ok()   { printf '\033[32m[hyonix]\033[0m %s\n' "$*"; }
c_warn() { printf '\033[33m[hyonix]\033[0m %s\n' "$*"; }
c_err()  { printf '\033[31m[hyonix]\033[0m %s\n' "$*" >&2; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if command -v sudo >/dev/null 2>&1; then SUDO="sudo"; else c_err "请使用 root 运行，或先安装 sudo"; exit 1; fi
fi

fetch() {  # fetch URL DEST
  if command -v curl >/dev/null 2>&1; then curl -fsSL "$1" -o "$2"
  elif command -v wget >/dev/null 2>&1; then wget -qO "$2" "$1"
  else c_err "需要 curl 或 wget"; exit 1; fi
}

ask_yes() {  # 从终端读取确认（兼容 curl | bash），默认 Yes
  local reply="y"
  if [ -r /dev/tty ]; then read -r -p "$1 [Y/n] " reply </dev/tty || reply="y"; fi
  [ -z "$reply" ] || [[ "$reply" =~ ^[Yy] ]]
}

# ---- 1. Docker ----
if ! command -v docker >/dev/null 2>&1; then
  c_warn "未检测到 Docker。"
  if ask_yes "是否通过官方脚本 get.docker.com 自动安装 Docker？"; then
    curl -fsSL https://get.docker.com | $SUDO sh
    $SUDO systemctl enable --now docker 2>/dev/null || true
  else
    c_err "请先安装 Docker 后重试：https://docs.docker.com/engine/install/"; exit 1
  fi
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then DOCKER="$SUDO docker"; fi
if $DOCKER compose version >/dev/null 2>&1; then COMPOSE="$DOCKER compose"
elif command -v docker-compose >/dev/null 2>&1; then COMPOSE="$SUDO docker-compose"
else c_err "未找到 docker compose 插件，请安装 docker-compose-plugin 后重试"; exit 1; fi

# ---- 2. 目录与配置 ----
$SUDO mkdir -p "$DIR/data"
$SUDO chown "$(id -u):$(id -g)" "$DIR" 2>/dev/null || true
cd "$DIR"

c_info "下载 docker-compose.yml → $DIR"
fetch "$RAW/docker-compose.yml" docker-compose.yml.new && mv docker-compose.yml.new docker-compose.yml

if [ ! -f .env ]; then
  c_info "首次安装：生成 .env（含随机会话密钥）"
  fetch "$RAW/.env.example" .env
  if command -v openssl >/dev/null 2>&1; then SECRET="$(openssl rand -hex 32)"
  else SECRET="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"; fi
  sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=${SECRET}|" .env
  sed -i "s|^HYONIX_PORT=.*|HYONIX_PORT=${HYONIX_PORT:-23988}|" .env
  sed -i "s|^HYONIX_VERSION=.*|HYONIX_VERSION=${HYONIX_VERSION:-latest}|" .env

  # Hyonix 账号：优先取环境变量，否则从终端询问（兼容 curl | bash）
  EMAIL="${HYONIX_EMAIL:-}"; PASS="${HYONIX_PASSWORD:-}"
  if [ -z "$EMAIL" ] && [ -r /dev/tty ]; then read -r -p "Hyonix 登录邮箱: " EMAIL </dev/tty || true; fi
  if [ -z "$PASS" ] && [ -r /dev/tty ]; then read -r -s -p "Hyonix 登录密码: " PASS </dev/tty || true; echo; fi
  case "$PASS" in *"'"*) c_warn "密码含单引号，请安装后手动编辑 $DIR/.env 中的 HYONIX_PASSWORD"; PASS="";; esac
  sed -i '/^HYONIX_EMAIL=/d; /^HYONIX_PASSWORD=/d' .env
  printf "HYONIX_EMAIL=%s\nHYONIX_PASSWORD='%s'\n" "$EMAIL" "$PASS" >> .env
  [ -n "$EMAIL" ] && [ -n "$PASS" ] || c_warn "未填写 Hyonix 账号，请稍后编辑 $DIR/.env 并执行 docker compose up -d"
  chmod 600 .env
else
  c_info "保留已有 .env 与 data/（升级模式）"
fi
PORT="$(grep -E '^HYONIX_PORT=' .env | tail -1 | cut -d= -f2)"; PORT="${PORT:-23988}"

# ---- 3. 拉取镜像（失败则从源码构建）----
c_info "拉取镜像 ghcr.io/${REPO}..."
if $COMPOSE pull; then
  rm -f docker-compose.override.yml
else
  c_warn "镜像拉取失败，改为从 GitHub 源码本地构建（约 1~2 分钟）"
  rm -rf src && mkdir -p src
  fetch "https://github.com/${REPO}/archive/refs/heads/${BRANCH}.tar.gz" src.tar.gz
  tar -xzf src.tar.gz -C src --strip-components=1 && rm -f src.tar.gz
  cat > docker-compose.override.yml <<'YML'
services:
  hyonix-monitor:
    image: hyonix-monitor:local
    build:
      context: ./src
YML
  $COMPOSE build
fi

# ---- 4. 启动 ----
$COMPOSE up -d --remove-orphans
c_info "等待服务就绪..."
ok=""
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then ok=1; break; fi
  sleep 2
done
$DOCKER image prune -f >/dev/null 2>&1 || true

IP="$(curl -fsS -m 3 https://api.ipify.org 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
echo
if [ -n "$ok" ]; then
  VER="$(curl -fsS "http://127.0.0.1:${PORT}/healthz" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')"
  c_ok "Hyonix 监控 v${VER:-?} 已运行"
else
  c_warn "服务暂未通过健康检查，可用 'cd $DIR && $COMPOSE logs -f' 查看日志"
fi
cat <<EOF

  访问地址：http://${IP:-服务器IP}:${PORT}
  默认账号：admin / admin（首次登录需修改密码）
  安装目录：${DIR}（数据在 ${DIR}/data，配置在 ${DIR}/.env）

  升级：重新运行本安装命令
  日志：cd ${DIR} && ${COMPOSE} logs -f
  忘记密码：cd ${DIR} && ${COMPOSE} exec hyonix-monitor python -m app.reset_admin

EOF
