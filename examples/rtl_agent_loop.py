"""A small agentic RTL loop on CHIA: edit -> build -> simulate -> feedback.

Each iteration:
  1. an LLM agent edits Chipyard's Chisel sources through a BashTool,
  2. ChiselBuildNode builds a Verilator simulator from the edited tree,
  3. VerilatorRunNode runs every test program in parallel,
  4. if the expected string is missing (or the build/sim failed), the error
     is fed back into the next prompt.

The agent's BashTool and the build must land on the SAME Chipyard container,
because containers do not share files. --pin picks that container by a
machine-specific resource (cy_head / cy_remote in configs/cluster-lan.yaml).

Run from the head, in a directory containing this file:
    chia job submit --working-dir . -- python rtl_agent_loop.py --iters 3
    chia job submit --working-dir . -- python rtl_agent_loop.py --pin cy_remote

Needs workers advertising: chipyard, verilator_run, opencode_creds.
"""

import argparse
import subprocess
import uuid

import ray

from chia.base.ChiaFunction import ChiaFunction, get
from chia.base.tools.BashTool import BashTool
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode
from chia.chipyard.verilator_run_node import VerilatorRunNode
from chia.models.opencode import OpenCodeLLM
from chia.trace.profiler import start_collector

CHIPYARD_PATH = "/home/ray/chipyard"
RISCV_GCC = f"{CHIPYARD_PATH}/.conda-env/riscv-tools/bin/riscv64-unknown-elf-gcc"

TESTS = {
    "hello": r"""
#include <stdio.h>
int main(void) { printf("hello from the test program\n"); return 0; }
""",
    "sum": r"""
#include <stdio.h>
int main(void) {
    long s = 0;
    for (int i = 1; i <= 100; i++) s += i;
    printf("sum=%ld\n", s);
    return s == 5050 ? 0 : 1;
}
""",
}

TASK = (
    "Use the chipyard_bash tool to edit the Chisel sources under "
    "generators/rocket-chip so that the Rocket core prints exactly "
    "\"CHIA loop: instret reached 2000\" (one line) when its retired "
    "instruction counter (instret) equals 2000. If such a print already "
    "exists, keep exactly one copy. Do NOT run make or build anything yourself: "
    "the loop builds and simulates after you finish. Reply with a short "
    "summary of the file and lines you changed."
)
EXPECT = "CHIA loop: instret reached 2000"


@ChiaFunction(resources={"chipyard": 1})
def compile_program(name: str, src: str) -> bytes:
    """Compile one C test for bare-metal Rocket (HTIF) on a Chipyard worker."""
    tag = uuid.uuid4().hex[:8]
    c_path, elf_path = f"/tmp/{name}-{tag}.c", f"/tmp/{name}-{tag}.riscv"
    with open(c_path, "w", encoding="utf-8") as f:
        f.write(src)
    proc = subprocess.run(
        [RISCV_GCC, "-static", "-specs=htif_nano.specs", c_path, "-o", elf_path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"compile {name} failed:\n{proc.stderr}")
    with open(elf_path, "rb") as f:
        return f.read()


def chipyard_resources(pin: str) -> dict:
    """Resources that land on one specific Chipyard container."""
    return {"chipyard": 1} if pin == "chipyard" else {"chipyard": 1, pin: 0.01}


def ask_agent(llm, prompt: str, pin: str) -> str:
    bash = BashTool("chipyard_bash", CHIPYARD_PATH,
                    task_options={"resources": chipyard_resources(pin)})
    try:
        resp = get(llm.prompt.chia_remote(llm, prompt, tools=[bash]))
    finally:
        bash.stop()  # frees the chipyard slot for the build
    return resp.result


def build_and_test(pin: str, binaries: dict) -> tuple[bool, str]:
    cb_node = ChiselBuildNode(CHIPYARD_PATH, "RocketConfig",
                              target=BuildTarget.VERILATOR, timeout_seconds=3600)
    artifact_ref = cb_node.build.options(
        resources=chipyard_resources(pin)).chia_remote(cb_node)
    artifact = get(artifact_ref)  # check the build before fanning out sims
    if not artifact.success:
        tail = (artifact.stdout + artifact.stderr)[-3000:]
        return False, f"BUILD FAILED (rc={artifact.returncode}):\n{tail}"

    sim = VerilatorRunNode()
    # One task per test; Ray spreads them over every verilator_run worker.
    refs = {name: sim.run.chia_remote(sim, artifact_ref, elf, f"{name}.riscv", "/home/ray")
            for name, elf in binaries.items()}
    report, found = [], False
    for name, ref in refs.items():
        r = get(ref)
        found = found or EXPECT in r.out or EXPECT in r.log
        report.append(f"[{name}] rc={r.returncode}\n{r.log.strip()[-800:]}")
    return found, "\n\n".join(report)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=3)
    ap.add_argument("--pin", default="chipyard",
                    help="resource naming the Chipyard container to use (e.g. cy_head)")
    ap.add_argument("--model", default="opencode/big-pickle")
    ap.add_argument("--profile-dir", default="/tmp/chia_profiles")
    args = ap.parse_args()

    ray.init(address="auto", ignore_reinit_error=True)
    start_collector(log_dir=args.profile_dir)

    # Dispatch every compile first so they run in parallel, then collect.
    refs = {name: compile_program.chia_remote(name, src) for name, src in TESTS.items()}
    binaries = {name: get(ref) for name, ref in refs.items()}

    # timeout_seconds: an agent turn that also explores the tree can exceed the 600 s default.
    llm = OpenCodeLLM(model=args.model, timeout_seconds=1800)
    prompt = TASK
    for i in range(args.iters):
        print(f"\n===== iteration {i} =====")
        print("Agent:", ask_agent(llm, prompt, args.pin))
        ok, report = build_and_test(args.pin, binaries)
        print(report)
        if ok:
            print(f"\nSUCCESS after {i + 1} iteration(s): found {EXPECT!r}")
            return
        prompt = (TASK + "\n\nYour previous attempt did not work. Build/simulation "
                  "output:\n" + report + "\nFix the problem.")
    print(f"\nGave up after {args.iters} iterations.")


if __name__ == "__main__":
    main()
