#!/usr/bin/env bash
# 下载官方 ip2region xdb 数据文件（IPv4 + IPv6）
#
# 用法:  bash scripts/update_db.sh
# 换镜像: IPAPI_DB_BASE=https://your-mirror/data bash scripts/update_db.sh
# 只更一个: bash scripts/update_db.sh v4     或  bash scripts/update_db.sh v6
#
# 数据文件不入仓库（授权与体积原因），首次部署前需跑一次本脚本。
#
# 说明：v3 格式的 xdb 由官方仓库定期重建（数据源包含各厂商公布的 geofeed
# 与社区反馈），构建时间写在文件头里，可以用 GET /health 查到。
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$BASE_DIR/data"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

REPO="lionsoul2014/ip2region"
BRANCH="master"

# 官方 raw 优先；后两个是常见的 GitHub 加速镜像。
# 注意 cdn.jsdelivr.net 对单文件有 20MB 上限，v6（约 37MB）必然失败，故排在最后。
DEFAULT_SOURCES=(
    "https://raw.githubusercontent.com/$REPO/$BRANCH/data"
    "https://ghproxy.net/https://raw.githubusercontent.com/$REPO/$BRANCH/data"
    "https://gh-proxy.com/https://raw.githubusercontent.com/$REPO/$BRANCH/data"
    "https://cdn.jsdelivr.net/gh/$REPO@$BRANCH/data"
)

if [ "${IPAPI_DB_BASE:-}" != "" ]; then
    SOURCES=("$IPAPI_DB_BASE")
else
    SOURCES=("${DEFAULT_SOURCES[@]}")
fi

# 最小体积自检：宁可失败，也不要用一个残缺文件覆盖掉能用的库
MIN_V4=5000000
MIN_V6=10000000

WANT="${1:-both}"

for c in curl od; do
    command -v "$c" >/dev/null 2>&1 || { echo "[!] 缺少命令: $c"; exit 1; }
done

mkdir -p "$DATA_DIR"

# 从 xdb 文件头读字段。头布局：
#   0-1  结构版本(2B) | 2-3 索引策略(2B) | 4-7 构建时间戳(4B)
#   8-11 起始索引指针 | 12-15 结束索引指针 | 16-17 IP 版本(2B)
#
# ⚠️ 别图省事写 `od ... -N18 | ...` 再 `set --` 然后取 `$17`：
#    bash 会把 `$17` 解析成 `${1}` 拼上字面量 "7"（不是 `${17}`！），
#    于是 $1=3 会拼出 "37" 这种荒谬结果，校验就永远通不过。逐字段读最稳。
xdb_u8()  { od -An -v -tu1 -j"$2" -N1 "$1" | tr -d ' \n'; }
xdb_u32() { od -An -v -tu4 -j"$2" -N4 "$1" | tr -d ' \n'; }

# fetch <远程文件名> <本地文件名> <最小字节> <期望IP版本> <说明>
fetch() {
    local remote="$1" local_name="$2" min_size="$3" want_ip="$4" label="$5"
    local dest="$DATA_DIR/$local_name"
    local got=""

    for base in "${SOURCES[@]}"; do
        local url="$base/$remote"
        local out="$TMP_DIR/$local_name"
        rm -f "$out"
        printf "    尝试 %s\n" "$url"
        if ! curl -fsSL --max-time 900 -o "$out" "$url" 2>/dev/null; then
            echo "      -> 下载失败，换下一个源"
            continue
        fi
        local sz
        sz="$(wc -c < "$out" | tr -d ' ')"
        if [ "$sz" -lt "$min_size" ]; then
            echo "      -> 只有 $sz 字节（应 > $min_size），像是不完整，换下一个源"
            continue
        fi
        local hdr ver ipv ts
        ver="$(xdb_u8 "$out" 0)"
        ipv="$(xdb_u8 "$out" 16)"
        ts="$(xdb_u32 "$out" 4)"
        if [ "$ver" != "3" ]; then
            echo "      -> xdb 结构版本是 $ver，本程序只支持 3，换下一个源"
            continue
        fi
        if [ "$ipv" != "$want_ip" ]; then
            echo "      -> 文件里是 IPv$ipv，但 $label 需要 IPv$want_ip，换下一个源"
            continue
        fi
        got="$out"
        echo "      -> OK  $sz 字节  IPv$ipv  构建时间戳 $ts"
        break
    done

    if [ -z "$got" ]; then
        echo "[!] $label 所有镜像都失败，data/ 未改动"
        return 1
    fi

    # 先写 .new 再原子替换，服务端会按 mtime 自动热重载
    cp "$got" "$dest.new"
    mv "$dest.new" "$dest"
    echo "    [写入] $dest"
}

RC=0
if [ "$WANT" = "both" ] || [ "$WANT" = "v4" ]; then
    echo "[*] IPv4 数据 ($REPO/$BRANCH)"
    fetch "ip2region_v4.xdb" "ip2region_v4.xdb" "$MIN_V4" 4 "IPv4" || RC=1
fi
if [ "$WANT" = "both" ] || [ "$WANT" = "v6" ]; then
    echo "[*] IPv6 数据 ($REPO/$BRANCH)"
    fetch "ip2region_v6.xdb" "ip2region_v6.xdb" "$MIN_V6" 6 "IPv6" || RC=1
fi

echo
if [ "$RC" != "0" ]; then
    echo "[!] 有文件没更新成功，请检查网络或换镜像重试："
    echo "    IPAPI_DB_BASE=https://你的镜像/data bash scripts/update_db.sh"
    exit 1
fi

echo "[+] 更新完成:"
ls -lh "$DATA_DIR"/*.xdb 2>/dev/null | sed 's/^/    /'
echo
echo "[i] 旧的 ip2region.db / ipv6wry.db（v1 格式）如果还在，可以删掉了："
echo "    rm -f data/ip2region.db data/ipv6wry.db"
echo "[i] 服务会在下一次请求时自动热重载（无需重启）"
