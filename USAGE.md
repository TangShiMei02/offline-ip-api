# 怎么用这个 API

服务地址（本文示例统一用这个，换成你自己的域名即可）：

```
https://ip.guancii.cc.cd
```

---

## 0. 先在浏览器里试一下

不用装任何工具，地址栏直接打开：

```
https://ip.guancii.cc.cd/ip?ip=223.5.5.5
https://ip.guancii.cc.cd/me
https://ip.guancii.cc.cd/health
```

---

## 1. 四个接口

| 接口 | 作用 | 说明 |
|---|---|---|
| `GET /ip?ip=<地址>` | 查指定 IP | 支持 IPv4 / IPv6 |
| `GET /me` | 查**调用方自己**的 IP 归属地 | 见下面的「重要语义」 |
| `GET /batch?ips=a,b,c` | 批量查询 | 最多 50 个，逗号分隔 |
| `GET /health` | 健康检查 | 返回版本号和数据库状态 |

### ⚠️ `/me` 的重要语义：谁调它，它返回谁

- **浏览器里调** → 返回**访客**的 IP 归属地（拿来做「你在哪里」正合适）
- **你自己的服务器上调** → 返回**你服务器**的 IP 归属地，不是访客的

所以要让服务端知道访客位置，得先用访客 IP 再查：

```php
$visitorIp = $_SERVER['REMOTE_ADDR'];          // 详见第 5 节「拿访客 IP」
$geo = ip_lookup($visitorIp);                   // 再调 /ip?ip=<访客IP>
```

### 返回字段

```json
{
  "country": "中国",
  "region": "",
  "province": "浙江省",
  "city": "杭州市",
  "isp": "阿里云",
  "raw": "中国|0|浙江省|杭州市|阿里云",
  "ip": "223.5.5.5",
  "version": 4
}
```

| 字段 | 说明 |
|---|---|
| `country` / `province` / `city` / `isp` | 归属地信息，取不到时是空字符串 |
| `region` | 多数数据库都为空，可以直接忽略 |
| `version` | `4` 或 `6` |
| `raw` | 数据库原始串，调试用 |
| `ip` | 回显你查的那个 IP |

### 状态码

| 码 | 含义 | 你该怎么处理 |
|---|---|---|
| 200 | 成功 | 正常解析 |
| 400 | 缺参数 | 说明调用写错了 |
| 404 | **IP 无效或未收录** | ⚠️ 这是**正常业务码**，响应的 body 仍然是 JSON |
| 429 | 触发限流 | 降级显示「暂时不可用」，别让页面崩掉 |
| 503 | 数据库未加载 | 服务端问题，重试或报错 |

---

## 2. 浏览器 / 前端（原生 JS）

接口已开 CORS（`Access-Control-Allow-Origin: *`），任何域名都能直接调，不需要后端中转。

```js
const IPAPI = 'https://ip.guancii.cc.cd';

/**
 * 查 IP 归属地。不传 ip 则查「当前访客自己」。
 * @returns {Promise<object|null>} 查不到（无效/未收录）时返回 null
 */
async function lookupIP(ip) {
  const url = ip ? `${IPAPI}/ip?ip=${encodeURIComponent(ip)}` : `${IPAPI}/me`;
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 5000);   // 一定要设超时，别拖死页面
  try {
    const r = await fetch(url, { signal: ctl.signal });

    let data;
    try {
      data = await r.json();
    } catch (e) {
      // 返回的不是 JSON —— 一般是反向代理把错误页换成了 HTML
      throw new Error(`HTTP ${r.status}，返回的不是 JSON`);
    }

    if (r.status === 404) return null;                 // 无效 IP，属正常情况
    if (r.status === 429) throw new Error('查询太频繁了，稍后再试');
    if (!r.ok) throw new Error(data.msg || `HTTP ${r.status}`);
    return data;
  } finally {
    clearTimeout(timer);
  }
}

// ── 用法一：显示「你在哪里」（浏览器直接调 /me，拿到的就是访客的 IP）──
lookupIP().then(d => {
  if (d) document.querySelector('#where').textContent =
    `${d.country} ${d.province} ${d.city} ${d.isp}`.trim() || '未知';
}).catch(() => { /* 静默失败，别影响页面 */ });

// ── 用法二：查指定 IP ──
lookupIP('8.8.8.8').then(d => console.log(d.country, d.isp));
```

---

## 3. Python

零依赖（标准库）：

```python
import json
import urllib.error
import urllib.parse
import urllib.request

IPAPI = "https://ip.guancii.cc.cd"


def lookup_ip(ip=None, timeout=5):
    """查 IP 归属地。ip 为 None 时查本机出口 IP。
    返回 dict；IP 无效或未收录返回 None。"""
    path = "/ip?ip=" + urllib.parse.quote(ip) if ip else "/me"
    try:
        with urllib.request.urlopen(IPAPI + path, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 429:
            raise RuntimeError("触发限流，稍后再试")
        raise


if __name__ == "__main__":
    d = lookup_ip("8.8.8.8")
    print(d["country"], d["isp"])        # 美国 Level3

    print(lookup_ip("223.5.5.5")["city"])        # 杭州市
    print(lookup_ip("999.1.1.1"))                # None

    # 批量（最多 50 个）
    with urllib.request.urlopen(IPAPI + "/batch?ips=8.8.8.8,114.114.114.114", timeout=5) as r:
        for item in json.load(r)["results"]:
            print(item.get("ip"), item.get("province") or item.get("error"))
```

