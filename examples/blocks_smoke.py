"""Kiểm tra các khối của configs/cluster-blocks.template.yaml đang bật.

  chipyard   môi trường fork + build Verilator qua ChiselBuildNode
  vivado     `vivado -version`            (chỉ khi bật --fpga)
  asic       `openroad -version`, `yosys -V` (chỉ khi bật --asic)

Khối nào không có trong cluster thì bỏ qua. Chạy từ thư mục chứa file này:
  chia job submit --working-dir . -- python blocks_smoke.py [--config RocketConfig] [--clean]
--clean: `make clean` trước khi build, tức build lại từ đầu (lâu hơn nhiều).
"""
import argparse
import os
import subprocess

import ray
from chia.base.ChiaFunction import ChiaFunction, get
from chia.chipyard.chisel_build_node import BuildTarget, ChiselBuildNode


def _run(cmd: list[str]) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return (p.stdout + p.stderr).strip().splitlines()[0] if p.returncode == 0 else f"LỖI ({p.returncode}): {p.stderr[-300:]}"


def _env() -> dict:
    d = {k: os.environ.get(k) for k in ("RISCV", "CONDA_PREFIX")}
    d["verilator"] = os.popen("which verilator").read().strip()
    return d


@ChiaFunction(resources={"chipyard": 1})
def probe_chipyard() -> dict:
    return _env()


@ChiaFunction(resources={"vivado": 1})
def probe_vivado() -> dict:
    return {"which": os.popen("which vivado").read().strip(), "version": _run(["vivado", "-version"])}


@ChiaFunction(resources={"asic": 1})
def probe_asic() -> dict:
    flow = "/OpenROAD-flow-scripts/flow"
    return {"openroad": _run(["openroad", "-version"]), "yosys": _run(["yosys", "-V"]),
            "flow_writable": os.access(flow, os.W_OK)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="RocketConfig")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--no-build", action="store_true", help="chỉ kiểm môi trường, không build")
    args = ap.parse_args()

    ray.init(address="auto", ignore_reinit_error=True)
    res = ray.cluster_resources()
    ok = True

    env = get(probe_chipyard.chia_remote())
    print("[chipyard] env:", env)
    fork = os.path.dirname(os.path.dirname(env["RISCV"] or "")) if env["RISCV"] else None
    if not (fork and env["verilator"]):
        print("[chipyard] LỖI: worker chưa nạp env.sh của fork")
        ok = False
    elif not args.no_build:
        node = ChiselBuildNode(fork, args.config, target=BuildTarget.VERILATOR,
                               make_jobs=16, timeout_seconds=7200, clean=args.clean)
        art = get(node.build.options(resources={"chipyard": 1}).chia_remote(node))
        print(f"[chipyard] build {args.config} (clean={args.clean}): SUCCESS={art.success}")
        if not art.success:
            print((art.stdout + art.stderr)[-3000:])
            ok = False

    if res.get("vivado"):
        print("[vivado]", get(probe_vivado.chia_remote()))
    else:
        print("[vivado] tắt, bỏ qua")
    if res.get("asic"):
        print("[asic]", get(probe_asic.chia_remote()))
    else:
        print("[asic] tắt, bỏ qua")

    print("KẾT QUẢ:", "OK" if ok else "CÓ LỖI")


if __name__ == "__main__":
    main()
