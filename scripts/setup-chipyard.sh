#!/usr/bin/env bash
# Dựng Chipyard gốc (ucb-bar/chipyard, ghim một tag) trong container chia-chisel-build, để khối
# `chipyard`/`vivado` của configs/cluster-blocks.template.yaml dùng được. Mã nguồn nằm trên host
# (sửa tay được), toolchain/conda env được build trong container (cùng môi trường lúc CHIA chạy).
#
# Cách dùng (đủ 11 bước mất nhiều giờ và vài chục GB):
#   scripts/setup-chipyard.sh [--tag TAG] [--verify] [--dry-run] [CHIPYARD_DIR] [-- CỜ_BUILD_SETUP...]
#     CHIPYARD_DIR  mặc định $CHIPYARD_FORK, hoặc ~/tools/chipyard
#     --tag         tag cần ghim khi clone (mặc định 1.15.0); clone xong ở nhánh cục bộ local-<TAG>
#     --verify      sau setup: build Verilator RocketConfig và chạy rv64ui-p-simple
#     --dry-run     chỉ in các lệnh sẽ chạy
#     -- ...        cờ thêm cho build-setup.sh, ví dụ `-- -s 8 -s 9` để bỏ bước 8, 9
#
# Mặc định chạy đủ 11 bước của build-setup.sh (gồm FireSim, FireMarshal, CIRCT). Bước nào hỏng thì
# script dừng, in số bước và lệnh chạy lại; KHÔNG tự bỏ bước nào.
# Thư mục đã có thì không clone, không checkout (giữ sửa tay), chỉ cảnh báo nếu không đứng trên tag.
#
# CHƯA chạy thật trên Chipyard 1.15.0 (viết 14/11/2026; các bước tay đã kiểm chứng với fork 1.10.0).
set -euo pipefail

IMAGE="ghcr.io/ucb-bar/chia-chisel-build:latest"
URL="https://github.com/ucb-bar/chipyard.git"
TAG="1.15.0"
VERIFY_CONFIG="${VERIFY_CONFIG:-RocketConfig}"
VERIFY=0 DRY=0 CY="" EXTRA=()

while [ $# -gt 0 ]; do
    case "$1" in
        --tag) TAG="$2"; shift 2 ;;
        --verify) VERIFY=1; shift ;;
        --dry-run) DRY=1; shift ;;
        --) shift; EXTRA=("$@"); break ;;
        -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
        *) CY="$1"; shift ;;
    esac
done
CY="$(realpath -m "${CY:-${CHIPYARD_FORK:-$HOME/tools/chipyard}}")"
JVM="$(realpath -m "${JVM_CACHE:-$HOME/chia_cache/jvm}")"

run() { if [ "$DRY" = 1 ]; then echo "+ $*"; else "$@"; fi; }

if [ ! -d "$CY" ]; then
    run mkdir -p "$(dirname "$CY")"
    run git clone "$URL" "$CY"
    run git -C "$CY" checkout -b "local-$TAG" "$TAG"
elif [ "$DRY" = 0 ]; then
    [ -x "$CY/build-setup.sh" ] || { echo "LỖI: $CY không phải repo Chipyard (thiếu build-setup.sh)." >&2; exit 1; }
    at="$(git -C "$CY" describe --tags --exact-match HEAD 2>/dev/null || true)"
    [ "$at" = "$TAG" ] || echo "CẢNH BÁO: $CY không đứng đúng tag $TAG (đang: ${at:-không phải tag}). Không checkout, giữ nguyên." >&2
fi

[ "$DRY" = 1 ] || echo "== Chipyard: $CY ($(git -C "$CY" log -1 --oneline))"
echo "== Log: $CY/setup.log"

# Trong container: nạp conda base rồi chạy build-setup. Chipyard 1.15.0 tự tạo env conda-lock riêng
# (.conda-lock-env), không cần cài conda-lock hay cờ -f như fork 1.10.
# Bỏ bước 1 (chạy lại sau khi hỏng giữa chừng) thì build-setup.sh cần conda env của Chipyard đã kích hoạt
# và $RISCV (bước 3), nên phải source env.sh do lần chạy trước sinh ra.
SRC_ENV=""
case " ${EXTRA[*]} " in *" -s 1 "*|*" --skip 1 "*|*" --skip-conda "*) SRC_ENV="source env.sh || exit 1
" ;; esac
INNER='set -eo pipefail
source /home/ray/anaconda3/etc/profile.d/conda.sh && conda activate base
cd "'"$CY"'"
'"$SRC_ENV"'./build-setup.sh riscv-tools'"${EXTRA[*]:+ ${EXTRA[*]}}"' 2>&1 | tee setup.log
grep -q "Setup complete!" setup.log'

