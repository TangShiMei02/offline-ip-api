#!/usr/bin/env bash
# 自动更新 IP 数据库（从 npmmirror 拉取 ip2region npm 包里的数据文件）
#
# 用法: bash scripts/update_db.sh
# 换镜像: IPAPI_NPM_MIRROR=https://registry.npmjs.org bash scripts/update_db.sh
#
# 数据文件不入仓库（授权原因，见 README「数据来源与许可」），
# 所以首次部署前需要跑一次本脚本把 data/*.db 准备好。
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$BASE_DIR/data"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

MIRROR="${IPAPI_NPM_MIRROR:-https://registry.npmmirror.com}"
PKG="ip2region"

for c in curl tar python3; do
    command -v "$c" >/dev/null 2>&1 || { echo "[!] 缺少命令: $c"; exit 1; }
done

echo "[*] 查询最新版本..."
META="$TMP_DIR/meta.json"
curl -fsSL --max-time 30 "$MIRROR/$PKG" -o "$META"

TARBALL="$(python3 -c "
import json
d = json.load(open('$META'))
v = d['dist-tags']['latest']
print(d['versions'][v]['dist']['tarball'])
")"
echo "[*] 下载: $TARBALL"

curl -fsSL --max-time 300 "$TARBALL" -o "$TMP_DIR/pkg.tgz"
tar xzf "$TMP_DIR/pkg.tgz" -C "$TMP_DIR"

NEW_V4="$TMP_DIR/package/data/ip2region.db"
NEW_V6="$TMP_DIR/package/data/ipv6wry.db"

[ -f "$NEW_V4" ] || { echo "[!] 包内未找到 ip2region.db"; exit 1; }
[ -f "$NEW_V6" ] || { echo "[!] 包内未找到 ipv6wry.db"; exit 1; }

# 大小自检：宁可下载失败，也不要用一个残缺文件覆盖掉好库
for f in "$NEW_V4" "$NEW_V6"; do
    sz=$(wc -c < "$f")
    if [ "$sz" -lt 100000 ]; then
        echo "[!] $(basename "$f") 只有 $sz 字节，看起来不完整，已中止（未改动 data/）"
        exit 1
    fi
done
echo "[+] 文件大小自检通过"
ls -lh "$NEW_V4" "$NEW_V6" | sed 's/^/    /'

mkdir -p "$DATA_DIR"
# 先写临时文件再原子替换，服务端会自动热重载
cp "$NEW_V4" "$DATA_DIR/ip2region.db.new"
cp "$NEW_V6" "$DATA_DIR/ipv6wry.db.new"
mv "$DATA_DIR/ip2region.db.new" "$DATA_DIR/ip2region.db"
mv "$DATA_DIR/ipv6wry.db.new"   "$DATA_DIR/ipv6wry.db"

echo "[+] 更新完成:"
ls -lh "$DATA_DIR" | sed 's/^/    /'
echo "[+] 服务会在下一次请求时自动重载（无需重启）"
