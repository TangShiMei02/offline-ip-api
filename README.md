# ipapi — 离线 IP 归属地查询 API

一个**零第三方依赖**的离线 IP 归属地查询服务。纯 Python 标准库实现，数据库全量常驻内存，
适合跑在 1 核 512MB 的小机器上。

- 支持 **IPv4 + IPv6**
- **零依赖**：只用 Python 标准库，不需要 pip install，不需要 Node.js
- **快**：单次查询 0.03～0.18 ms（纯内存二分），单核实测 **2500+ QPS**
- **省**：常驻内存约 **26 MB**
- 自带 API Key 鉴权、按 IP 限流、数据库热更新
- 附一个单文件网页查询界面，可选的

---

## 一、目录结构

```
ipapi/
├── app/
│   ├── server.py           # HTTP 服务
│   └── ipdb.py             # 数据库解析核心
├── data/                   # 数据库放这里（不入仓库，见第九节）
│   ├── ip2region.db        # IPv4（约 8.3 MB）
│   └── ipv6wry.db          # IPv6（约 2.5 MB）
├── scripts/
│   ├── deploy.sh           # 一键部署（装 systemd 服务）
│   ├── ctl.sh              # 启动/停止/状态（不用 systemd 时）
│   └── update_db.sh        # 下载 / 更新数据库
├── deploy/
│   ├── ipapi.service       # systemd 单元模板
│   ├── nginx.conf.example  # Nginx 反向代理配置示例
│   └── index.html          # 可选：网页查询界面（挂法见 deploy/nginx.conf.example）
├── Dockerfile
├── README.md
└── USAGE.md                # 调用示例（JS / Python / PHP / 命令行）
```

---

## 二、快速开始

### 1. 准备数据库

**数据文件不随仓库分发**（授权原因，见第九节），先把它拉下来：

```bash
bash scripts/update_db.sh
```

成功后会看到 `data/` 下出现两个 `.db` 文件。如果网络不通，可以换镜像：

```bash
IPAPI_NPM_MIRROR=https://registry.npmjs.org bash scripts/update_db.sh
```

### 2. 跑起来

```bash
python3 app/server.py --port 8080
```

不用装任何东西。Python 3.8+ 即可（在 3.9 上实测通过）。

### 3. 验证

```bash
curl 'http://127.0.0.1:8080/health'
curl 'http://127.0.0.1:8080/ip?ip=114.114.114.114'
```

```json
{
  "country": "中国",
  "region": "",
  "province": "江苏省",
  "city": "南京市",
  "isp": "",
  "raw": "中国|0|江苏省|南京市|0",
  "ip": "114.114.114.114",
  "version": 4
}
```

---

## 三、部署

### 方式 A：systemd（推荐）

```bash
sudo bash scripts/deploy.sh

# 带配置：
sudo IPAPI_KEY=你的密钥 IPAPI_RATE_LIMIT=60 bash scripts/deploy.sh
sudo PORT=9000 bash scripts/deploy.sh
```

脚本会：检查环境 → 校验数据 → 用模板生成 `/etc/systemd/system/ipapi.service`
→ 启动 → 自测（并且会**回查 API Key 是否真的写进去了**）。

> 脚本默认把服务跑在 `www` 用户下（读不到项目目录时自动回退 root）。
> 想固定身份，改 `deploy/ipapi.service` 里的 `User=`。

### 方式 B：不用 systemd（LXC / 共享环境）

```bash
bash scripts/ctl.sh start     # 启动
bash scripts/ctl.sh status    # 状态 + 健康检查
bash scripts/ctl.sh stop
bash scripts/ctl.sh log       # 跟踪日志
```

pid 和日志默认写在 `/tmp`（**不写进项目目录**，因为项目目录在很多部署方式下不可写）。
可用 `IPAPI_RUNTIME_DIR` 改。

### 方式 C：Docker

```bash
docker build -t ipapi .
docker run -d --name ipapi -p 8080:8080 ipapi
```

镜像里会在构建阶段自动下载数据库（如果 `data/` 为空）。

### 对外访问：反向代理

