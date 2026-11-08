#!/usr/bin/env bash
# Chép OpenROAD-flow-scripts từ image chia-orfs ra host (mặc định ~/tools/OpenROAD-flow-scripts)
# để sửa tay được, giống fork Chipyard. Khối `asic` mount thư mục này đè lên
# /OpenROAD-flow-scripts trong container, nên flow, design và cả binary đều lấy từ host.
#
# Chép từ chính image (thay vì git clone) để script flow khớp đúng phiên bản OpenROAD/Yosys đã build.
#
# Cách dùng (sau khi đã build image: docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker):
#   scripts/setup-orfs.sh [--verify] [ORFS_DIR]
#     ORFS_DIR   mặc định $ORFS_DIR, hoặc ~/tools/OpenROAD-flow-scripts
#     --verify   chạy thử flow mẫu nangate45/gcd trong container (vài phút)
#
# CHƯA chạy thử.
set -euo pipefail

IMAGE="chia-orfs:latest"
VERIFY=0 DEST=""
while [ $# -gt 0 ]; do
    case "$1" in
        --verify) VERIFY=1; shift ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) DEST="$1"; shift ;;
    esac
done
DEST="$(realpath -m "${DEST:-${ORFS_DIR:-$HOME/tools/OpenROAD-flow-scripts}}")"

docker image inspect "$IMAGE" >/dev/null 2>&1 \
    || { echo "LỖI: chưa có $IMAGE. Build: docker build -t $IMAGE -f docker/orfs-ray.Dockerfile docker" >&2; exit 1; }

if [ -e "$DEST" ]; then
    echo "== $DEST đã có, không chép đè (xoá nó nếu muốn chép lại)."
else
    mkdir -p "$(dirname "$DEST")"
    cid=$(docker create "$IMAGE")
    trap 'docker rm -f "$cid" >/dev/null' EXIT
    echo "== Chép /OpenROAD-flow-scripts từ $IMAGE vào $DEST (vài GB)..."
    docker cp "$cid:/OpenROAD-flow-scripts" "$DEST"
    # Theo dõi thay đổi tay của mình trong flow/ (design, script); không theo dõi binary.
    if [ ! -d "$DEST/.git" ]; then
        git -C "$DEST" init -q
        git -C "$DEST" add flow
        git -C "$DEST" -c user.name=setup-orfs -c user.email=setup-orfs@localhost commit -qm "ORFS chép từ $IMAGE"
        echo "== Đã git init; thay đổi tay xem bằng: git -C $DEST status"
    fi
fi

if [ "$VERIFY" = 1 ]; then
    docker run --rm -i --shm-size=4gb --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$DEST:/OpenROAD-flow-scripts:z" "$IMAGE" bash -c '
        set -eo pipefail
        source /OpenROAD-flow-scripts/env.sh
        cd /OpenROAD-flow-scripts/flow
        make DESIGN_CONFIG=./designs/nangate45/gcd/config.mk 2>&1 | tail -15
        ls results/nangate45/gcd/base/6_final.gds && echo VERIFY_OK'
fi

echo "== Xong. Bật khối asic: python scripts/chia-up-blocks.py --asic"
