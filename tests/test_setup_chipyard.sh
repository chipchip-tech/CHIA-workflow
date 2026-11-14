#!/usr/bin/env bash
# Test cho scripts/setup-chipyard.sh. Không cần docker/git thật: thay bằng bản giả qua PATH.
# Chạy: bash tests/test_setup_chipyard.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/setup-chipyard.sh"
PASS=0 FAIL=0

# --- docker/git giả -----------------------------------------------------------
# docker: ghi argv vào $CALLS; tìm thư mục làm việc (-w); nếu FAKE_FAIL_STEP=N thì ghi dòng
# BEGINNING STEP N vào setup.log và thoát 1, ngược lại ghi "Setup complete!" và thoát 0.
# git: ghi argv; `clone URL DIR` tạo DIR/build-setup.sh; `describe` in $FAKE_DESCRIBE.
make_fakes() {
    local bin="$1"; mkdir -p "$bin"
    cat > "$bin/docker" <<'EOF'
#!/usr/bin/env bash
echo "docker $(tr "\n" " " <<<"$*")" >> "$CALLS"
w=""; prev=""
for a in "$@"; do [ "$prev" = "-w" ] && w="$a"; prev="$a"; done
[ -e "$w/setup.log" ] && echo "STALE_LOG_PRESENT" >> "$CALLS"
if [ -n "${FAKE_VERIFY_FAIL:-}" ]; then
    printf ' ========== BEGINNING STEP 11: Cleaning up repository ==========\nSetup complete!\n' >> "$w/setup.log"
    exit 2
fi
if [ -n "${FAKE_FAIL_STEP:-}" ]; then
    [ -n "${FAKE_FAIL_TEXT:-}" ] && echo "$FAKE_FAIL_TEXT" >> "$w/setup.log"
    printf ' ========== BEGINNING STEP %s: %s ==========\n' "$FAKE_FAIL_STEP" "${FAKE_FAIL_DESC:-x}" >> "$w/setup.log"
    exit 1
fi
echo "Setup complete!" >> "$w/setup.log"
echo "VERIFY_OK"
EOF
    cat > "$bin/git" <<'EOF'
#!/usr/bin/env bash
echo "git $*" >> "$CALLS"
if [ "$1" = clone ]; then mkdir -p "${@: -1}"; : > "${@: -1}/build-setup.sh"; chmod +x "${@: -1}/build-setup.sh"; exit 0; fi
case " $* " in
    *" describe "*) echo "${FAKE_DESCRIBE:-1.15.0}" ;;
    *" log "*) echo "abc1234 fake" ;;
esac
exit 0
EOF
    chmod +x "$bin/docker" "$bin/git"
}

new_env() {
    T="$(mktemp -d)"; export CALLS="$T/calls"; : > "$CALLS"
    make_fakes "$T/bin"; export PATH="$T/bin:$ORIG_PATH"
    export HOME="$T/home"; mkdir -p "$HOME"
    unset CHIPYARD_FORK JVM_CACHE FAKE_FAIL_STEP FAKE_FAIL_DESC FAKE_FAIL_TEXT FAKE_DESCRIBE FAKE_VERIFY_FAIL
}
make_cy() { mkdir -p "$1"; : > "$1/build-setup.sh"; chmod +x "$1/build-setup.sh"; }

ok()   { PASS=$((PASS+1)); echo "PASS $1"; }
bad()  { FAIL=$((FAIL+1)); echo "FAIL $1: $2"; }
has()  { grep -qF -- "$2" <<<"$1"; }
ORIG_PATH="$PATH"

# --- Các ca ---------------------------------------------------------------------
t_dry_run_clone() {
    new_env; local d="$T/cy" out rc
    out="$(bash "$SCRIPT" --dry-run "$d" 2>&1)"; rc=$?
    [ $rc -eq 0 ] || { bad dry_run_clone "rc=$rc: $out"; return; }
    has "$out" "git clone https://github.com/ucb-bar/chipyard.git" || { bad dry_run_clone "thiếu git clone: $out"; return; }
    has "$out" "1.15.0" || { bad dry_run_clone "thiếu tag 1.15.0"; return; }
    [ ! -e "$d" ] || { bad dry_run_clone "dry-run đã tạo $d"; return; }
    [ ! -s "$CALLS" ] || { bad dry_run_clone "dry-run đã gọi: $(cat "$CALLS")"; return; }
    ok dry_run_clone
}

