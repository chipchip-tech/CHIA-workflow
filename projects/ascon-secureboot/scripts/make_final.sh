#!/usr/bin/env bash
# Build out/final/ (M4) from one loop run: the iteration whose tests passed.
#
# Usage: scripts/make_final.sh <run out-dir> [iterN]
#   scripts/make_final.sh out/m3            # last iteration of out/m3
#   scripts/make_final.sh out/m3 iter0
#
# Everything in out/final/ except README.md is replaced. Written:
#   chipyard.patch       tracked changes under generators/chipyard (git apply)
#   files/               new files the agent created (+ AsconCoreV4.sv), as paths
#                        relative to the Chipyard root; untracked.tar.gz has the same
#   full.diff            whole-tree diff vs HEAD (reference)
#   results.csv, summary.json, sim_<test>.log, agent_reply.md, prompt.md,
#   expected.json, run_args.json, install_rtl.json, SOURCE.txt, SHA256SUMS
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
RUN=${1:?usage: make_final.sh <run out-dir> [iterN]}
RUN=$(cd "$RUN" && pwd)
ITER=${2:-$(cd "$RUN" && ls -d iter* | sort -V | tail -1)}
SRC=$RUN/$ITER
FINAL=$ROOT/out/final
[[ -f $SRC/summary.json ]] || { echo "no $SRC/summary.json"; exit 1; }

python3 - "$SRC/summary.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))
rows = s["rows"]
ok = s["build_ok"] and rows and all(r["passed"] for r in rows) and not s["violations"]
for r in rows:
    print(f"  {r['test']:<13} boot={r['boot_result']:<5} digest_match={r['digest_match']} "
          f"passed={r['passed']} cycles_sw={r['cycles_sw']} cycles_rocc={r['cycles_rocc']}")
if not ok:
    sys.exit("this iteration did not pass every test; pick another one")
PY

mkdir -p "$FINAL"
find "$FINAL" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} +
cp -r "$SRC/files" "$FINAL/files"
for f in chipyard.patch full.diff untracked.tar.gz untracked.txt summary.json \
         agent_reply.md prompt.md; do
  cp "$SRC/$f" "$FINAL/"
done
cp "$SRC"/sim_*.log "$FINAL/"
for f in results.csv expected.json run_args.json install_rtl.json; do
  if [[ -f $RUN/$f ]]; then cp "$RUN/$f" "$FINAL/"; fi
done
{
  echo "run: $RUN"
  echo "iteration: $ITER"
  echo "made: $(date -Iseconds)"
  python3 -c "import json,sys; i=json.load(open(sys.argv[1])); print('chipyard:', i['git_branch'], i['git_head']); print('verilator:', i['verilator_version'])" "$RUN/install_rtl.json" 2>/dev/null || true
} > "$FINAL/SOURCE.txt"
(cd "$FINAL" && find . -type f ! -name SHA256SUMS ! -name README.md -printf '%P\n' | sort \
   | xargs -d '\n' sha256sum > SHA256SUMS)
echo "out/final <- $SRC"
(cd "$FINAL" && find . -type f | sort)
