"""Bước 0 của minsoc-fmax: kiểm môi trường, KHÔNG build. Chạy trên cluster `chia-up-blocks.py --fpga`.

  chipyard  fork (suy từ $RISCV), fork + submodule có sạch không, Config đã cài chưa, chữ ký
            WithArty100TTweaks, fpga harness có cần vá wf_active không, riscv-tests ISA + 8 benchmark
  vivado    vivado trên PATH + phiên bản, board files Arty A7-100, board part/part trong fpga-shells,
            Vivado mở được part xc7a100tcsg324-1 (license WebPACK), RAM trống

  cd ~/CHIA-workflow/projects/minsoc-fmax
  chia job submit --working-dir . -- python probe.py
"""
import glob
import os
import subprocess

import ray
from chia.base.ChiaFunction import ChiaFunction, get

PART = "xc7a100tcsg324-1"
BENCH = ["dhrystone", "median", "multiply", "qsort", "towers", "vvadd", "mm", "spmv"]
OWN = ("MinSocFmaxConfig.scala", "MinSocArty100TConfigs.scala", "MinimalSoCConfig.scala")


def sh(cmd: str, cwd=None, timeout=600) -> str:
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return (p.stdout + p.stderr).strip() + ("" if p.returncode == 0 else f"\n[rc={p.returncode}]")


def fork_dir() -> str:
    riscv = os.environ.get("RISCV") or ""
    return os.path.realpath(os.path.join(riscv, "..", "..")) if riscv else ""


@ChiaFunction(resources={"chipyard": 1})
def elaborate_fpga(config: str, jobs: int = 8) -> dict:
    """Chỉ elaborate (Chisel -> Verilog) config FPGA, không Vivado. ~10-20 phút."""
    cy = fork_dir()
    log = f"{cy}/fpga/minsoc-elab-{config}.log"
    out = sh(f"cd {cy}/fpga && make -j{jobs} SUB_PROJECT=arty100t CONFIG_PACKAGE=chipyard.fpga.arty100t "
             f"CONFIG={config} verilog > {log} 2>&1; echo RC=$?", timeout=5400)
    tail = sh(f"tail -40 {log}")
    gen = sh(f"ls -d {cy}/fpga/generated-src/*{config}* 2>/dev/null; "
             f"grep -l -iE 'InclusiveCache' {cy}/fpga/generated-src/*{config}*/gen-collateral/*.sv 2>/dev/null | head -3; "
             f"grep -ohE 'cache-size = <[0-9]+>' {cy}/fpga/generated-src/*{config}*/*.dts 2>/dev/null | sort | uniq -c")
    return {"rc": out.strip().splitlines()[-1], "log": log, "tail": tail, "generated": gen}


@ChiaFunction(resources={"chipyard": 1})
def probe_chipyard() -> dict:
    cy = fork_dir()
    r = {"RISCV": os.environ.get("RISCV"), "fork": cy, "env_sh": os.path.exists(f"{cy}/env.sh")}
    if not r["env_sh"]:
        return r
    st = sh("git status --short; git submodule foreach --quiet 'git status --short | sed \"s|^|$sm_path: |\"'", cwd=cy)
    r["dirty"] = [l for l in st.splitlines() if l.strip() and not any(o in l for o in OWN)]
    r["heads"] = sh("git rev-parse --short HEAD; for s in generators/rocket-chip generators/sifive-cache "
                    "generators/testchipip; do echo \"$s $(git -C $s rev-parse --short HEAD)\"; done", cwd=cy)
    r["configs_installed"] = {o: bool(glob.glob(f"{cy}/**/{o}", recursive=True)) for o in OWN}
    r["arty_tweaks"] = sh("grep -n 'class WithArty100TTweaks' fpga/src/main/scala/arty100t/*.scala", cwd=cy)
    r["arty_configs"] = sh("grep -n '^class' fpga/src/main/scala/arty100t/Configs.scala | head -20", cwd=cy)
    r["wf_active_in_chipyard"] = sh("grep -rln 'wf_active' generators/chipyard/src | head -5 || true", cwd=cy)
    r["fpga_shells_arty100"] = sh("ls fpga/fpga-shells/xilinx | grep -i arty; "
                                  "grep -rhoE '(board_part|BOARD_PART|part)[^\\n]{0,80}' fpga/fpga-shells/xilinx/arty_a7_100/tcl "
                                  "2>/dev/null | head -10", cwd=cy)
    r["arty_scala"] = sh("ls fpga/src/main/scala/arty100t/; grep -n -iE 'ddr|mig|extmem|mem_axi|TLMem|MemPort|HarnessBinder\\(' "
                         "fpga/src/main/scala/arty100t/*.scala | grep -v MinSoc | head -40", cwd=cy)
    share = os.path.join(os.environ.get("RISCV", ""), "riscv64-unknown-elf", "share", "riscv-tests")
    isa = sorted(os.path.basename(p) for p in glob.glob(f"{share}/isa/rv64*-p-*") if not p.endswith(".dump"))
    r["isa_dir"], r["isa_count"] = f"{share}/isa", len(isa)
    r["isa_sample"] = isa[:3] + isa[-3:]
    r["bench"] = {b: [p for p in glob.glob(f"{share}/benchmarks/{b}.riscv")] for b in BENCH}
    r["mem_free"] = sh("free -g | head -2")
    return r


