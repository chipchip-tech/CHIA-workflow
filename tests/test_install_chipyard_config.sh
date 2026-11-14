#!/usr/bin/env bash
# Test cho scripts/install-chipyard-config.sh trên một cây Chipyard giả (chỉ có vài file .scala).
# Chạy: bash tests/test_install_chipyard_config.sh
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$REPO/scripts/install-chipyard-config.sh"
PASS=0 FAIL=0
ok()  { PASS=$((PASS+1)); echo "PASS $1"; }
bad() { FAIL=$((FAIL+1)); echo "FAIL $1: $2"; }

# Cây giả theo bố cục 1.15.0: WithNBigCores ở package rocket, không còn ở subsystem.
fake_tree() {
    local cy="$1"
    mkdir -p "$cy/generators/chipyard/src/main/scala/config" "$cy/generators/rc/src" "$cy/generators/tc/src"
    printf 'package freechips.rocketchip.rocket\nclass WithNBigCores(\n' > "$cy/generators/rc/src/RocketConfigs.scala"
    printf 'package freechips.rocketchip.subsystem\nclass WithNBanks(n: Int)\nclass WithInclusiveCache(\n' > "$cy/generators/rc/src/Sub.scala"
    printf 'package testchipip.soc\nclass WithNoScratchpads(\n' > "$cy/generators/tc/src/Scratch.scala"
    printf 'package chipyard.config\nclass AbstractConfig extends Config(\n' > "$cy/generators/chipyard/src/main/scala/config/AbstractConfig.scala"
}

# Repo giả: chỉ thư mục chipyard/ với một config cho trước, dùng script thật.
run_with_config() {
    local cfg="$1" cy="$2" r
    r="$(mktemp -d)"; mkdir -p "$r/scripts" "$r/chipyard"
    cp "$SCRIPT" "$r/scripts/"; printf '%s\n' "$cfg" > "$r/chipyard/Test.scala"
    bash "$r/scripts/install-chipyard-config.sh" "$cy" 2>&1
}

t_wrong_package_warns() {
    local cy; cy="$(mktemp -d)"; fake_tree "$cy"
    local out; out="$(run_with_config 'class X extends Config(
  new freechips.rocketchip.subsystem.WithNBigCores(1) ++
  new chipyard.config.AbstractConfig)' "$cy")"
    grep -q "CẢNH BÁO.*freechips.rocketchip.subsystem.WithNBigCores" <<<"$out" || { bad wrong_package_warns "không cảnh báo: $out"; return; }
    grep -q "MIXIN_OK" <<<"$out" && { bad wrong_package_warns "vẫn in MIXIN_OK"; return; }
    ok wrong_package_warns
}

t_correct_config_ok() {
    local cy; cy="$(mktemp -d)"; fake_tree "$cy"
    local out; out="$(run_with_config 'class X extends Config(
  new testchipip.soc.WithNoScratchpads ++
  new freechips.rocketchip.subsystem.WithInclusiveCache(nWays = 4, capacityKB = 128) ++
  new freechips.rocketchip.subsystem.WithNBanks(1) ++
  new freechips.rocketchip.rocket.WithNBigCores(1) ++
  new chipyard.config.AbstractConfig)' "$cy")"
    grep -q "MIXIN_OK" <<<"$out" || { bad correct_config_ok "không MIXIN_OK: $out"; return; }
    ok correct_config_ok
}

t_default_dir() {
    local h; h="$(mktemp -d)"; local out
    out="$(HOME="$h" CHIPYARD_FORK= bash "$SCRIPT" 2>&1)"
    grep -q "$h/tools/chipyard" <<<"$out" || { bad default_dir "không dùng ~/tools/chipyard: $out"; return; }
    ok default_dir
}

t_wrong_package_warns; t_correct_config_ok; t_default_dir
echo "== $PASS pass, $FAIL fail"
[ $FAIL -eq 0 ]
