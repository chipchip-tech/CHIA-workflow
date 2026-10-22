#!/usr/bin/env bash
# Local smoke test of loop/ascon_loop.py orchestration: real Ray + real CHIA
# cache/bypass, fake Chipyard. Needs: python env with chialoops 2.0.1, gcc, git.
#   * fake checkout: a git repo with generators/chipyard and a rocket-chip
#     submodule; sims/verilator/Makefile "builds" a simulator script that just
#     executes the test binary, and fails for AsconRocketConfig unless
#     AsconConfigs.scala exists
#   * fake riscv gcc: host gcc (drops -specs/-mcmodel), so tests run natively
# Scenarios: A M1 (fake LLM, RocketConfig, SW_ONLY) passes; B same run-id
# again replays build+compile; D build failure path; C replay of a seeded
# agent turn restores the tree, then the real (fake) build passes.
#
# It starts its OWN local Ray head and stops it with `ray stop --force` at the
# end, which would also kill a CHIA cluster: run it only after `chia down`.
set -euo pipefail
PY=${PY:-python}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
if ray status >/dev/null 2>&1; then
  echo "A Ray cluster is running (CHIA?). Run 'chia down <cluster.yaml> -y' first."
  exit 1
fi
W=$(mktemp -d)
trap 'ray stop --force >/dev/null 2>&1 || true; rm -rf "$W"' EXIT
CY=$W/chipyard

mkdir -p "$W/bin" "$W/sub" && cd "$W/sub"
git init -q && echo orig > f.scala && git add . && git -c user.email=s@t -c user.name=s commit -qm init
mkdir -p "$CY" && cd "$CY" && git init -q
mkdir -p generators/chipyard/src/main/scala sims/verilator .conda-env/riscv-tools/bin
echo "// original" > generators/chipyard/src/main/scala/Foo.scala
printf 'sims/verilator/simulator-*\n' > .gitignore
cat > sims/verilator/Makefile <<'MK'
CONFIG ?= RocketConfig
CONFIG_PACKAGE ?= chipyard
SIM = simulator-$(CONFIG_PACKAGE).harness-$(CONFIG)
all:
	@echo build >> $(SMOKE_COUNTER)
	@if [ "$(CONFIG)" = AsconRocketConfig ] && [ ! -f ../../generators/chipyard/src/main/scala/config/AsconConfigs.scala ]; then \
	  echo "[error] Foo.scala:1:1: not found: type AsconRocketConfig"; exit 1; fi
	@printf '#!/bin/bash\nexec "$${@: -1}"\n' > $(SIM) && chmod +x $(SIM)
	@echo "built $(SIM)"
clean:
	rm -f simulator-*
MK
cat > .conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc <<'GCC'
#!/bin/bash
args=(); for a in "$@"; do case $a in -specs=*|-mcmodel=*) ;; *) args+=("$a");; esac; done
exec gcc "${args[@]}"
GCC
chmod +x .conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc
git add . && git -c protocol.file.allow=always submodule add -q "$W/sub" generators/rocket-chip
git -c user.email=s@t -c user.name=s commit -qm init
printf '#!/bin/bash\ncat\n' > "$W/bin/spike-dasm"; chmod +x "$W/bin/spike-dasm"

export SMOKE_COUNTER=$W/builds ASCON_CHIPYARD_PATH=$CY ASCON_SIM_WORK_DIR=$W/simwork
export PATH=$W/bin:$PATH GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=protocol.file.allow GIT_CONFIG_VALUE_0=always
touch "$SMOKE_COUNTER"
ray start --head --num-cpus 8 --disable-usage-stats \
  --resources '{"chipyard": 1, "verilator_run": 1, "opencode_creds": 1}' > "$W/ray.log" 2>&1

loop() { (cd "$ROOT/loop" && $PY ascon_loop.py --project-dir "$ROOT" --cache-dir "$W/cache" \
           --sw-only "$@") > "$W/last.log" 2>&1; }