服务默认只监听 `127.0.0.1`，**不要直接把 8080 暴露到公网**（没有 HTTPS，API Key 会明文传输）。
正确做法是让 Web 服务器反代过来：

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $remote_addr;   # 覆盖，不要用 $proxy_add_x_forwarded_for
    proxy_set_header Connection        "";
    proxy_buffering off;
    proxy_cache     off;
    proxy_intercept_errors off;    # 别让 error_page 404 把接口的 404 换成 HTML
}
```

完整配置（含上游 keep-alive、宝塔面板操作步骤）见 **`deploy/nginx.conf.example`**。

> ### ⚠️ 反向代理时务必注意「真实 IP」
>
> 本服务的限流是按请求方 IP 计数的。如果上游给的 IP 是客户端可以自己伪造的，
> **限流就完全没有意义**。有两个坑：
>
> **坑 1**：`proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` 是**追加**语义，
> 客户端自己发的值会排在最前面。上面示例用 `$remote_addr` **覆盖**来规避。
>
> **坑 2（更隐蔽）**：链路上如果配了 `ngx_http_realip_module`：
> ```nginx
> set_real_ip_from 0.0.0.0/0;        # ← 信任所有来源
> real_ip_header X-Forwarded-For;
> ```
> **`$remote_addr` 本身会被改写成客户端自报的值**，于是无论 `proxy_set_header` 怎么写都是假的，
> 访问日志也全是假的。不少面板（如宝塔的「CDN 获取真实 IP」功能）会默认写成这样。
> 只在真正有 CDN 在你前面时才启用 realip，并且必须把 `set_real_ip_from` 收窄到 CDN 的官方网段。
>
> **自查方法**（两个头给**不同**的值，看哪个生效）：
> ```bash
> curl -H "X-Real-IP: 1.1.1.1" -H "X-Forwarded-For: 2.2.2.2" https://你的域名/me
> ```
> 期望返回**真实来源地址**。若返回 `2.2.2.2`，说明中了坑 2。

### 可选：把自带的网页查询页挂在 `/`

项目自带 `deploy/index.html`（单文件查询界面，无依赖无构建）。想让它显示在
`https://你的域名/`，同时 `/ip` `/me` 仍然走反代：

```bash
cp deploy/index.html ./index.html      # 放到站点根目录并改名为 index.html
```

```nginx
# 在 server { } 里、location / 之前加上：
location = / {
    root /opt/ipapi;                 # 换成你的项目绝对路径
    try_files /index.html =404;      # ⚠️ 必须用 try_files，不能用 index
}
location = /index.html {
    root /opt/ipapi;
    try_files /index.html =404;
}
```

> **⚠️ 这里有个坑，别用 `index index.html;`**
>
> `index` 会让 nginx 做一次**内部重定向到 `/index.html`**，而内部重定向会
> **重新走一遍 location 匹配** —— 于是 `/index.html` 被反代的 `location ^~ /` 抢走、
> 送进本服务，首页就变成了本服务的 404 JSON（`{"code":404,"msg":"not found"}`）。
>
> `try_files` 命中文件时是在**当前上下文**直接处理，不重新匹配 location，所以是对的。
> `location = /index.html` 那段是兜底，防止别的东西又内部重定向回来。
>
> 挂上之后：`/` 是查询页，`/help` 是接口清单 JSON（本来 `/` 返回的就是这个），
> `/ip` `/me` `/batch` `/health` 照旧。

---

## 四、API

> 📘 **想直接看「怎么在项目里调用」的完整代码示例（JS / Python / PHP / 命令行），
> 以及最容易踩的五个坑，见 [`USAGE.md`](USAGE.md)。**

### `GET /ip?ip=<地址>`

查询指定 IP，支持 IPv4 / IPv6。

```bash
curl 'http://127.0.0.1:8080/ip?ip=223.5.5.5'
```

| 字段 | 说明 |
|---|---|
| `country` | 国家 / 地区 |
| `region` | 区域（多数库为空） |
| `province` | 省份 |
| `city` | 城市 |
| `isp` | 运营商 |
| `version` | `4` 或 `6` |
| `raw` | 数据库原始字符串 |

### `GET /me`

查询**请求方自己**的公网 IP 归属地（从 `X-Real-IP` / `X-Forwarded-For` 识别）。
返回结构与 `/ip` 一致。

### `GET /batch?ips=a,b,c`

批量查询，最多 50 个，逗号分隔。无效项单独给 `error` 字段，不影响其他项。

```json
{ "count": 3, "results": [ { "...": "..." }, { "ip": "notanip", "error": "无效或未收录" } ] }
```

### `GET /health`

健康检查，返回数据库加载状态。

```json
{ "status": "ok", "version": "1.1.0", "ipv4": true, "ipv6": true, "mtime": 1789520300 }
```

### `GET /`（或 `/help`）

返回接口清单和版本号。

### 状态码

| 码 | 含义 |
|---|---|
| 200 | 成功 |
| 400 | 参数缺失 |
| 401 | API Key 错误 |
| 404 | IP 无效或未收录 |
| 429 | 触发限流 |
| 503 | 数据库未加载 |

### 使用 API Key

配置 `IPAPI_KEY` 后，客户端有两种传法：

```bash
# 请求头（推荐）
curl -H "X-API-Key: 你的KEY" 'http://127.0.0.1:8080/ip?ip=8.8.8.8'

# URL 参数（会写进访问日志，安全性差一些）
curl 'http://127.0.0.1:8080/ip?ip=8.8.8.8&key=你的KEY'
```

---

