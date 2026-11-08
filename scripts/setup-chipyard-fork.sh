#!/usr/bin/env bash
# Dựng một fork Chipyard trong container chia-chisel-build, để khối `chipyard` của
# configs/cluster-blocks.template.yaml dùng được. Mã nguồn nằm trên host (sửa tay được),
# toolchain/conda env được build trong container (cùng môi trường lúc CHIA chạy).
#
# Cách dùng (nên chạy trong tmux, mất 30–90 phút):
#   scripts/setup-chipyard-fork.sh [--clone URL] [--verify] [FORK_DIR]
#     FORK_DIR   mặc định $CHIPYARD_FORK, hoặc ~/tools/RISCVTutorial
#     --clone    git clone URL vào FORK_DIR nếu thư mục chưa có
#     --verify   sau khi dựng: build Verilator RocketConfig và chạy rv64ui-p-simple
#
# Đã kiểm chứng TỪNG BƯỚC bằng tay với RISCVTutorial (Chipyard 1.10.0) ngày 08/11/2026;
# bản script gộp này CHƯA chạy nguyên vẹn lần nào.
set -euo pipefail

IMAGE="ghcr.io/ucb-bar/chia-chisel-build:latest"
CONDA_LOCK_VERSION="${CONDA_LOCK_VERSION:-1.4.0}"   # Chipyard 1.10 dùng conda-lock 1.x
VERIFY_CONFIG="${VERIFY_CONFIG:-RocketConfig}"
CLONE_URL="" VERIFY=0 FORK=""

while [ $# -gt 0 ]; do
    case "$1" in
        --clone) CLONE_URL="$2"; shift 2 ;;
        --verify) VERIFY=1; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) FORK="$1"; shift ;;
    esac
done
FORK="${FORK:-${CHIPYARD_FORK:-$HOME/tools/RISCVTutorial}}"
FORK="$(realpath -m "$FORK")"

if [ ! -d "$FORK" ]; then
    [ -n "$CLONE_URL" ] || { echo "LỖI: $FORK chưa có. Dùng --clone URL." >&2; exit 1; }
    mkdir -p "$(dirname "$FORK")"
    git clone "$CLONE_URL" "$FORK"
fi
[ -x "$FORK/build-setup.sh" ] || { echo "LỖI: $FORK không phải repo Chipyard (thiếu build-setup.sh)." >&2; exit 1; }

echo "== Fork: $FORK ($(git -C "$FORK" log -1 --oneline))"
echo "== Log: $FORK/setup.log"

# Trong container: nạp conda base, cài conda-lock, chạy build-setup.
#  -f        bỏ câu hỏi "not on an official release" (nếu không, chạy không có bàn phím sẽ lặp mãi)
#  -s 6..9   bỏ FireSim và FireMarshal (không cần cho mô phỏng Verilator)
INNER='set -eo pipefail
source /home/ray/anaconda3/etc/profile.d/conda.sh && conda activate base
pip install -q "conda-lock=='"$CONDA_LOCK_VERSION"'"
cd "'"$FORK"'"
./build-setup.sh riscv-tools -f -s 6 -s 7 -s 8 -s 9 2>&1 | tee setup.log
grep -q "Setup complete!" setup.log'

if [ "$VERIFY" = 1 ]; then
    INNER="$INNER"'
source env.sh && cd sims/verilator
make -j16 CONFIG='"$VERIFY_CONFIG"' 2>&1 | tee build-verify.log | tail -5
make CONFIG='"$VERIFY_CONFIG"' run-binary BINARY=$RISCV/riscv64-unknown-elf/share/riscv-tests/isa/rv64ui-p-simple
echo "VERIFY_OK"'
fi

docker run --rm -i --shm-size=10.24gb \
    -v "$FORK:$FORK:z" -w "$FORK" "$IMAGE" bash -c "$INNER"

echo "== Xong. Bật cluster: CHIPYARD_FORK=$FORK python scripts/chia-up-blocks.py"