builds() { wc -l < "$SMOKE_COUNTER"; }
fail() { echo "SMOKE FAIL: $*"; tail -60 "$W/last.log"; exit 1; }

echo "== A: M1 (fake LLM, RocketConfig, SW_ONLY)"
loop --out-dir "$W/out/A" --run-id smokeA --config RocketConfig --fake-llm --iters 1 || fail "A exit code"
grep -q "SUCCESS after 1" "$W/last.log" || fail "A no success"
for f in results.csv install_rtl.json iter0/chipyard.patch iter0/untracked.tar.gz \
         iter0/files/generators/chipyard/src/main/resources/vsrc/AsconCoreV4.sv \
         iter0/sim_boot_good.log iter0/sim_boot_tamper.log iter0/prompt.md iter0/agent_reply.md; do
  [[ -e $W/out/A/$f ]] || fail "A missing $f"
done
cmp -s "$ROOT/rtl/AsconCoreV4.sv" "$CY/generators/chipyard/src/main/resources/vsrc/AsconCoreV4.sv" || fail "A sv"
grep -q "^0,1,boot_good,OK,1," "$W/out/A/results.csv" || fail "A csv good"
grep -q "^0,1,boot_tamper,FAIL,1," "$W/out/A/results.csv" || fail "A csv tamper"
[[ $(builds) == 1 ]] || fail "A builds=$(builds)"
cat "$W/out/A/results.csv"

echo "== B: same run-id -> build and compiles replayed from cache"
loop --out-dir "$W/out/B" --run-id smokeA --config RocketConfig --fake-llm --iters 1 || fail "B exit code"
[[ $(builds) == 1 ]] || fail "B rebuilt (builds=$(builds))"
grep -q "SUCCESS after 1" "$W/last.log" || fail "B no success"

echo "== D: build failure is fed back"
loop --out-dir "$W/out/D" --run-id smokeD --config AsconRocketConfig --fake-llm --iters 2 && fail "D should exit 1"
grep -q "not found: type AsconRocketConfig" "$W/out/D/iter0/feedback.md" || fail "D feedback"
grep -q "not found: type AsconRocketConfig" "$W/out/D/iter1/prompt.md" || fail "D prompt"
grep -q "^1,0,boot_good,NOT_RUN" "$W/out/D/results.csv" || fail "D csv"
[[ $(builds) == 3 ]] || fail "D builds=$(builds)"

echo "== C: replay a seeded agent turn, restore its tree, real build"
echo "// stray edit that restore must revert" >> "$CY/generators/chipyard/src/main/scala/Foo.scala"
$PY "$ROOT/tests/smoke_seed.py" "$W/cache" smokeC > "$W/seed.log" 2>&1 || { cat "$W/seed.log"; fail "seed"; }
loop --out-dir "$W/out/C" --run-id smokeC --config AsconRocketConfig --iters 1 || fail "C exit code"
grep -q "agent (replayed" "$W/last.log" || fail "C not replayed"
[[ $(cat "$CY/generators/chipyard/src/main/scala/Foo.scala") == "// patched by the agent" ]] || fail "C Foo.scala"
[[ -f $CY/generators/chipyard/src/main/scala/config/AsconConfigs.scala ]] || fail "C AsconConfigs"
[[ -f $W/out/C/iter0/files/generators/chipyard/src/main/scala/config/AsconConfigs.scala ]] || fail "C out files"
[[ $(builds) == 4 ]] || fail "C builds=$(builds)"
grep -q "SUCCESS after 1" "$W/last.log" || fail "C no success"

echo "== E: rule violation (edit inside the rocket-chip submodule) is reported"
echo hack >> "$CY/generators/rocket-chip/f.scala"
loop --out-dir "$W/out/E" --run-id smokeE --config RocketConfig --fake-llm --iters 1 && fail "E should exit 1"
grep -q "generators/rocket-chip:  M f.scala" "$W/out/E/iter0/feedback.md" || fail "E feedback"
echo "smoke: all scenarios passed"
