#!/usr/bin/env bash
# 一键部署脚本（systemd / 宝塔面板 / 通用 Linux）
#
# 用法：
#   sudo bash scripts/deploy.sh
#   sudo IPAPI_KEY=你的密钥 IPAPI_RATE_LIMIT=60 bash scripts/deploy.sh
#   sudo PORT=9000 bash scripts/deploy.sh
#
# 不想用 systemd 的话见 scripts/ctl.sh（裸后台进程）。
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8080}"
KEY="${IPAPI_KEY:-}"
RATE_LIMIT="${IPAPI_RATE_LIMIT:-60}"
PYTHON="${PYTHON:-$(command -v python3 || true)}"

echo "======================================"
echo " IP 归属地查询 API —— 部署"
echo " 目录  : $BASE_DIR"
echo " 端口  : $PORT"
echo " Python: ${PYTHON:-(未找到)}"
echo " 限流  : $RATE_LIMIT 次/分钟/IP"
echo " API Key: ${KEY:-未设置（谁都能调，仅靠限流保护）}"
echo "======================================"

if [ "$(id -u)" != "0" ]; then
    echo "[!] 需要 root 权限（要写 /etc/systemd/system/）。"
    echo "    请用： sudo bash scripts/deploy.sh"
    echo "    或者不使用 systemd，改用： bash scripts/ctl.sh start"
    exit 1
fi

# 1. 检查 python3
if [ -z "$PYTHON" ]; then
    echo "[!] 未找到 python3，请先安装"
    exit 1
fi
echo "[+] Python: $("$PYTHON" -c 'import sys;print("%d.%d.%d"%sys.version_info[:3])')"

# 2. 检查数据文件（IPv4 必须有；IPv6 可选，缺了只是 v6 查不了）
if [ ! -f "$BASE_DIR/data/ip2region_v4.xdb" ]; then
    echo "[!] 缺少 IPv4 数据文件 data/ip2region_v4.xdb"
    echo "    请先运行： bash scripts/update_db.sh"
    echo "    注意：数据文件因体积与授权原因不入仓库，见 README「数据来源与许可」。"
    exit 1
fi
if [ ! -f "$BASE_DIR/data/ip2region_v6.xdb" ]; then
    echo "[i] 没有 IPv6 数据文件 data/ip2region_v6.xdb —— IPv6 查询会返回「无效或未收录」"
    echo "    需要的话： bash scripts/update_db.sh v6"
fi
echo "[+] 数据文件就绪"
ls -lh "$BASE_DIR/data"/*.xdb | sed 's/^/    /'

# 3. 给脚本加执行权限
chmod +x "$BASE_DIR/scripts/"*.sh 2>/dev/null || true

# 4. 选一个运行身份：优先 www（宝塔 / 常见 web 用户），否则 root
RUN_USER="www"
if ! id "$RUN_USER" >/dev/null 2>&1; then
    RUN_USER="root"
fi
# 该用户得能读到项目目录，否则服务起不来
if [ "$RUN_USER" != "root" ] && ! su -s /bin/sh "$RUN_USER" -c "test -r '$BASE_DIR/app/server.py'" 2>/dev/null; then
    echo "[i] $RUN_USER 读不到项目目录，改用 root 运行（可自行调整 deploy/ipapi.service 的 User=）"
    RUN_USER="root"
fi
echo "[+] 运行身份: $RUN_USER"

# 5. 安装 systemd 服务（如果可用）
HAS_SYSTEMD=0
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    HAS_SYSTEMD=1
fi

if [ "$HAS_SYSTEMD" = "1" ]; then
    echo "[+] 安装 systemd 服务..."
    # 用占位符替换，而不是去匹配 "IPAPI_KEY=\"\"" 这种脆弱的模式
    sed -e "s|__BASE_DIR__|$BASE_DIR|g" \
        -e "s|__PORT__|$PORT|g" \
        -e "s|__PYTHON__|$PYTHON|g" \
        -e "s|__RUN_USER__|$RUN_USER|g" \
        -e "s|__IPAPI_KEY__|$KEY|g" \
        -e "s|__RATE_LIMIT__|$RATE_LIMIT|g" \
        "$BASE_DIR/deploy/ipapi.service" > /etc/systemd/system/ipapi.service

    # 自检：确认环境变量真的写进去了（避免"静默失效"）
    if ! grep -qxF "Environment=\"IPAPI_KEY=$KEY\"" /etc/systemd/system/ipapi.service; then
        echo "[!] 生成的服务文件里 IPAPI_KEY 不对，已中止。内容如下："
        grep -n "Environment" /etc/systemd/system/ipapi.service
        exit 1
    fi
    echo "[+] 已写入 /etc/systemd/system/ipapi.service"
    grep -n "Environment=" /etc/systemd/system/ipapi.service | sed 's/^/    /'

    systemctl daemon-reload
    systemctl enable ipapi >/dev/null 2>&1 || true
    systemctl restart ipapi
    sleep 1
    if systemctl is-active --quiet ipapi; then
        echo "[+] 服务已启动 (systemd)"
    else
        echo "[!] systemd 启动失败，改用后台进程方式..."
        PORT="$PORT" bash "$BASE_DIR/scripts/ctl.sh" start
    fi
else
    echo "[i] 未检测到运行中的 systemd，使用后台进程方式启动"
    PORT="$PORT" IPAPI_KEY="$KEY" IPAPI_RATE_LIMIT="$RATE_LIMIT" \
        bash "$BASE_DIR/scripts/ctl.sh" start
fi

# 6. 自测
echo ""
echo "[*] 自测..."
sleep 1
curl -s -m 5 "http://127.0.0.1:$PORT/health" && echo
curl -s -m 5 "http://127.0.0.1:$PORT/ip?ip=114.114.114.114${KEY:+&key=$KEY}" && echo

if [ -n "$KEY" ]; then
    code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/ip?ip=8.8.8.8")
    if [ "$code" = "401" ]; then
        echo "[+] API Key 生效（不带 key 返回 401）"
    else
        echo "[!] 警告：不带 key 却返回 $code，鉴权可能没生效！"
    fi
fi

echo ""
echo "======================================"
echo " 部署完成"
echo " 本地测试: curl 'http://127.0.0.1:$PORT/ip?ip=8.8.8.8'"
echo " 对外访问: 请在 Web 服务器里把域名反向代理到 127.0.0.1:$PORT"
echo "           （nginx 配置示例见 deploy/nginx.conf.example）"
echo " ⚠️ 反代时一定要注意真实 IP 的传递，否则限流形同虚设："
echo "    见部署文档「反向代理与真实 IP」一节"
echo "======================================"