用了 `requests` 的话：

```python
import requests

r = requests.get("https://ip.guancii.cc.cd/ip", params={"ip": "1.1.1.1"}, timeout=5)
geo = r.json() if r.status_code == 200 else None
```

---

## 4. PHP

```php
<?php
/**
 * 查 IP 归属地。返回数组；IP 无效 / 未收录 / 请求失败都返回 null。
 */
function ip_lookup(string $ip, int $timeout = 5): ?array
{
    $url = 'https://ip.guancii.cc.cd/ip?ip=' . rawurlencode($ip);

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT        => $timeout,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_HTTPHEADER     => ['Accept: application/json'],
    ]);
    $body = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($body === false || $code !== 200) {
        return null;          // 404（未收录）/ 429（限流）/ 网络问题，一律当「查不到」
    }
    $data = json_decode($body, true);
    return is_array($data) ? $data : null;
}

// ── 用法：显示访客归属地 ──
$visitorIp = $_SERVER['REMOTE_ADDR'] ?? '';
$geo = ip_lookup($visitorIp);

echo $geo
    ? htmlspecialchars("{$geo['province']}{$geo['city']} {$geo['isp']}", ENT_QUOTES, 'UTF-8')
    : '未知';
```

> **关于拿访客 IP**：上面用的是 `$_SERVER['REMOTE_ADDR']`，它由 Web 服务器填，
> **客户端伪造不了**，是默认的正确选择。
> 只有当你的站前面真的挂了 CDN / 反代时，才需要看 `X-Forwarded-For`，
> 而且那个头是**可以被伪造的**——要用的话请确保前面的代理用覆盖而非追加的方式写入，
> 或者只信任 CDN 的官方网段（参考 `deploy/nginx.conf.example` 里的说明）。

主机没有 curl 扩展时，可以用：

```php
$geo = @json_decode(@file_get_contents(
    'https://ip.guancii.cc.cd/ip?ip=' . rawurlencode($ip),
    false,
    stream_context_create(['http' => ['timeout' => 5]])
), true);
```

---

## 5. 命令行 / 脚本

```bash
# 单查
curl -s 'https://ip.guancii.cc.cd/ip?ip=8.8.8.8'

# 好看一点
curl -s 'https://ip.guancii.cc.cd/ip?ip=8.8.8.8' | python3 -m json.tool

# 只要城市
curl -s 'https://ip.guancii.cc.cd/ip?ip=223.5.5.5' | python3 -c 'import json,sys;print(json.load(sys.stdin)["city"])'

# 批量
curl -s 'https://ip.guancii.cc.cd/batch?ips=8.8.8.8,1.1.1.1,114.114.114.114'

# 只看状态码（探测是否可用）
curl -s -o /dev/null -w '%{http_code}\n' 'https://ip.guancii.cc.cd/health'
```

---

## 6. 五个容易踩的坑

**① 404 是正常业务码，别当错误处理**
`/ip?ip=坏IP` 返回 **404**，但 body 仍然是标准 JSON。前端不能只看 `r.ok` 就直接报错，
要单独把 404 当成「查不到」这一正常分支（上面的示例代码都处理了）。

**② 一定要设超时**
接口再快也可能因为网络抽风而卡住。JS 用 `AbortController`，Python 用 `timeout=5`，
PHP 用 `CURLOPT_TIMEOUT`。**不设超时会把你的页面一起拖死。**

**③ 限流是 120 次/分钟/IP，且 429 要优雅降级**
- 别在循环里逐个调，用 `/batch`（一次最多 50 个）
- IP 归属地不会分钟级变化，前端可以 `sessionStorage` 缓存一下，服务端可以缓存几小时
- 遇到 429 显示「暂时不可用」就行，不要抛异常把页面搞崩

**④ `/me` 是「谁调返回谁」**
浏览器调 = 访客；服务器调 = 服务器。见第 1 节。

**⑤ 数据库精度是城市级**
结果为运营商登记的归属地，**不能作为精确定位依据**。
IPv6 部分省份/城市可能只到市级（区县信息会丢），个别库外地址只有 `isp` 字段有值。

---

## 7. 一个小场景：给博客加「你在哪里」

```html
<span id="where">定位中…</span>

<script>
fetch('https://ip.guancii.cc.cd/me')
  .then(r => r.ok ? r.json() : null)
  .then(d => {
    const el = document.getElementById('where');
    if (!d) { el.textContent = '未知'; return; }
    el.textContent = [d.country, d.province, d.city, d.isp].filter(Boolean).join(' ') || '未知';
  })
  .catch(() => { document.getElementById('where').textContent = '未知'; });
</script>
```

就这么几行——因为接口开了 CORS，**不需要后端中转，纯静态页面也能用**。
