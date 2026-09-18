"""
IP 归属地离线查询（官方 ip2region xdb v3 格式，IPv4 + IPv6 双栈）
=================================================================

数据文件：
- IPv4：``data/ip2region_v4.xdb``
- IPv6：``data/ip2region_v6.xdb``

查询引擎用的是官方随仓库发布的 Python 绑定（同目录 ``ip2region/``，Apache-2.0，
来源与改动见 ``ip2region/SOURCE.md``）。本文件只做四件事：

1. 把官方返回的 ``国家|省份|城市|ISP|iso`` 串拆成结构化字典；
2. 统一 v4 / v6 入口，并处理 IPv4-mapped-IPv6 这类边界；
3. 提供三种缓存策略（``IPAPI_CACHE``），并保证多线程安全；
4. 暴露数据文件的构建时间，方便 ``/health`` 回答「我在用哪一版数据」。

零第三方依赖，只用标准库。

关于线程安全
------------
官方 ``Searcher`` 在 ``file`` / ``vector_index`` 模式下**共用一个文件句柄**
（``seek`` 之后再 ``read``），多线程并发会互相踩指针、读到错段。
本模块按缓存策略分别处理：

- ``buffer``：整个文件在内存里，搜索过程不碰文件，天然无共享可变状态，不加锁；
- ``vector`` / ``file``：每次查询走文件，用一把 ``Lock`` 把 ``search()`` 串起来。

一次 ``search()`` 在预热后的耗时是微秒级（数据在 page cache 里），
所以在「单核 + 反代」这台机器上加锁不是瓶颈（实测见 README 的性能章节）。
"""
import ipaddress
import os
import threading

from ip2region import searcher as _searcher
from ip2region import util as _util

# 缓存策略：
#   vector（默认）—— 512KB 向量索引常驻内存，其余按需读文件
#   buffer         —— 整个 xdb 读进内存，最快，占用最大
#   file           —— 什么都不常驻，最省内存，每次查询都要读文件
CACHE_MODES = ("vector", "buffer", "file")

_CACHE_ALIASES = {
    "": "vector", "vector": "vector", "index": "vector",
    "vectorindex": "vector", "vector_index": "vector",
    "buffer": "buffer", "memory": "buffer", "content": "buffer",
    "file": "file", "fileonly": "file", "file_only": "file", "none": "file",
}


def normalize_cache(mode):
    """把 ``IPAPI_CACHE`` 的取值归一化成 vector / buffer / file。

    无法识别时返回 ``None``（由调用方决定是报警还是回退）。
    """
    if mode is None:
        return None
    return _CACHE_ALIASES.get(str(mode).strip().lower())


def parse_region(raw):
    """把官方原始串拆成字典。

    v3 格式为 ``国家|省份|城市|ISP|iso-alpha2-code``，例如
    ``中国|浙江省|杭州市|阿里|CN``、``Japan|Tokyo|Tokyo|Amazon|JP``。
    ``0`` 表示该字段没有数据。

    ``region`` 键是为兼容旧调用方而保留的（v3 起没有独立的「区域」字段），
    恒为空字符串；``iso`` 是 v3 新增的 ISO 3166-1 alpha-2 国码。
    """
    parts = raw.split("|")

    def g(i):
        if i >= len(parts):
            return ""
        v = parts[i].strip()
        return "" if v in ("0", "0.0", "-") else v

    return {
        "country": g(0),
        "region": "",
        "province": g(1),
        "city": g(2),
        "isp": g(3),
        "iso": g(4),
        "raw": raw,
    }


