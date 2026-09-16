"""
纯 Python 实现的 IP 归属地离线查询（纯真 IP 库格式）
- 支持 IPv4：ip2region.db（xdb 格式，来自 ip2region 项目）
- 支持 IPv6：ipv6wry.db（IPDB 格式，来自 ip2region npm 包 / 纯真 IPv6 库）

零第三方依赖，仅用标准库。启动时全部读入内存。
"""
import ipaddress
import struct
import threading


class Ipv4DB:
    """ip2region xdb (IPv4) 解析"""

    INDEX_BLOCK_LEN = 12

    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        self.first_index_ptr = struct.unpack_from("<I", self.data, 0)[0]
        self.last_index_ptr = struct.unpack_from("<I", self.data, 4)[0]
        self.total_blocks = (self.last_index_ptr - self.first_index_ptr) // self.INDEX_BLOCK_LEN + 1
        if self.total_blocks <= 0:
            raise ValueError("invalid xdb file")

    @staticmethod
    def ip_to_long(ip):
        return struct.unpack(">I", ipaddress.IPv4Address(ip).packed)[0]

    def _search_long(self, ip_long):
        low, high = 0, self.total_blocks
        while low <= high:
            mid = (low + high) >> 1
            pos = self.first_index_ptr + mid * self.INDEX_BLOCK_LEN
            sip = struct.unpack_from("<I", self.data, pos)[0]
            if ip_long < sip:
                high = mid - 1
            else:
                eip = struct.unpack_from("<I", self.data, pos + 4)[0]
                if ip_long > eip:
                    low = mid + 1
                else:
                    data_pos = struct.unpack_from("<I", self.data, pos + 8)[0]
                    if data_pos == 0:
                        return None
                    data_len = (data_pos >> 24) & 0xFF
                    data_pos &= 0x00FFFFFF
                    region = self.data[data_pos + 4: data_pos + data_len].decode("utf-8", "ignore")
                    return region.split("|")
        return None

    def search(self, ip):
        try:
            ip_long = self.ip_to_long(ip)
        except Exception:
            return None
        parts = self._search_long(ip_long)
        if not parts:
            return None
        # 国家|区域|省份|城市|ISP
        def g(i):
            return parts[i] if i < len(parts) and parts[i] != "0" else ""
        return {
            "country": g(0), "region": g(1), "province": g(2),
            "city": g(3), "isp": g(4),
            "raw": "|".join(parts),
        }


