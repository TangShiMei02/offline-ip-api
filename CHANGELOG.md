# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] — 2026-09-16

这一版主要是**安全与健壮性修复**，其中两项是实际部署中踩出来的问题。

### 安全

- **`client_ip()` 不再优先信任 `X-Forwarded-For`**。
  之前取 XFF 的**第一段**，而 nginx 的 `$proxy_add_x_forwarded_for` 是追加语义，
  客户端自己发的值排在最前面 → **限流 key 由请求方控制，换个假头就能绕过**。
  现在改为：优先 `X-Real-IP`（反代用 `$remote_addr` 设置，客户端伪造不了），
  XFF 只作兜底且取**最后一段**。
- **`IPAPI_TRUST_PROXY` 改为三档，默认挡住「伪造转发头」**。
  之前是布尔值且默认为真：服务一旦**直接对外暴露**（前面没有反向代理），
  任何人自己加一个 `X-Forwarded-For` 就能伪造身份、绕过限流。
  现在默认档只在对端是**本机 / 内网**时才信任转发头 —— 也就是「前面确实有个反代」的典型场景，
  其余情况一律用 TCP 对端地址。需要无条件信任时显式设 `IPAPI_TRUST_PROXY=2`。
- **`scripts/deploy.sh` 修复 API Key 注入静默失效**。
  之前用 `sed` 匹配 `IPAPI_KEY=""`，而模板里是 `Environment="IPAPI_KEY="`（只有一个引号），
  **匹配不到且不报错** → 你以为设了密钥，实际服务完全没有鉴权。
  现在改用 `__IPAPI_KEY__` 占位符替换，并且**写入后会回查确认**，不一致就直接中止。
- `deploy/nginx.conf.example` 补充了「真实 IP」的两个坑，特别是
  `set_real_ip_from 0.0.0.0/0` 会让 `$remote_addr` 本身被客户端改写这一隐蔽问题。

### 修复

- **提升监听队列**：`socketserver` 默认 `request_queue_size = 5`，
  在高并发建连时会被打满 → 内核丢 SYN → 客户端等约 1 秒重传
  （实测默认值下约 15% 的新连接会卡 ~1s）。改为 `128`。
- **`IPDB.reload()` 不再破坏 IPv6**：旧实现会引用未初始化的 `self.v4_path`，
  且只重载 IPv4、把 IPv6 库丢掉。现在 `v4_path` / `v6_path` 在 `__init__` 里保存，
  `reload()` 同时重载两个库并加锁。
- **热重载加锁内二次检查**：之前并发请求同时发现数据变化时会各自构建一份完整数据库，
  小内存机器上瞬时内存翻倍，现在只让第一个真正加载。
- **热重载增加节流**（`IPAPI_RELOAD_CHECK`，默认 60 秒）：
  之前每个请求都会 stat 数据文件。重载失败时只记警告，继续用旧库服务，不再影响请求。

### 变更

- `GET /me` 的返回结构与 `/ip` **统一**（之前是 `{"ip": ..., "result": {...}}`，
  现在直接把结果平铺，顶层就有 `ip` / `country` / `province` / `city` / `isp` / `version`）。
- `/` 和 `/health` 的响应里增加 `version` 字段，方便确认线上跑的是哪一版。
- 新增 `--version` 命令行参数。
- `GET /me` 查不到时不再返回 `"result": null`，而是 `{"ip": ..., "version": 0}`。

### 部署

- `scripts/ctl.sh` 的 pid / 日志**移到运行时目录**（默认 `/tmp`，可用 `IPAPI_RUNTIME_DIR` 改）。
  之前写在项目目录里，而项目目录常常不可写（例如属主是 `www` 而你是普通用户），
  会导致「启动失败却看不到失败原因」。
- `deploy/ipapi.service` 改为占位符模板；默认 `User=www`（读不到项目目录时部署脚本自动回退 root）。
- `scripts/deploy.sh` 增加 root 权限检查（缺权限时给出明确提示而不是莫名其妙地失败），
  并在结尾做鉴权自测。
- `scripts/update_db.sh` 增加文件大小自检，避免用残缺文件覆盖掉可用的数据库。

### 文档

- README 重写：数据文件获取方式、反代配置、真实 IP 注意事项、实测性能数据。
- README 与 `deploy/nginx.conf.example` 补充「**把自带查询页挂在 `/`**」的完整做法，
  并说明为什么只能用 `try_files` 而不能用 `index index.html;`
  （`index` 的内部重定向会重新匹配 location，被整站反代的 `location ^~ /` 抢走，
  首页会变成服务自己的 404 JSON）。
- **数据库文件不再随仓库分发**（授权原因），改用 `scripts/update_db.sh` 获取。
- 新增 `LICENSE`（MIT）、`.gitignore`、`data/README.md`。

---

## [1.0.0] — 2026-09-16

首个版本。

- 纯 Python 标准库，零第三方依赖
- 支持 IPv4（`ip2region.db`，xdb 段索引格式）+ IPv6（`ipv6wry.db`，IPDB 变长偏移表）
- 接口：`/ip` `/me` `/batch` `/health`
- API Key 鉴权、按 IP 内存限流、数据文件热重载
- 部署方式：systemd 一键脚本 / 裸进程 / Docker
- 附带单文件网页查询界面
