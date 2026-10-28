"""Probe: can an opencode model call the MCP tool chipyard_bash, per builtin_tools mode?

fmax-ab-t1 (--ab-tool, builtin tools off): opencode printed no event in 45 min and the MCP
servers saw no tool call. This runs the same setup (SalvageOpenCodeLLM + BashTool "chipyard_bash")
with a tiny prompt and a short timeout, once per model and per builtin_tools mode (see
SalvageOpenCodeLLM), with opencode's logs on stderr, so a hang shows where it stops.
probe-tools (27/10/2026): "off" -> "Free usage limit reached" then a hang; "on" works.
--agent claude runs the same check with SalvageClaudeLLM (`--tools ""`, --effort), once per model.

    chia job submit --working-dir . -- python probe_tools.py --models opencode/big-pickle
    chia job submit --working-dir . -- python probe_tools.py --agent claude --models sonnet
"""
import argparse
import time
from pathlib import Path

import ray

import fmax_nodes as N
from chia.base.ChiaFunction import get
from chia.base.tools.BashTool import BashTool

PROMPT = ("Use the tool {tool} to run exactly this command: "
          "echo $((6*7))-PING && git -C /home/ray/chipyard rev-parse --short HEAD\n"
          "Then reply with the command output only.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--agent", choices=["opencode", "claude"], default="opencode")
    p.add_argument("--effort", default="medium", help="--agent claude: claude --effort")
    p.add_argument("--models", nargs="+", default=None, help="default: opencode/big-pickle, or sonnet (claude)")
    p.add_argument("--builtin", nargs="+", choices=N.SalvageOpenCodeLLM.MODES,
                   default=["deny", "readonly", "on"])
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--log-level", default="INFO", help="opencode --log-level (DEBUG/INFO/WARN)")
    p.add_argument("--out", default=str(Path.home() / "rocket-fmax-out" / "probe-tools"))
    a = p.parse_args()
    claude = a.agent == "claude"
    a.models = a.models or ["sonnet" if claude else "opencode/big-pickle"]
    modes = ["-"] if claude else a.builtin
    read_partial = N.read_text_claude if claude else N.read_text_opencode
    prompt = PROMPT.format(tool="mcp__chipyard_bash__chipyard_bash_run_command" if claude
                           else "chipyard_bash_chipyard_bash_run_command")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    ray.init(address="auto", namespace="rocket_fmax", ignore_reinit_error=True)
    rows = []
    for model in a.models:
        for builtin in modes:
            if claude:
                name = f"claude-{model.split('/')[-1]}_effort-{a.effort}"
                llm = N.SalvageClaudeLLM(model=model, system_message="You are a test agent.",
                                         timeout_seconds=a.timeout, retries=1, effort=a.effort, max_turns=10)
            else:
                name = f"{model.split('/')[-1]}_builtin-{builtin}"
                llm = N.SalvageOpenCodeLLM(model=model, system_message="You are a test agent.",
                                           timeout_seconds=a.timeout, retries=1, builtin_tools=builtin,
                                           extra_cli_args=["--print-logs", "--log-level", a.log_level])
            tool = BashTool("chipyard_bash", N.CHIPYARD, timeout_seconds=120,
                            task_options={"resources": {"chipyard": 1}})
            get(read_partial.chia_remote(N.LLM_PARTIAL_FILE, True))
            t0 = time.time()
            try:
                r = get(llm.prompt.chia_remote(llm, prompt, tools=[tool]))
                res, stream, rc, err = r.result, r.stream_result, r.returncode, getattr(r, "stderr", "")
            except Exception as e:                                   # noqa: BLE001
                res, stream, rc, err = "", "", "exc", f"{type(e).__name__}: {e}"
            finally:
                tool.stop()
            dt = time.time() - t0
            partial = get(read_partial.chia_remote(N.LLM_PARTIAL_FILE, True)) or ""
            called = "chipyard_bash" in (stream or "") or "chipyard_bash" in partial
            ok = "42-PING" in (res or "")
            (out / f"{name}.txt").write_text(
                f"model {model} agent={a.agent} builtin={builtin} rc={rc} {dt:.0f}s\n\n== result\n{res}\n\n"
                f"== stream\n{stream}\n\n== stderr\n{(err or '')[-20000:]}\n\n== partial (timeout)\n{partial}\n")
            rows.append(f"{name:50s} rc={rc!s:4s} {dt:5.0f}s tool_in_stream={called} answer_ok={ok}")
            print(rows[-1], flush=True)
    (out / "summary.txt").write_text("\n".join(rows) + "\n")
    print(f"\n{out}/summary.txt và {out}/<tên>.txt")


if __name__ == "__main__":
    main()