class Ipv6DB:
    """纯真 IPv6 库 (IPDB) 解析"""

    def __init__(self, path, ipv4=None):
        with open(path, "rb") as f:
            self.data = f.read()
        if self.data[0:4] != b"IPDB":
            raise ValueError("not an IPDB file")
        self.offlen = struct.unpack_from("<b", self.data, 6)[0]
        self.record = struct.unpack_from("<q", self.data, 8)[0]
        self.index_start = struct.unpack_from("<q", self.data, 16)[0]
        self.ipv4 = ipv4

    def _read_long(self, offset):
        b = self.data[offset:offset + self.offlen]
        return int.from_bytes(b, "little", signed=False)

    def _get_string(self, offset):
        end = self.data.find(b"\x00", offset)
        if end < 0:
            end = len(self.data)
        return self.data[offset:end].decode("utf-8", "ignore")

    def _get_area_addr(self, offset):
        byte = struct.unpack_from("<b", self.data, offset)[0]
        if byte in (1, 2):
            p = self._read_long(offset + 1)
            return self._get_area_addr(p)
        return self._get_string(offset)

    def _get_addr(self, offset):
        o = offset
        byte = struct.unpack_from("<b", self.data, o)[0]
        if byte == 1:
            return self._get_addr(self._read_long(o + 1))
        c_area = self._get_area_addr(o)
        if byte == 2:
            o += 1 + self.offlen
        else:
            o = self.data.find(b"\x00", o) + 1
        a_area = self._get_area_addr(o)
        return {"cArea": c_area.replace(" ", ""), "aArea": a_area}

    def _find(self, ip, l, r):
        while r - l > 1:
            m = (l + r) >> 1
            o = self.index_start + m * (8 + self.offlen)
            new_ip = struct.unpack_from("<Q", self.data, o)[0]
            if ip < new_ip:
                r = m
            else:
                l = m
        return l

    def _search_long(self, ip6):
        if ip6 == 1:
            return {"cArea": "IANA保留地址", "aArea": "本机地址"}
        ip = (ip6 >> 64) & 0xFFFFFFFFFFFFFFFF
        if ip == 0:
            realip = ip6 & 0xFFFFFFFF
            return self._search_ipv4(realip)
        if ((ip >> 48) & 0xFFFF) == 0x2002:
            realip = (ip & 0x0000FFFFFFFF0000) >> 16
            return self._search_ipv4(realip)
        if ((ip >> 32) & 0xFFFFFFFF) == 0x20010000:
            realip = (~ip6) & 0xFFFFFFFF
            return self._search_ipv4(realip)
        if ((ip6 >> 32) & 0xFFFF) == 0x5EFE:
            realip = ip6 & 0xFFFFFFFF
            return self._search_ipv4(realip)
        idx = self._find(ip, 0, self.record)
        ip_off = self.index_start + idx * (8 + self.offlen)
        ip_rec_off = self._read_long(ip_off + 8)
        return self._get_addr(ip_rec_off)

    def _search_ipv4(self, realip):
        if not self.ipv4:
            return {"cArea": "未知", "aArea": "未知"}
        parts = self.ipv4._search_long(int(realip))
        if not parts:
            return {"cArea": "未知", "aArea": "未知"}
        return {"cArea": "|".join(parts), "aArea": parts[4] if len(parts) > 4 else ""}

    def search(self, ip):
        try:
            if "/" in ip:
                ip = ip.split("/")[0]
            num = int(ipaddress.IPv6Address(ip))
        except Exception:
            return None
        ret = self._search_long(num)
        if not ret:
            return None
        if "city" in ret or ("country" in ret):
            return ret
        c_area = ret.get("cArea", "")
        # 从 cArea 提取 国家/省/市
        country = province = city = ""
        first = c_area.find("国")
        if first == 1:
            first += 1
            country = c_area[:first]
        else:
            first = 0
        second = c_area.find("省")
        if second >= 0:
            second += 1
            province = c_area[first:second]
        else:
            for p in ("内蒙古", "广西", "西藏", "宁夏", "新疆"):
                i = c_area.find(p)
                if i >= 0:
                    second = i + len(p)
                    province = c_area[first:second]
                    break
            else:
                second = first
        city1 = c_area.find("市")
        city2 = c_area.find("州")
        if city1 >= 0 and second < city1:
            city = c_area[second:city1 + 1]
        elif city2 >= 0 and second < city2:
            city = c_area[second:city2 + 1]
        return {
            "country": country, "province": province.strip(),
            "city": city.strip(), "isp": ret.get("aArea", ""),
            "raw": f"{c_area}|{ret.get('aArea','')}",
        }


class IPDB:
    """统一查询入口，自动区分 v4/v6"""

    def __init__(self, v4_path=None, v6_path=None):
        self.v4 = None
        self.v6 = None
        self.v4_path = v4_path
        self.v6_path = v6_path
        self._lock = threading.Lock()
        if v4_path:
            self.v4 = Ipv4DB(v4_path)
        if v6_path:
            self.v6 = Ipv6DB(v6_path, ipv4=self.v4)
        if not self.v4 and not self.v6:
            raise ValueError("at least one database required")

    def lookup(self, ip):
        ip = ip.strip()
        try:
            obj = ipaddress.ip_address(ip)
        except ValueError:
            return None
        if obj.version == 4:
            if not self.v4:
                return None
            r = self.v4.search(ip)
            if r:
                r["ip"] = ip
                r["version"] = 4
            return r
        else:
            if not self.v6:
                return None
            r = self.v6.search(ip)
            if r:
                r["ip"] = ip
                r["version"] = 6
            return r

    def reload(self):
        """重新加载全部数据库（v4 + v6）。

        注意：调用方需要自己保证线程安全——重建期间旧实例仍在被其他线程读取，
        所以不要在原地改 self.v4/self.v6，而是构建一个新 IPDB 再整体替换引用
        （参考 app/server.py 的 maybe_reload()）。
        """
        with self._lock:
            if not self.v4_path and not self.v6_path:
                raise ValueError("no database path to reload from")
            v4 = Ipv4DB(self.v4_path) if self.v4_path else None
            v6 = Ipv6DB(self.v6_path, ipv4=v4) if self.v6_path else None
            self.v4, self.v6 = v4, v6
