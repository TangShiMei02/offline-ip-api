#!/usr/bin/env bash
# 服务管理 / 离线启动（不依赖 systemd，适合 LXC / 宝塔 / 共享环境）
#
# 用法: bash scripts/ctl.sh {start|stop|restart|status|log}
#
# 说明：pid / 日志默认写在运行时目录，**不写进项目目录**——
# 项目目录在很多部署方式下是不可写的（例如属主是 www 而你是普通用户），
# 写进去会导致"启动失败却看不到原因"。
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8080}"
RUNDIR="${IPAPI_RUNTIME_DIR:-/tmp}"
PIDFILE="$RUNDIR/ipapi.pid"
LOGFILE="$RUNDIR/ipapi.log"
PYTHON="${PYTHON:-$(command -v python3 || true)}"

if [ -z "$PYTHON" ]; then
    echo "[!] 未找到 python3"
    exit 1
fi

start() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "[=] 已在运行 (pid $(cat "$PIDFILE"))"
        return 0
    fi
    cd "$BASE_DIR"
    nohup "$PYTHON" "$BASE_DIR/app/server.py" --port "$PORT" > "$LOGFILE" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 1
    if kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "[+] 已启动 (pid $(cat "$PIDFILE")), 端口 $PORT, 运行身份 $(id -un)"
        echo "    日志: $LOGFILE"
    else
        echo "[!] 启动失败，日志末尾："
        tail -20 "$LOGFILE" || true
        rm -f "$PIDFILE"
        exit 1
    fi
}

stop() {
    if [ -f "$PIDFILE" ]; then
        PID="$(cat "$PIDFILE")"
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            sleep 1
            kill -0 "$PID" 2>/dev/null && kill -9 "$PID" 2>/dev/null || true
            echo "[+] 已停止 (pid $PID)"
        fi
        rm -f "$PIDFILE"
    else
        echo "[=] 未运行"
    fi
}

status() {
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        PID="$(cat "$PIDFILE")"
        echo "[+] 运行中 (pid $PID)"
        ps -o pid,rss,etime,cmd -p "$PID" 2>/dev/null || true
        echo "--- 健康检查 ---"
        curl -s -m 5 "http://127.0.0.1:$PORT/health" || echo "(无响应)"
        echo
    else
        echo "[=] 未运行"
    fi
}

case "${1:-}" in
    start)   start ;;
    stop)    stop ;;
    restart) stop; sleep 1; start ;;
    status)  status ;;
    log)     tail -f "$LOGFILE" ;;
    *)       echo "用法: $0 {start|stop|restart|status|log}"; exit 1 ;;
esac
