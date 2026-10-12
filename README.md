# Hyonix 监控

自托管的 [Hyonix](https://hyonix.com) VPS 补货监控后台：自动登录 Hyonix 客户中心，定时比对全站机房与套餐库存，发现补货、售罄、价格变化和上下架时，通过 Telegram 推送通知。

基于 [Misaka 监控](https://github.com/xiaomonk88/misaka-monitor) 改造，后台界面与使用方式保持一致。

## 功能

- **全自动登录**：内置浏览器自动登录 my.hyonix.com（含 Cloudflare 人机验证），会话失效自动重登，无需手动复制 Cookie
- **产品目录**：全站机房 / 套餐 / 价格 / 实时库存 / 宿主机余量，跨机房勾选套餐一键创建监控
- **监控任务**：单个套餐、整个机房或全站上新；独立轮询周期与订阅事件；一键探测
- **Telegram 通知**：补货（附宿主机余量与下单链接）、售罄、价格与规格变化、上下架
- **异常告警**：登录失败、被拦截或页面结构变化导致连续抓取失败时推送告警，恢复后再通知
- **事件日志**：监控事件与同步日志分开记录，筛选、产品时间线、CSV 导出
- **安全**：默认账号 `admin / admin`，首次登录强制修改密码；单文件 SQLite，数据全部在本地

## 一键安装（Docker）

在 Linux 服务器上执行（需 root 或 sudo；未安装 Docker 时脚本会询问是否自动安装）：

```bash
curl -fsSL https://raw.githubusercontent.com/xiaomonk88/hyonix-monitor/main/install.sh | bash
```

首次安装会提示输入 **Hyonix 登录邮箱和密码**（写入安装目录的 `.env`，权限 600）。完成后访问 `http://服务器IP:23988`，用 **`admin / admin`** 登录并设置新密码。

可选参数写在 `bash` 前，例如改端口、免交互传入账号：

```bash
curl -fsSL https://raw.githubusercontent.com/xiaomonk88/hyonix-monitor/main/install.sh | HYONIX_PORT=9000 HYONIX_EMAIL=you@example.com HYONIX_PASSWORD='your-pass' bash
```

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HYONIX_DIR` | `/opt/hyonix-monitor` | 安装目录（`.env` 配置与 `data/` 数据都在这里） |
| `HYONIX_PORT` | `23988` | 对外访问端口 |
| `HYONIX_VERSION` | `latest` | 镜像版本，可锁定为如 `1.0.0` |
| `HYONIX_EMAIL` / `HYONIX_PASSWORD` | 交互输入 | Hyonix 客户中心账号 |

## 首次使用

1. **通知渠道**：添加 Telegram Bot Token 与 Chat ID，点「测试」确认能收到消息
2. **产品目录**：启动后会自动登录并同步一次（首次登录约 20 秒）；也可点「同步目录」手动刷新
3. 在目录中勾选想要的套餐（可跨机房多选）→ 创建监控任务，补货时立即推送到 Telegram

仪表盘的「Hyonix 账号」卡片显示登录状态与最近一次抓取错误。

## 工作原理

- Hyonix 的登录页（`clientarea.php` / `dologin.php`）受 Cloudflare Turnstile 保护，普通 HTTP 客户端和无头浏览器都会被拦截。
  容器内用 [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python)（去除自动化特征的 Playwright）驱动 **有界面的 Chrome**，由 Xvfb 提供虚拟显示器，自动通过验证并登录（勾选 Remember Me）。
- 登录后的会话 Cookie 保存在 `data/hyonix_cookies.json`，日常轮询只用 HTTP 抓取订购页 `order.php`（约 150 KB / 次），不启动浏览器。
- 订购页内嵌全站数据：`allLocations`（机房，含可开套餐列表）与 `allPlans`（套餐规格与价格）。某套餐的 pid 出现在机房的可开列表中即为有货，出现次数为可开出该套餐的宿主机数（「余量」）。

## 升级

重新运行一键安装命令即可：保留 `.env` 与 `data/`，拉取最新镜像后重启。也可以手动：

```bash
cd /opt/hyonix-monitor
docker compose pull && docker compose up -d
```

## 常用命令

```bash
cd /opt/hyonix-monitor
docker compose logs -f                                         # 实时日志
docker compose restart                                         # 重启
docker compose down                                            # 停止（数据保留在 ./data）
docker compose exec hyonix-monitor python -m app.reset_admin   # 忘记后台密码：重置为 admin / admin
```

修改 Hyonix 密码后：编辑 `.env` 中的 `HYONIX_PASSWORD`，删除 `data/hyonix_cookies.json`，再 `docker compose up -d`。

## 数据与备份

- 全部数据（SQLite 数据库、登录 Cookie、文件日志）在安装目录的 `data/`，备份即复制该目录
- `.env` 与 `data/` 含 Hyonix 账号密码、会话 Cookie 与 Telegram Token，请勿公开
- 建议防火墙只对自己的 IP 放行端口，或前置 Nginx / Caddy 反向代理并启用 HTTPS

## 从源码运行

```bash
git clone https://github.com/xiaomonk88/hyonix-monitor.git && cd hyonix-monitor
cp .env.example .env            # 填写 HYONIX_EMAIL / HYONIX_PASSWORD，修改 APP_SECRET_KEY

# Docker 本地构建
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build

# 或直接用 Python 3.12 运行（本机需安装 Google Chrome，并在 .env 中设置 BROWSER_CHANNEL=chrome）
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 23988
```

> 调度器与 SQLite 要求单进程单实例运行，不要使用 `--workers` 或启动多个副本。

开发调试工具：`tools/probe.py` 登录后保存订购页 HTML 到 `data/probe/`，用于页面结构变化时排查解析问题。

## 版本发布

版本号只在 [`app/__init__.py`](app/__init__.py) 中维护。修改 `__version__` 并在 `CHANGELOG.md` 记录后，打同名标签推送（`git tag v1.0.1 && git push origin main v1.0.1`），GitHub Actions 自动构建 `amd64 / arm64` 镜像并发布到 `ghcr.io/<owner>/hyonix-monitor`。

更多架构与开发细节见 [DEVELOPMENT.md](DEVELOPMENT.md)，部署细节见 [DEPLOY.md](DEPLOY.md)。
