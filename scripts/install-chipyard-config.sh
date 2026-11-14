#!/usr/bin/env bash
# Copy các Config Scala dùng chung (chipyard/*.scala trong repo này) vào fork Chipyard trên host.
# Container khối `chipyard` mount cùng thư mục, nên không cần copy vào container.
#
# Cách dùng:
#   scripts/install-chipyard-config.sh [CHIPYARD_DIR]
#     CHIPYARD_DIR   mặc định $CHIPYARD_FORK, hoặc ~/tools/chipyard
#
# Sau khi copy, kiểm mọi mixin `new a.b.C` trong các file đó có thật trong cây Chipyard (đúng package
# VÀ đúng tên lớp), in MIXIN_OK nếu đủ. Kiểm trên cây Chipyard 1.15.0 trong sandbox (14/11/2026);
# chưa chạy trên máy người dùng.
set -euo pipefail

case "${1:-}" in -h|--help) sed -n '2,8p' "$0"; exit 0 ;; esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CY="$(realpath -m "${1:-${CHIPYARD_FORK:-$HOME/tools/chipyard}}")"
DEST="$CY/generators/chipyard/src/main/scala/config"

[ -d "$DEST" ] || { echo "LỖI: không thấy $DEST. CHIPYARD_DIR có đúng là thư mục Chipyard không?" >&2; exit 1; }

shopt -s nullglob
SRCS=("$REPO"/chipyard/*.scala)
[ ${#SRCS[@]} -gt 0 ] || { echo "LỖI: không có file .scala trong $REPO/chipyard" >&2; exit 1; }

for src in "${SRCS[@]}"; do
    dst="$DEST/$(basename "$src")"
    if [ -e "$dst" ] && cmp -s "$src" "$dst"; then
        echo "GIỐNG   $dst"
    else
        if [ -e "$dst" ]; then
            echo "GHI ĐÈ  $dst (diff bên dưới)"
            diff -u "$dst" "$src" || true
        else
            echo "MỚI     $dst"
        fi
        cp "$src" "$dst"
    fi
    sha256sum "$dst"
done

# Mỗi mixin `new <package>.<Lớp>` phải có trong một file .scala của cây Chipyard khai báo đúng
# `package <package>` và `class <Lớp>`. Chỉ grep tên lớp là không đủ: giữa các bản Chipyard lớp hay
# đổi package (WithNBigCores: subsystem ở 1.10.0, rocket ở 1.15.0).
WARN=0
for ref in $(grep -ohE 'new +[a-z][A-Za-z0-9_]*(\.[a-z][A-Za-z0-9_]*)*\.[A-Z][A-Za-z0-9_]*' "${SRCS[@]}" | sed -E 's/^new +//' | sort -u); do
    pkg="${ref%.*}" cls="${ref##*.}"
    found=0
    while IFS= read -r f; do
        grep -qE "^ *(case +)?class +$cls\b" "$f" && { found=1; break; }
    done < <(grep -rlE --include="*.scala" "^package +${pkg//./\\.} *\$" "$CY/generators" "$CY/tools" 2>/dev/null)
    if [ $found -eq 0 ]; then
        echo "CẢNH BÁO: không thấy class $cls trong package $pkg ($ref); config sẽ không elaborate được." >&2
        WARN=1
    fi
done
[ $WARN -eq 0 ] && echo "MIXIN_OK"
echo "Xong. Build: xem docs/chipyard-minimal-soc.md"
