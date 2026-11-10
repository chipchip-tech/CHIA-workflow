#!/usr/bin/env bash
# Chép Config Scala của project minsoc-fmax vào fork Chipyard trên host (khối chipyard/vivado mount
# cùng thư mục). Cần MinimalSoCConfig đã cài (scripts/install-chipyard-config.sh, nhánh setup).
#
#   projects/minsoc-fmax/install.sh [CHIPYARD_DIR]     mặc định $CHIPYARD_FORK hoặc ~/tools/RISCVTutorial
#
# Chưa chạy thật (viết 10/11/2026).
set -euo pipefail
case "${1:-}" in -h|--help) sed -n '2,7p' "$0"; exit 0 ;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
CY="$(realpath -m "${1:-${CHIPYARD_FORK:-$HOME/tools/RISCVTutorial}}")"
CFG="$CY/generators/chipyard/src/main/scala/config"
FPGA="$CY/fpga/src/main/scala/arty100t"

for d in "$CFG" "$FPGA"; do
    [ -d "$d" ] || { echo "LỖI: không thấy $d" >&2; exit 1; }
done
[ -e "$CFG/MinimalSoCConfig.scala" ] || { echo "LỖI: chưa cài MinimalSoCConfig (scripts/install-chipyard-config.sh)" >&2; exit 1; }

copy() {   # src dst
    if [ -e "$2" ] && cmp -s "$1" "$2"; then echo "GIỐNG   $2"
    else
        if [ -e "$2" ]; then echo "GHI ĐÈ  $2"; diff -u "$2" "$1" || true; else echo "MỚI     $2"; fi
        cp "$1" "$2"
    fi
    sha256sum "$2"
}
copy "$HERE/scala/MinSocFmaxConfig.scala" "$CFG/MinSocFmaxConfig.scala"
copy "$HERE/scala/MinSocArty100TConfigs.scala" "$FPGA/MinSocArty100TConfigs.scala"

echo "--- WithArty100TTweaks trong fork (cần có tham số freqMHz):"
grep -n "class WithArty100TTweaks" "$FPGA"/*.scala || echo "CẢNH BÁO: không thấy WithArty100TTweaks"
echo "--- file mới chưa track (bình thường): git status của fork"
git -C "$CY" status --short | head -20
echo "INSTALL_DONE"