t_realpath_mount() {
    new_env; local real="$T/realhome" out
    rm -rf "$HOME"; mkdir -p "$real"; ln -s "$real" "$HOME"
    make_cy "$real/tools/chipyard"
    out="$(bash "$SCRIPT" 2>&1)"
    local dl; dl="$(grep '^docker run' "$CALLS")"
    has "$dl" "-v $real/tools/chipyard:$real/tools/chipyard" || { bad realpath_mount "mount sai: $dl"; return; }
    has "$dl" "$HOME/tools" && { bad realpath_mount "còn đường dẫn symlink: $dl"; return; }
    ok realpath_mount
}

t_no_skip_default() {
    new_env; local d="$T/cy" dl
    make_cy "$d"
    bash "$SCRIPT" "$d" > /dev/null 2>&1
    dl="$(grep '^docker run' "$CALLS")"
    has "$dl" "./build-setup.sh riscv-tools" || { bad no_skip_default "thiếu build-setup: $dl"; return; }
    grep -qE -- '-s [0-9]' <<<"$dl" && { bad no_skip_default "có -s khi không yêu cầu: $dl"; return; }
    : > "$CALLS"; rm -f "$d/setup.log"
    bash "$SCRIPT" "$d" -- -s 6 -s 7 > /dev/null 2>&1
    dl="$(grep '^docker run' "$CALLS")"
    has "$dl" "./build-setup.sh riscv-tools -s 6 -s 7" || { bad no_skip_default "không truyền -s 6 -s 7: $dl"; return; }
    ok no_skip_default
}

t_existing_dir_other_ref() {
    new_env; local d="$T/cy" out
    make_cy "$d"; export FAKE_DESCRIBE="1.10.0-5-gdeadbee"
    out="$(bash "$SCRIPT" "$d" 2>&1)"
    has "$out" "CẢNH BÁO" || { bad existing_dir_other_ref "không cảnh báo: $out"; return; }
    grep -qE '^git .*(clone|checkout)' "$CALLS" && { bad existing_dir_other_ref "đã clone/checkout: $(cat "$CALLS")"; return; }
    ok existing_dir_other_ref
}

t_failure_report() {
    new_env; local d="$T/cy" err rc
    make_cy "$d"; export FAKE_FAIL_STEP=8 FAKE_FAIL_DESC="Setting up FireMarshal"
    err="$(bash "$SCRIPT" "$d" 2>&1 >/dev/null)"; rc=$?
    [ $rc -ne 0 ] || { bad failure_report "thoát 0 khi hỏng"; return; }
    has "$err" "bước 8" || { bad failure_report "thiếu 'bước 8': $err"; return; }
    has "$err" "Setting up FireMarshal" || { bad failure_report "thiếu tên bước: $err"; return; }
    has "$err" "-- -s 1 -s 2 -s 3 -s 4 -s 5 -s 6 -s 7 -s 8 -s 9" || { bad failure_report "gợi ý chạy lại phải bỏ bước 1–7 đã xong và cặp 8–9: $err"; return; }
    ok failure_report
}

# Hỏng ở bước 1: build-setup 1.15.0 từ chối chạy lại khi .conda-env/.conda-lock-env còn; phải bảo xoá.
t_failure_step1() {
    new_env; local d="$T/cy" err
    make_cy "$d"; export FAKE_FAIL_STEP=1 FAKE_FAIL_DESC="Conda environment setup"
    err="$(bash "$SCRIPT" "$d" 2>&1 >/dev/null)"
    has "$err" ".conda-lock-env" || { bad failure_step1 "không bảo xoá .conda-lock-env: $err"; return; }
    has "$err" ".conda-env" || { bad failure_step1 "không bảo xoá .conda-env: $err"; return; }
    ok failure_step1
}