class XdbDB:
    """单个 xdb 文件（v4 或 v6）的查询封装。"""

    def __init__(self, path, expect=None, cache="vector"):
        self.path = path
        self.cache = normalize_cache(cache) or "vector"

        if not os.path.exists(path):
            raise FileNotFoundError("数据文件不存在: %s" % path)

        # 先确认结构版本兼容（xdb 结构将来若升级，这里会明确报错而不是查出错结果）
        with open(path, "rb") as h:
            _util.verify(h)
        header = _util.load_header_from_file(path)
        ver = _util.version_from_header(header)
        if ver is None:
            raise ValueError("无法识别的 xdb ipVersion=%r: %s" % (header.ipVersion, path))
        if expect is not None and ver.id != expect.id:
            raise ValueError("数据文件是 %s，但期望 %s: %s" % (ver.name, expect.name, path))

        self.version = ver
        self.created_at = header.createdAt

        if self.cache == "buffer":
            buf = _util.load_content_from_file(path)
            self._s = _searcher.new_with_buffer(ver, buf)
            self._lock = None          # 纯内存，无需加锁
        elif self.cache == "file":
            self._s = _searcher.new_with_file_only(ver, path)
            self._lock = threading.Lock()
        else:
            v_index = _util.load_vector_index_from_file(path)
            self._s = _searcher.new_with_vector_index(ver, path, v_index)
            self._lock = threading.Lock()

    def raw(self, ip):
        """返回官方原始串；查不到或地址不合法时返回空串。"""
        try:
            if self._lock is None:
                return self._s.search(ip)
            with self._lock:
                return self._s.search(ip)
        except Exception:
            return ""

    def search(self, ip):
        s = self.raw(ip)
        return parse_region(s) if s else None

    def close(self):
        try:
            self._s.close()
        except Exception:
            pass


class IPDB:
    """统一查询入口：自动区分 v4 / v6，统一返回结构化字典。"""

    def __init__(self, v4_path=None, v6_path=None, cache=None):
        self.v4_path = v4_path
        self.v6_path = v6_path

        wanted = normalize_cache(cache if cache is not None
                                 else os.environ.get("IPAPI_CACHE"))
        self.cache = wanted or "vector"
        self.cache_fallback = (wanted is None
                               and (cache is not None or os.environ.get("IPAPI_CACHE")))

        self.v4 = None
        self.v6 = None
        self.problems = []
        self._lock = threading.Lock()

        if v4_path:
            try:
                self.v4 = XdbDB(v4_path, _util.IPv4, self.cache)
            except Exception as e:
                self.problems.append("IPv4: %s" % e)
        if v6_path:
            try:
                self.v6 = XdbDB(v6_path, _util.IPv6, self.cache)
            except Exception as e:
                self.problems.append("IPv6: %s" % e)

        if not self.v4 and not self.v6:
            raise ValueError(
                "没有任何可用数据文件，请先运行: bash scripts/update_db.sh"
                + ("\n  " + "\n  ".join(self.problems) if self.problems else "")
            )

    # ---------- 查询 ----------

    def lookup(self, ip):
        """查一个 IP。返回字典（含 ip / version），查不到返回 ``None``。"""
        ip = (ip or "").strip()
        if "/" in ip:                       # 容忍 1.2.3.0/24 这种写法，取网络号
            ip = ip.split("/")[0]
        if not ip:
            return None
        try:
            obj = ipaddress.ip_address(ip)
        except ValueError:
            return None

        # IPv4-mapped 的 IPv6（::ffff:8.8.8.8）语义上就是 IPv4，交给 v4 库查，
        # 但 version 照实报 6 —— 它与 ::ffff:0:0/96 之外的 v6 地址不是一回事。
        return self._lookup(ip, obj)

    def _lookup(self, ip, obj):
        if obj.version == 4:
            r = self.v4.search(ip) if self.v4 else None
        else:
            mapped = obj.ipv4_mapped
            if mapped is not None and self.v4:
                r = self.v4.search(str(mapped))
            else:
                r = self.v6.search(ip) if self.v6 else None
        if r:
            r["ip"] = ip
            r["version"] = obj.version
        return r

    # ---------- 维护 ----------

    def info(self):
        """给 /health 用：数据版本与缓存策略。"""
        def one(db):
            if not db:
                return None
            return {"file": os.path.basename(db.path),
                    "built_at": db.created_at,
                    "cache": db.cache}
        return {"cache": self.cache, "ipv4": one(self.v4), "ipv6": one(self.v6)}

    def reload(self):
        """重新加载全部数据（v4 + v6）。

        注意：调用方需自己保证线程安全 —— 重建期间旧实例仍在被其他线程读取，
        所以不要原地改 ``self.v4``/``self.v6``，而是构建一个新的 ``IPDB``
        再整体替换引用（参考 ``app/server.py`` 的 ``maybe_reload``）。

        这个方法保留是为了兼容旧调用方；实际推荐用新实例替换。
        """
        with self._lock:
            if not self.v4_path and not self.v6_path:
                raise ValueError("没有可重载的数据文件路径")
            v4 = XdbDB(self.v4_path, _util.IPv4, self.cache) if self.v4_path else None
            v6 = XdbDB(self.v6_path, _util.IPv6, self.cache) if self.v6_path else None
            self.v4, self.v6 = v4, v6
