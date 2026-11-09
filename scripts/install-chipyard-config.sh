#!/usr/bin/env bash
# Copy các Config Scala dùng chung (chipyard/*.scala trong repo này) vào fork Chipyard trên host.
# Container khối `chipyard` mount cùng thư mục, nên không cần copy vào container.
#
# Cách dùng:
#   scripts/install-chipyard-config.sh [CHIPYARD_DIR]
#     CHIPYARD_DIR   mặc định $CHIPYARD_FORK, hoặc ~/tools/RISCVTutorial
#
# Chưa chạy thật (viết 09/11/2026).
set -euo pipefail

case "${1:-}" in -h|--help) sed -n '2,8p' "$0"; exit 0 ;; esac

REPO="$(cd "$(dirname "$0")/.." && pwd)"
CY="$(realpath -m "${1:-${CHIPYARD_FORK:-$HOME/tools/RISCVTutorial}}")"
DEST="$CY/generators/chipyard/src/main/scala/config"

[ -d "$DEST" ] || { echo "LỖI: không thấy $DEST. CHIPYARD_DIR có đúng là fork Chipyard không?" >&2; exit 1; }

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

# Fork có thể khác Chipyard 1.10.0 upstream: kiểm tra các mixin MinimalSoCConfig dùng.
WARN=0
for pat in "class WithNBigCores" "class WithInclusiveCache" "class AbstractConfig"; do
    if ! grep -rqs --include="*.scala" "$pat" "$CY/generators"; then
        echo "CẢNH BÁO: không thấy '$pat' trong fork; MinimalSoCConfig có thể không elaborate được." >&2
        WARN=1
    fi
done
[ $WARN -eq 0 ] && echo "MIXIN_OK"
echo "Xong. Build: xem docs/chipyard-minimal-soc.md"