if [ "$VERIFY" = 1 ]; then
    INNER="$INNER"'
source env.sh && cd sims/verilator
make -j16 CONFIG='"$VERIFY_CONFIG"' 2>&1 | tee build-verify.log | tail -5
make CONFIG='"$VERIFY_CONFIG"' run-binary BINARY=$RISCV/riscv64-unknown-elf/share/riscv-tests/isa/rv64ui-p-simple
echo "VERIFY_OK"'
fi

# Cache JVM chung với khối chipyard (bước 5/7 tiền biên dịch Scala qua sbt; coursier không có read
# timeout nên socket chết tới Maven làm treo vô hạn, xem docs/cluster-blocks.md).
# Conda (bước 1) tải hàng trăm gói; mặc định chỉ thử lại 3 lần nên mạng chập chờn làm hỏng cả bước
# (gặp 14/11/2026: HTTP 000 CONNECTION FAILED tới conda.anaconda.org). Tăng số lần thử và giãn cách.
# Chặn chạy trùng: 14/11/2026 hai container build-setup cùng chạy trên một thư mục (lần trước còn sót ở
# terminal khác), tranh nhau ghi .conda-env và chia băng thông; lần sau còn xoá setup.log của lần trước.
# Container được gắn nhãn theo đường dẫn Chipyard để nhận ra.
LABEL="chia.setup-chipyard=$CY"
if [ "$DRY" = 0 ]; then
    busy="$(docker ps -q --filter "label=$LABEL")"
    if [ -n "$busy" ]; then
        {
            echo "LỖI: đã có container đang dựng $CY: $(tr '\n' ' ' <<<"$busy")"
            echo "Để nó chạy xong (xem $CY/setup.log), hoặc dừng hẳn rồi mới chạy lại:"
            echo "  docker stop $(tr '\n' ' ' <<<"$busy")"
        } >&2
        exit 1
    fi
fi
# Bước 2 (init-submodules --full, không tắt được Gemmini) clone gemmini-rocc-tests đệ quy, mà submodule
# rocc-software của nó trỏ tới github.com/ibm/rocc-software đã bị xoá (gặp 14/11/2026: "could not read
# Username", bước 2 hỏng). Upstream (gemmini-rocc-tests PR #54, 10/11/2026) chuyển sang bản sao
# ucb-bar/rocc-software, nhưng bản sao chỉ có một commit mới, không có commit 1.15.0 ghim. Nên dựng sẵn
# gemmini + gemmini-rocc-tests trên host, đặt rocc-software ở commit của bản sao và cho
# `submodule.rocc-software.update none` để bước 2 bỏ qua nó. Mã nguồn giống nhau (bản sao khôi phục từ ibm).
ROCC_URL="https://github.com/ucb-bar/rocc-software.git"
ROCC_REV="9492c0abd0ca04abb9cddf3737ba68232326fdae"
fix_rocc_software() {
    local t="$CY/generators/gemmini/software/gemmini-rocc-tests"
    echo "== Vá rocc-software của Gemmini (ibm/rocc-software đã bị xoá) -> $ROCC_URL"
    run git -C "$CY" submodule update --init generators/gemmini
    run git -C "$CY/generators/gemmini" submodule update --init software/gemmini-rocc-tests
    run git -C "$t" config submodule.rocc-software.url "$ROCC_URL"
    run git -C "$t" config submodule.rocc-software.update none
    if [ "$DRY" = 1 ] || ! { [ -e "$t/rocc-software/.git" ] &&
            git -C "$t/rocc-software" rev-parse -q --verify "$ROCC_REV^{commit}" >/dev/null; }; then
        run rm -rf "$t/rocc-software"
        run git clone -q "$ROCC_URL" "$t/rocc-software"
        run git -C "$t/rocc-software" checkout -q "$ROCC_REV"
    fi
}
case " ${EXTRA[*]} " in
    *" -s 2 "*|*" --skip 2 "*|*" --skip-submodules "*) ;;
    *) fix_rocc_software ;;
esac

run mkdir -p "$JVM/coursier" "$JVM/sbt" "$JVM/ivy2"
# Xoá log lần trước: nếu docker hỏng trước khi tee chạy, log cũ sẽ bị chẩn đoán nhầm (hoặc có sẵn
# "Setup complete!" làm báo thành công giả).
run rm -f "$CY/setup.log"
rc=0
run docker run --rm --shm-size=10.24gb --label "$LABEL" \
    -v "$CY:$CY:z" -w "$CY" \
    -v "$JVM/coursier:/home/ray/.cache/coursier:z" -v "$JVM/sbt:/home/ray/.sbt:z" -v "$JVM/ivy2:/home/ray/.ivy2:z" \
    -e "JAVA_TOOL_OPTIONS=-Dsun.net.client.defaultConnectTimeout=30000 -Dsun.net.client.defaultReadTimeout=120000" \
    -e CONDA_REMOTE_MAX_RETRIES=10 -e CONDA_REMOTE_BACKOFF_FACTOR=3 -e CONDA_REMOTE_READ_TIMEOUT_SECS=120 \
    "$IMAGE" bash -c "$INNER" || rc=$?