VIV_TCL = f"""create_project -in_memory -part {PART}
puts "@@PART [get_property PART [current_project]]"
puts "@@BOARDS [get_board_parts -quiet *arty-a7-100*]"
exit 0
"""


@ChiaFunction(resources={"vivado": 1})
def probe_vivado() -> dict:
    xv = os.environ.get("XILINX_VIVADO", "")
    root = os.path.realpath(os.path.join(xv, "..", "..")) if xv else ""   # .../Xilinx
    r = {"XILINX_VIVADO": xv, "which": sh("which vivado"), "version": sh("vivado -version | head -1")}
    boards = glob.glob(f"{root}/**/arty-a7-100/*/board.xml", recursive=True) if root else []
    r["board_xml"] = boards
    r["board_part"] = sh("grep -ohE 'part_name=\"[^\"]+\"' " + " ".join(boards) + " | sort -u") if boards else "KHÔNG THẤY"
    tcl = "/tmp/minsoc_probe.tcl"
    with open(tcl, "w") as f:
        f.write(VIV_TCL)
    repo = os.path.dirname(os.path.dirname(os.path.dirname(boards[0]))) if boards else ""
    pre = f"set_param board.repoPaths [list {repo}]\n" if repo else ""
    with open("/tmp/minsoc_pre.tcl", "w") as f:
        f.write(pre + f"source {tcl}\n")
    out = sh("cd /tmp && vivado -mode batch -nojournal -nolog -source /tmp/minsoc_pre.tcl", timeout=900)
    r["vivado_part_check"] = [l for l in out.splitlines() if l.startswith(("@@", "ERROR", "CRITICAL"))] or out[-1500:]
    r["mem_free"] = sh("free -g | head -2")
    return r


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--elaborate", default=None, metavar="CONFIG",
                    help="thêm: elaborate config FPGA (vd MinSocArty100TF100Config), không chạy Vivado")
    args = ap.parse_args()
    ray.init(address="auto", namespace="minsoc_fmax", ignore_reinit_error=True)
    res = ray.cluster_resources()
    ok = True
    for name, fn in (("chipyard", probe_chipyard), ("vivado", probe_vivado)):
        if res.get(name, 0) < 1:
            print(f"[{name}] THIẾU khối (chia-up-blocks.py --fpga?)")
            ok = False
            continue
        r = get(fn.chia_remote())
        print(f"\n===== [{name}] =====")
        for k, v in r.items():
            print(f"{k}: {v}")
        if name == "chipyard":
            miss = [b for b, v in r.get("bench", {}).items() if not v]
            if not r.get("env_sh") or r.get("dirty") or miss or r.get("isa_count", 0) < 50 \
                    or not all(r.get("configs_installed", {}).values()):
                ok = False
                print(f"!! chipyard: dirty={r.get('dirty')} thiếu benchmark={miss}")
        else:
            if not r["board_xml"] or not any("@@PART" in l for l in r["vivado_part_check"]):
                ok = False
    if args.elaborate:
        e = get(elaborate_fpga.chia_remote(args.elaborate))
        print(f"\n===== [elaborate {args.elaborate}] =====")
        for k, v in e.items():
            print(f"{k}: {v}")
        ok = ok and e["rc"] == "RC=0"
    print("\nKẾT QUẢ:", "OK" if ok else "CÓ VẤN ĐỀ (gửi lại toàn bộ log)")


if __name__ == "__main__":
    main()
