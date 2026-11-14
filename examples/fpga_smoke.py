"""Kiểm tra khối vivado bằng flow `fpga/` của Chipyard: build bitstream từ đầu qua job CHIA.

Chạy trên worker có resource `vivado` (bật bằng `python scripts/chia-up-blocks.py --fpga`). Worker đã nạp
env.sh của Chipyard và settings64.sh của Vivado; container của CHIA luôn có --net=host (thiếu thì Vivado
2025.1 sập ở bước kiểm license sau route, xem docs/cluster-blocks.md mục 4).

Mặc định: SUB_PROJECT=arty100t, CONFIG=RocketArty100TConfig (config có sẵn của Chipyard 1.15.0), cần board file
Digilent arty-a7-100 trong Vivado. Chạy từ thư mục chứa file này:
  chia job submit --working-dir . -- python fpga_smoke.py [--config CFG] [--clean]
--clean: xoá thư mục build của config và .classpath_cache/chipyard_fpga.jar, tức chạy lại cả sbt, elaborate,
         synth, place, route (khoảng 1 giờ).

Xong khi in `KẾT QUẢ: OK`: make thoát 0, có file .bit mới, đọc được WNS/WHS từ report/timing.txt.
"""
import argparse
import os
import shutil
import subprocess
import time

import ray
from chia.base.ChiaFunction import ChiaFunction, get

# SUB_PROJECT -> (MODEL_PACKAGE, MODEL, CONFIG mặc định), khớp fpga/Makefile của Chipyard 1.15.0.
SUB_PROJECTS = {
    "arty100t": ("chipyard.fpga.arty100t", "Arty100THarness", "RocketArty100TConfig"),
}


def _timing(path: str) -> dict:
    """Đọc dòng số ngay dưới tiêu đề WNS(ns) của 'Design Timing Summary'."""
    try:
        lines = open(path).read().splitlines()
    except OSError as e:
        return {"error": str(e)}
    for i, ln in enumerate(lines):
        if ln.strip().startswith("WNS(ns)"):
            head, vals = ln.split(), lines[i + 2].split()
            nums = [v for v in vals if v.replace(".", "", 1).lstrip("-").isdigit()]
            # Thứ tự cột: WNS TNS TNSfail TNStotal WHS THS THSfail THStotal WPWS TPWS ...
            try:
                return {"WNS": float(nums[0]), "TNS": float(nums[1]), "WHS": float(nums[4]),
                        "THS": float(nums[5]), "THS_failing": int(float(nums[6]))}
            except (IndexError, ValueError):
                return {"error": f"không đọc được: {head} / {vals}"}
    return {"error": "không thấy WNS(ns)"}


@ChiaFunction(resources={"vivado": 1})
def build_bitstream(sub: str, config: str, clean: bool, timeout_s: int) -> dict:
    riscv = os.environ.get("RISCV") or ""
    cy = os.path.dirname(os.path.dirname(riscv))  # RISCV = <cy>/.conda-env/riscv-tools
    if not os.path.isfile(os.path.join(cy, "fpga", "Makefile")):
        return {"ok": False, "error": f"không tìm được Chipyard từ RISCV={riscv!r}"}
    pkg, model, _ = SUB_PROJECTS[sub]
    fpga = os.path.join(cy, "fpga")
    build = os.path.join(fpga, "generated-src", f"{pkg}.{model}.{config}")
    bit = os.path.join(build, "obj", f"{model}.bit")
    info = {"chipyard": cy, "build_dir": build,
            "vivado": subprocess.run(["vivado", "-version"], capture_output=True, text=True).stdout.split("\n")[0]}
    if clean:
        shutil.rmtree(build, ignore_errors=True)
        jar = os.path.join(cy, ".classpath_cache", "chipyard_fpga.jar")
        if os.path.exists(jar):
            os.remove(jar)
    log = os.path.join(fpga, f"build-{sub}-{config}-smoke.log")
    t0 = time.time() - 2  # mtime của kernel dùng đồng hồ thô, có thể sớm hơn time.time() vài ms
    cmd = ["make", f"SUB_PROJECT={sub}", f"CONFIG={config}", "bitstream"]
    try:
        with open(log, "w") as f:
            rc = subprocess.run(cmd, cwd=fpga, stdout=f, stderr=subprocess.STDOUT, timeout=timeout_s).returncode
    except subprocess.TimeoutExpired:
        rc = "timeout"
    info.update({"cmd": " ".join(cmd), "rc": rc, "minutes": round((time.time() - t0) / 60, 1), "log": log})
    fresh = os.path.exists(bit) and os.path.getmtime(bit) >= t0
    info["bit"] = f"{bit} ({os.path.getsize(bit)} byte)" if fresh else f"không có .bit mới ({bit})"
    info["timing"] = _timing(os.path.join(build, "obj", "report", "timing.txt"))
    info["ok"] = rc == 0 and fresh and "error" not in info["timing"]
    if not info["ok"]:
        try:
            info["log_tail"] = open(log).read()[-4000:]
        except OSError:
            pass
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub-project", default="arty100t", choices=sorted(SUB_PROJECTS))
    ap.add_argument("--config", help="mặc định là config có sẵn của SUB_PROJECT")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--timeout-hours", type=float, default=3.0)
    args = ap.parse_args()
    config = args.config or SUB_PROJECTS[args.sub_project][2]

    ray.init(address="auto", ignore_reinit_error=True)
    if not ray.cluster_resources().get("vivado"):
        print("[vivado] khối vivado chưa bật (chia-up-blocks.py --fpga)")
        print("KẾT QUẢ: CÓ LỖI")
        return
    r = get(build_bitstream.chia_remote(args.sub_project, config, args.clean, int(args.timeout_hours * 3600)))
    tail = r.pop("log_tail", None)
    for k, v in r.items():
        print(f"[vivado] {k}: {v}")
    if tail:
        print("[vivado] đuôi log:\n" + tail)
    t = r.get("timing", {})
    if r.get("ok") and t.get("WHS", 0) < 0:
        print(f"[vivado] lưu ý: hold âm (WHS={t['WHS']} ns, {t.get('THS_failing')} endpoint); flow vẫn ra bitstream")
    print("KẾT QUẢ:", "OK" if r.get("ok") else "CÓ LỖI")


if __name__ == "__main__":
    main()