# Lệnh chạy lại in ra giữ nguyên --verify/--tag của lần này.
SELF="scripts/setup-chipyard.sh"
[ "$VERIFY" = 1 ] && SELF="$SELF --verify"
[ "$TAG" = "1.15.0" ] || SELF="$SELF --tag $TAG"
SELF="$SELF $CY"

if [ $rc -ne 0 ] && grep -q "Setup complete!" "$CY/setup.log" 2>/dev/null; then
    {
        echo "LỖI: build-setup.sh đã xong (Setup complete!) nhưng bước kiểm tra --verify hỏng (mã $rc)."
        echo "Log build Verilator: $CY/sims/verilator/build-verify.log (lỗi chạy rv64ui-p-simple in ở trên)."
        echo "Chạy lại chỉ phần kiểm tra:"
        echo "  $SELF -- -s 1 -s 2 -s 3 -s 4 -s 5 -s 6 -s 7 -s 8 -s 9 -s 10 -s 11"
    } >&2
    exit $rc
fi

if [ $rc -ne 0 ]; then
    last="$(grep 'BEGINNING STEP' "$CY/setup.log" 2>/dev/null | tail -1 || true)"
    n="$(sed -E 's/.*STEP ([0-9]+):.*/\1/' <<<"$last")"
    desc="$(sed -E 's/.*STEP [0-9]+: (.*) =+ *$/\1/' <<<"$last")"
    {
        echo "LỖI: build-setup.sh hỏng (mã $rc) ở bước ${n:-?}: ${desc:-không rõ}. Log: $CY/setup.log"
        # Cờ thêm không phải -s giữ nguyên; các bước trước bước hỏng đã xong nên bỏ qua khi chạy lại
        # (bước 1 của 1.15.0 từ chối chạy khi .conda-env đã có).
        keep=() i=0
        while [ $i -lt ${#EXTRA[@]} ]; do
            if [ "${EXTRA[$i]}" = "-s" ]; then i=$((i+2)); else keep+=("${EXTRA[$i]}"); i=$((i+1)); fi
        done
        done_steps() { local k; for ((k=1; k<$1; k++)); do printf -- ' -s %d' "$k"; done; }
        case "$n" in
            1)
                if grep -q "already exist" "$CY/setup.log" 2>/dev/null; then
                    # Lần chạy trước đã dựng xong env: KHÔNG xoá, chạy tiếp và bỏ bước 1 (thêm -s cho các bước
                    # khác đã xong, xem log lần trước; bỏ hết 1..11 nếu chỉ muốn --verify).
                    echo "Env Chipyard đã có từ lần chạy trước (.conda-env); không xoá. Chạy tiếp, bỏ bước 1:"
                    echo "  $SELF --${keep[*]:+ ${keep[*]}} -s 1"
                    echo "Đã xong cả 11 bước trước đó và chỉ muốn kiểm tra: thêm -s 2 ... -s 11."
                else
                    echo "Bước 1 hỏng thì build-setup.sh không chạy lại được khi env còn sót. Sửa lỗi, xoá rồi chạy lại"
                    echo "(env.sh được ghi lại khi bước 1 chạy xong):"
                    echo "  rm -rf $CY/.conda-lock-env $CY/.conda-env"
                    echo "  $SELF${keep[*]:+ -- ${keep[*]}}"
                fi ;;
            6|7|8|9)
                [ "$n" -le 7 ] && first=6 || first=8   # 7 cần 6 (FireSim), 9 cần 8 (FireMarshal)
                echo "Nếu quyết định bỏ bước $first–$((first+1)) (ghi lỗi vào docs trước), chạy lại:"
                echo "  $SELF --${keep[*]:+ ${keep[*]}}$(done_steps "$first") -s $first -s $((first+1))" ;;
            ''|*[!0-9]*)
                echo "Không xác định được bước hỏng; đọc log." ;;
            *)
                echo "Bước $n là bước bắt buộc; đọc log và sửa lỗi, rồi chạy tiếp từ bước $n:"
                echo "  $SELF --${keep[*]:+ ${keep[*]}}$(done_steps "$n")" ;;
        esac
    } >&2
    exit $rc
fi

[ "$DRY" = 1 ] || echo "== Xong. Bật cluster: CHIPYARD_FORK=$CY python scripts/chia-up-blocks.py"
