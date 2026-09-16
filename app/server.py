#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
IP 归属地查询 API 服务（零第三方依赖）
仅用 Python 标准库，内存占用极低，适合 1核512M 的小机器。

启动：
    python3 app/server.py --port 8080
"""
import argparse
import hmac
import ipaddress
import json
import logging
import os
import sys
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ipdb import IPDB  # noqa: E402

VERSION = "1.1.0"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")

V4_PATH = os.path.join(DATA_DIR, "ip2region.db")
V6_PATH = os.path.join(DATA_DIR, "ipv6wry.db")

# ---------- 配置（可用环境变量覆盖） ----------
API_KEY = os.environ.get("IPAPI_KEY", "").strip()           # 空 = 不校验
RATE_LIMIT = int(os.environ.get("IPAPI_RATE_LIMIT", "60"))  # 每分钟每IP请求数，0=关闭
RELOAD_INTERVAL = int(os.environ.get("IPAPI_RELOAD_CHECK", "60"))  # 秒；<=0 关闭热重载检查

# IPAPI_TRUST_PROXY 三档：
#   0 / false / off  不信任任何转发头（服务直接对外暴露时用）
#   1 / true         信任转发头，但**仅当对端是本机 / 内网地址**（默认；
#                    能挡住"客户端自己伪造 X-Real-IP/XFF"的情况）
#   2 / force        无条件信任（反向代理在另一台机器上、以公网 IP 连过来时用）
_TRUST = os.environ.get("IPAPI_TRUST_PROXY", "1").strip().lower()
TRUST_PROXY = _TRUST not in ("0", "false", "no", "off")
TRUST_PROXY_ALWAYS = _TRUST in ("2", "force", "always", "any")

log = logging.getLogger("ipapi")

# ---------- 数据库（惰性加载 + 热重载） ----------
_db = None
_db_lock = threading.Lock()
_db_mtime = 0


def _data_paths():
    return (V4_PATH if os.path.exists(V4_PATH) else None,
            V6_PATH if os.path.exists(V6_PATH) else None)


def _max_mtime(paths):
    stamps = [os.path.getmtime(p) for p in paths if p]
    return max(stamps) if stamps else 0


def get_db():
    """首次调用时加载数据库；之后直接返回缓存。"""
    global _db, _db_mtime
    if _db is None:
        with _db_lock:
            if _db is None:                    # 锁内二次检查，避免并发重复加载
                v4, v6 = _data_paths()
                if not v4 and not v6:
                    raise RuntimeError("未找到任何数据库文件，请放到 data/ 目录")
                _db = IPDB(v4, v6)
                _db_mtime = _max_mtime((v4, v6))
                log.info("数据库加载完成 v4=%s v6=%s", bool(v4), bool(v6))
    return _db


def maybe_reload():
    """数据文件被替换后自动重载（用于定时更新）。"""
    global _db, _db_mtime
    v4, v6 = _data_paths()
    if not v4 and not v6:
        return
    cur = _max_mtime((v4, v6))
    if cur <= _db_mtime + 1:
        return
    with _db_lock:
        # 锁内复查：并发请求同时发现变化时，只让第一个真正加载。
        # 否则每个线程都会 new 一份完整数据库，小内存机器会瞬间吃紧。
        if cur <= _db_mtime + 1:
            return
        _db = IPDB(v4, v6)
        _db_mtime = cur
        log.info("检测到数据更新，已重载")


# ---------- 简单内存限流 ----------
class RateLimiter:
    def __init__(self, limit_per_min):
        self.limit = limit_per_min
        self.buckets = {}
        self.lock = threading.Lock()

    def allow(self, key):
        if self.limit <= 0:
            return True
        now = time.time()
        win = int(now // 60)
        with self.lock:
            k = (key, win)
            cnt = self.buckets.get(k, 0) + 1
            self.buckets[k] = cnt
            if len(self.buckets) > 20000:  # 防止无限增长
                self.buckets = {kk: v for kk, v in self.buckets.items() if kk[1] >= win}
            return cnt <= self.limit


limiter = RateLimiter(RATE_LIMIT)

_last_reload_check = 0.0
_reload_check_lock = threading.Lock()


def maybe_reload_throttled():
    """把热重载检查限流到每 RELOAD_INTERVAL 秒一次，避免每个请求都 stat 文件。"""
    global _last_reload_check
    if RELOAD_INTERVAL <= 0:
        return
    now = time.time()
    if now - _last_reload_check < RELOAD_INTERVAL:
        return
    with _reload_check_lock:
        if now - _last_reload_check < RELOAD_INTERVAL:
            return
        _last_reload_check = now
        try:
            maybe_reload()
        except Exception as e:  # 数据文件损坏时不要让请求挂掉，保留旧库继续服务
            log.warning("热重载失败，继续使用旧数据: %s", e)


class Handler(BaseHTTPRequestHandler):
    server_version = "ipapi/" + VERSION
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True  # 关闭 Nagle，降低延迟

    def log_message(self, fmt, *args):
        log.debug("%s - %s", self.address_string(), fmt % args)

    # ---- 工具 ----
    def _send(self, code, payload, ctype="application/json; charset=utf-8"):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _peer_is_local(self):
        """TCP 对端是不是本机 / 内网地址（即「有可能是个反向代理」）。"""
        ip = self.client_address[0]
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        if addr.version == 6 and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        return addr.is_loopback or addr.is_private

    def client_ip(self):
        """
        取请求方的真实 IP，用于限流和 /me。

        ⚠️ 两点都别改：

        1. **顺序**：优先 `X-Real-IP`（反向代理用 `$remote_addr` 设置，客户端伪造不了），
           再退到 `X-Forwarded-For`，且取**最后一段**（最靠近本机的那一跳），
           最后才用 TCP 对端地址。
           常见错误是取 XFF 的**第一段**：nginx 的 `$proxy_add_x_forwarded_for` 是
           「追加」语义，客户端自己发的值会排在最前面，
           于是限流 key 完全由请求方控制，换个假头就能无限刷。

        2. **只在对端是本机 / 内网时才信这些头**（`IPAPI_TRUST_PROXY=1` 的默认行为）。
           否则服务直接对外暴露时，任何人都能自己编一个 IP 绕过限流。
           前置代理在另一台机器上时用 `IPAPI_TRUST_PROXY=2`。
        """
        if TRUST_PROXY and (TRUST_PROXY_ALWAYS or self._peer_is_local()):
            real = self.headers.get("X-Real-IP")
            if real:
                return real.strip()
            xff = self.headers.get("X-Forwarded-For")
            if xff:
                return xff.split(",")[-1].strip()
        return self.client_address[0]

    def check_key(self, qs):
        if not API_KEY:
            return True
        given = (qs.get("key", [""])[0]
                 or self.headers.get("X-API-Key", "")
                 or "")
        return hmac.compare_digest(given, API_KEY)

    # ---- 路由 ----
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path in ("/", "/help"):
            return self._send(200, {
                "service": "IP 归属地查询 API",
                "version": VERSION,
                "endpoints": {
                    "GET /ip?ip=<地址>": "查询指定 IP（支持 v4/v6）",
                    "GET /me": "查询请求方自己的公网 IP",
                    "GET /batch?ips=a,b,c": "批量查询（最多 50 个）",
                    "GET /health": "健康检查",
                },
                "example": "/ip?ip=114.114.114.114",
            })

        if path == "/health":
            try:
                db = get_db()
                return self._send(200, {
                    "status": "ok",
                    "version": VERSION,
                    "ipv4": bool(db.v4), "ipv6": bool(db.v6),
                    "mtime": int(_db_mtime),
                })
            except Exception as e:
                return self._send(503, {"status": "error", "msg": str(e)})

        if not self.check_key(qs):
            return self._send(401, {"code": 401, "msg": "invalid api key"})

        cip = self.client_ip()
        if not limiter.allow(cip):
            return self._send(429, {"code": 429, "msg": "rate limit exceeded"})

        maybe_reload_throttled()

        if path == "/me":
            try:
                db = get_db()
            except Exception as e:
                return self._send(503, {"code": 503, "msg": str(e)})
            r = db.lookup(cip)
            if r is None:
                return self._send(200, {"ip": cip, "version": 0})
            return self._send(200, r)

        if path == "/ip":
            ip = qs.get("ip", [""])[0].strip()
            if not ip:
                return self._send(400, {"code": 400, "msg": "缺少参数 ip"})
            try:
                db = get_db()
            except Exception as e:
                return self._send(503, {"code": 503, "msg": str(e)})
            r = db.lookup(ip)
            if r is None:
                return self._send(404, {"code": 404, "ip": ip, "msg": "无效的 IP 或未收录"})
            return self._send(200, r)

        if path == "/batch":
            raw = qs.get("ips", [""])[0].strip()
            if not raw:
                return self._send(400, {"code": 400, "msg": "缺少参数 ips"})
            items = [x.strip() for x in raw.split(",") if x.strip()][:50]
            try:
                db = get_db()
            except Exception as e:
                return self._send(503, {"code": 503, "msg": str(e)})
            out = []
            for it in items:
                r = db.lookup(it)
                out.append(r if r else {"ip": it, "error": "无效或未收录"})
            return self._send(200, {"count": len(out), "results": out})

        return self._send(404, {"code": 404, "msg": "not found"})

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    # socketserver 默认只排 5 个连接。反向代理默认「每个请求新开一条到上游的 TCP 连接」，
    # 高并发下默认队列会被打满 → 内核丢 SYN → 客户端等约 1 秒后重传。
    # 实测：队列为 5 时约 15% 的新连接会卡 ~1s；调到 128 后消失。
    request_queue_size = 128


def main():
    ap = argparse.ArgumentParser(description="IP 归属地查询 API（离线 · 零依赖）")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--version", action="version", version="ipapi " + VERSION)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    try:
        get_db()  # 启动时就加载，避免首个请求变慢
    except Exception as e:
        log.error("启动失败：%s", e)
        log.error("请先准备数据库：bash scripts/update_db.sh")
        log.error("（数据文件不随仓库分发，原因见 README「数据来源与许可」）")
        sys.exit(1)

    srv = Server((args.host, args.port), Handler)
    log.info("ipapi %s 已启动: http://%s:%d  (key=%s, limit=%d/min)",
             VERSION, args.host, args.port, "on" if API_KEY else "off", RATE_LIMIT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log.info("正在退出...")
        srv.shutdown()


if __name__ == "__main__":
    main()