## 五、配置项

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `IPAPI_KEY` | 空 | API Key。**留空 = 不校验**。对外提供服务时建议设置 |
| `IPAPI_RATE_LIMIT` | `60` | 每 IP 每分钟请求上限，`0` 关闭 |
| `IPAPI_TRUST_PROXY` | `1` | 是否信任 `X-Real-IP` / `X-Forwarded-For`，见下表 |
| `IPAPI_RELOAD_CHECK` | `60` | 热重载检查间隔（秒），`<=0` 关闭 |

`IPAPI_TRUST_PROXY` 有三档，别用错（用错会导致限流可被绕过）：

| 值 | 行为 | 什么时候用 |
|---|---|---|
| `0` | **不信任**任何转发头，一律用 TCP 对端地址 | 服务直接对外暴露、前面没有反向代理 |
| `1` | 信任转发头，**但仅当对端是本机 / 内网地址** | 默认值。反向代理和本服务在同一台机器（最常见） |
| `2` | **无条件**信任转发头 | 反向代理在**另一台**机器上、以公网 IP 连过来 |

> 默认档 `1` 的设计意图：反向代理通常在本机（对端是 `127.0.0.1`），
> 这时转发头可信；而服务如果被直接暴露在公网，对端就是公网地址，
> 转发头会被忽略——**避免任何人自己编一个 IP 就绕过限流**。

命令行参数：

```bash
python3 app/server.py --host 127.0.0.1 --port 8080 --debug
python3 app/server.py --version
```

---

## 六、性能与资源

在 **1 核 / 512 MB 内存**的 Linux 容器（Debian 11，Python 3.9）上实测：

| 指标 | 实测值 |
|---|---|
| 常驻内存 | 约 **26 MB** |
| 并发吞吐（20 线程，连接复用） | **2534 QPS**，0 错误 |
| 单次查询 | **0.03 ～ 0.18 ms** |
| 数据库加载 | **47 ms**（IPv4 + IPv6 一起读入） |

**注意**：单进程即可，**不要**用多 worker 启动——每个 worker 都会各自加载一份完整数据库，内存翻倍。

---

## 七、更新数据库

```bash
bash scripts/update_db.sh
```

会从镜像拉取最新的 `ip2region` 包并原子替换数据文件，**服务会自动热重载，无需重启**。

挂个定时任务：

```cron
# 每月 1 号凌晨 3 点更新
0 3 1 * * cd /opt/ipapi && bash scripts/update_db.sh >> /tmp/ipapi-update.log 2>&1
```

---

## 八、常见问题

**Q：启动报「未找到任何数据库文件」？**
`data/` 是空的。先跑 `bash scripts/update_db.sh`。

**Q：外网访问返回 502？**
反向代理的目标写错，或服务没起来。先 `curl 127.0.0.1:8080/health` 确认。

**Q：查出来的 IP 全是假的 / 限流不管用？**
看上面「反向代理时务必注意真实 IP」那一节，大概率是 `real_ip` 配置问题。

**Q：`/ip?ip=坏IP` 返回的是一页 HTML 而不是 JSON？**
你的 Web 服务器（或主机商）配了 `error_page 404 /404.html`，把接口的 404 换掉了。
在反代的 `location` 里加一行 `proxy_intercept_errors off;`。

**Q：查询结果不准，或者内网 IP 没识别出来？**
运营商 IP 段变动频繁，跑一次 `update_db.sh`。数据库精度是城市级，仅供参考。

**Q：内存不够？**
只保留 `ip2region.db`（IPv4）、删掉 IPv6 库可省约 2.5 MB；或调小 systemd 里的 `MemoryMax`。

**Q：想确认线上跑的是哪个版本？**
```bash
curl -s https://你的域名/health
curl -s https://你的域名/ | head -c 200
```

---

## 九、数据来源与许可

| 文件 | 来源 | 许可 |
|---|---|---|
| `data/ip2region.db` | [ip2region](https://github.com/lionsoul2014/ip2region) 的 IPv4 段索引数据 | MIT |
| `data/ipv6wry.db` | 纯真 IPv6 地址库（随 `ip2region` npm 包分发） | 见下方说明 |

**这两个数据文件不随本仓库分发**，原因是 `ipv6wry.db` 的授权条款对再分发和商业使用有限制，
直接打包进仓库会有合规风险。请通过 `scripts/update_db.sh` 自行获取，
并自行确认你的使用场景符合其授权。

**本项目自身的代码**以 MIT 许可发布，见 [LICENSE](LICENSE)。

---

## 十、实现说明

- IPv4 用的是 ip2region xdb 的**段索引**格式（每块 12 字节，`dataPtr` 高 8 位存长度）；
- IPv6 用的是 IPDB 的**变长偏移表**格式，并处理了 IPv4-mapped、6to4、Teredo 这几种内嵌 v4 的情况。

两种格式的解析都在 `app/ipdb.py` 里，关键位置有注释。
解析实现参考了 ip2region 的 C / Java 版本。