# Bỏ bước 1 thì build-setup cần $RISCV và conda env đã kích hoạt: phải source env.sh trước.
t_skip1_sources_env() {
    new_env; local d="$T/cy" dl
    make_cy "$d"
    bash "$SCRIPT" "$d" -- -s 1 -s 2 > /dev/null 2>&1
    dl="$(grep '^docker run' "$CALLS")"
    grep -qE 'source env.sh .*build-setup.sh riscv-tools -s 1 -s 2' <<<"$dl" || { bad skip1_sources_env "không source env.sh trước build-setup: $dl"; return; }
    : > "$CALLS"; rm -f "$d/setup.log"
    bash "$SCRIPT" "$d" > /dev/null 2>&1
    dl="$(grep '^docker run' "$CALLS")"
    grep -qE 'source env.sh .*build-setup.sh' <<<"$dl" && { bad skip1_sources_env "source env.sh khi chạy bước 1: $dl"; return; }
    ok skip1_sources_env
}

t_jvm_cache_mount() {
    new_env; local d="$T/cy" dl j="$HOME/chia_cache/jvm"
    make_cy "$d"
    bash "$SCRIPT" "$d" > /dev/null 2>&1
    dl="$(grep '^docker run' "$CALLS")"
    for p in "$j/coursier:/home/ray/.cache/coursier" "$j/sbt:/home/ray/.sbt" "$j/ivy2:/home/ray/.ivy2"; do
        has "$dl" "-v $p" || { bad jvm_cache_mount "thiếu mount $p: $dl"; return; }
    done
    has "$dl" "JAVA_TOOL_OPTIONS=" || { bad jvm_cache_mount "thiếu JAVA_TOOL_OPTIONS"; return; }
    has "$dl" "defaultReadTimeout=120000" || { bad jvm_cache_mount "thiếu read timeout"; return; }
    ok jvm_cache_mount
}

# Chạy lại khi env đã dựng xong: bước 1 báo "already exist". Không được khuyên xoá env đang dùng được.
t_rerun_after_success() {
    new_env; local d="$T/cy" err
    make_cy "$d"; export FAKE_FAIL_STEP=1 FAKE_FAIL_DESC="Conda environment setup"
    export FAKE_FAIL_TEXT="Error: Conda environment directories already exist! Delete them before trying to recreate"
    err="$(bash "$SCRIPT" --verify "$d" 2>&1 >/dev/null)"
    has "$err" "rm -rf" && { bad rerun_after_success "khuyên xoá env đã dựng: $err"; return; }
    has "$err" "-- -s 1" || { bad rerun_after_success "thiếu gợi ý bỏ bước 1: $err"; return; }
    has "$err" "--verify" || { bad rerun_after_success "lệnh gợi ý bỏ mất --verify: $err"; return; }
    ok rerun_after_success
}

# build-setup xong (Setup complete!) nhưng verify hỏng: không được đổ cho bước 11.
t_verify_failure() {
    new_env; local d="$T/cy" err rc
    make_cy "$d"; export FAKE_VERIFY_FAIL=1
    err="$(bash "$SCRIPT" --verify "$d" 2>&1 >/dev/null)"; rc=$?
    [ $rc -ne 0 ] || { bad verify_failure "thoát 0"; return; }
    has "$err" "bước 11" && { bad verify_failure "đổ lỗi cho bước 11: $err"; return; }
    has "$err" "build-verify.log" || { bad verify_failure "không trỏ tới build-verify.log: $err"; return; }
    ok verify_failure
}

# setup.log của lần trước phải bị xoá trước khi chạy, nếu không có thể báo nhầm bước hay báo thành công giả.
t_stale_log_removed() {
    new_env; local d="$T/cy"
    make_cy "$d"; echo "Setup complete!" > "$d/setup.log"
    bash "$SCRIPT" "$d" > /dev/null 2>&1
    grep -q STALE_LOG_PRESENT "$CALLS" && { bad stale_log_removed "setup.log cũ còn lúc chạy docker"; return; }
    ok stale_log_removed
}

for t in t_rerun_after_success t_verify_failure t_stale_log_removed t_dry_run_clone t_realpath_mount t_no_skip_default t_existing_dir_other_ref t_failure_report t_failure_step1 t_skip1_sources_env t_jvm_cache_mount; do
    "$t"
done
echo "== $PASS pass, $FAIL fail"
[ $FAIL -eq 0 ]
