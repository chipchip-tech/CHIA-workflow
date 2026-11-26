#!/usr/bin/env python3
"""clean_runs.py - dọn thư mục chạy của mọi project trong WORK_DIR (mặc định ~/chia_demo) để tiết kiệm đĩa.

Mặc định CHỈ LIỆT KÊ (không xoá gì). Thêm --yes để xoá thật. Chạy trên host (không cần cluster):
  python flow/clean_runs.py                 # xem dung lượng và kế hoạch
  python flow/clean_runs.py --yes           # làm theo kế hoạch
  python flow/clean_runs.py --only demo-alu/CG   # chỉ một project (<group>/<project>)

Kế hoạch, cho từng project (thư mục <WORK_DIR>/<group>/<project>/ có baseline-*, agent-*, sweep-*):
  - giữ --keep-baseline lần baseline OK mới nhất (mặc định 1: agent.py dùng bản này làm mốc) và baseline mà các lần
    agent được giữ đã dùng làm mốc; xoá các baseline khác (kể cả bản lỗi);
  - giữ --keep-agent lần agent mới nhất (mặc định 2), xoá các lần cũ hơn;
  - giữ --keep-sweep lần fpga_sweep.py mới nhất cho mỗi chế độ fmax/power (mặc định 1), xoá các lần cũ hơn;
  - ở các lần được giữ: xoá file trung gian lớn (binary Verilator obj/, checkpoint Vivado .dcp, .Xil, xsim.dir,
    ORFS objects/ và các .odb trừ 6_final.odb, thư mục mô phỏng xsim_pw/xsim_gls của FPGA, obj/ của GLS ASIC).
    SAIF (xsim_pw/act.saif) và netlist funcsim.v vẫn giữ. Giữ log, báo cáo, VCD, bitstream, GDS, ảnh layout, results/, profile/.
"""
import argparse
import json
import os
import shutil
from pathlib import Path



def size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    total = 0
    for dp, _, fns in os.walk(p):
        for f in fns:
            try:
                total += os.lstat(os.path.join(dp, f)).st_size
            except OSError:
                pass
    return total


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def baseline_ok(d: Path) -> bool:
    try:
        return json.loads((d / "results" / "baseline.json").read_text()).get("stopped_at") is None
    except (OSError, ValueError):
        return False


def intermediates(run: Path) -> list[Path]:
    """File trung gian lớn trong một lần chạy (baseline: run/, agent: run/<step>-r<k>/)."""
    out = []
    trees = [run] + [d for d in run.iterdir() if d.is_dir() and (d / "rtl").is_dir()]
    for t in trees:
        out += [t / "sim/build/obj", t / "fpga/out/post_route.dcp", t / "fpga/.Xil", t / "fpga/xsim.dir",
                t / "fpga/xsim_pw/xsim.dir", t / "fpga/xsim_pw/.Xil", t / "fpga/xsim_gls",
                t / "asic/orfs/objects", t / "asic/gls/build/obj"]
        for res in (t / "asic/orfs/results").glob("*/*/*"):
            out += [f for f in res.glob("*.odb") if f.name != "6_final.odb"]
    return [p for p in out if p.exists()]


def remove(p: Path) -> str:
    try:
        shutil.rmtree(p) if p.is_dir() and not p.is_symlink() else p.unlink()
        return ""
    except OSError as e:
        return str(e)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work-dir", default=os.environ.get("WORK_DIR", os.path.expanduser("~/chia_demo")))
    ap.add_argument("--keep-baseline", type=int, default=1)
    ap.add_argument("--keep-agent", type=int, default=2)
    ap.add_argument("--keep-sweep", type=int, default=1, help="số lần fpga_sweep.py giữ lại cho mỗi chế độ")
    ap.add_argument("--yes", action="store_true", help="xoá thật (mặc định chỉ liệt kê)")
    ap.add_argument("--only", help="chỉ project này (<group>/<project>)")
    args = ap.parse_args()

    root = Path(os.path.realpath(args.work_dir))
    if not root.is_dir():
        raise SystemExit(f"không có {root}")
    projects = sorted({p.parent for pat in ("*/*/baseline-*", "*/*/agent-*", "*/*/sweep-*") for p in root.glob(pat)})
    if args.only:
        projects = [d for d in projects if d.relative_to(root).as_posix() == args.only.strip("/")]
    delete, slim = [], []
    for d in projects:
        demo = d.relative_to(root).as_posix()
        bases = sorted(d.glob("baseline-*"), reverse=True)
        keep_b = [b for b in bases if baseline_ok(b)][:args.keep_baseline]
        agents = sorted(d.glob("agent-*"), reverse=True)
        keep_a = agents[:args.keep_agent]
        # Baseline mà lần agent được giữ dùng làm mốc cũng phải giữ (compare.md, ảnh layout trên status.html).
        for a in keep_a:
            try:
                ref = Path(json.loads((a / "results" / "agent.json").read_text())["baseline"]).name
            except (OSError, ValueError, KeyError):
                continue
            keep_b += [b for b in bases if b.name == ref and b not in keep_b]
        sweeps, keep_s = [], []
        for mode in ("fmax", "power"):
            s = sorted(d.glob(f"sweep-{mode}-*"), reverse=True)
            sweeps += s
            keep_s += s[:args.keep_sweep]
        print(f"== {demo}")
        for run in bases + agents + sweeps:
            keep = run in keep_b or run in keep_a or run in keep_s
            print(f"   {'GIỮ' if keep else 'XOÁ'}  {run.name:28s} {human(size(run)):>8s}")
            (slim if keep else delete).append(run)
    slim_files = [p for run in slim for p in intermediates(run)]
    freed = sum(size(p) for p in delete) + sum(size(p) for p in slim_files)
    print(f"\nXoá {len(delete)} lần chạy, dọn {len(slim_files)} file/thư mục trung gian ở lần được giữ: "
          f"giải phóng khoảng {human(freed)} (WORK_DIR hiện {human(size(root))}).")
    if not args.yes:
        print("Chưa xoá gì. Chạy lại với --yes để xoá.")
        return
    errs = [(p, e) for p in delete + slim_files if (e := remove(p))]
    for p, e in errs:
        print(f"LỖI xoá {p}: {e}")
    print(f"Xong. WORK_DIR còn {human(size(root))}." + (f" {len(errs)} mục không xoá được." if errs else ""))


if __name__ == "__main__":
    main()
