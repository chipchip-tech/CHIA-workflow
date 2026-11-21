#!/usr/bin/env python3
"""Bật cluster CHIA theo khối từ configs/cluster-blocks.template.yaml.

Mặc định bật chipyard + verilator + claude. Tuỳ chọn:
  --fpga        bật khối vivado
  --asic        bật khối asic (cần image chia-orfs:latest)
  --no-claude   tắt khối claude
  --verilator-slots N   số slot verilator_run (mặc định 1): N sim chạy song song trong 1 container
  ASIC_MEM=20g  (biến môi trường) giới hạn RAM của container asic, mặc định 12g
  WORK_DIR=...  (biến môi trường) thư mục làm việc chung của mọi khối, mặc định ~/chia_demo
  --dry-run     chỉ sinh cluster.gen.yaml và in ra, không `chia up`

Chạy trong env chia_env:
  python scripts/chia-up-blocks.py [--fpga] [--asic]
Tắt cluster (đặt THIS_MACHINE như lúc bật rồi gọi `chia down cluster.gen.yaml -y`):
  python scripts/chia-up-blocks.py --down
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "configs" / "cluster-blocks.template.yaml"
DEFAULTS = {
    "CHIPYARD_FORK": str(Path.home() / "tools" / "chipyard"),
    "ORFS_DIR": str(Path.home() / "tools" / "OpenROAD-flow-scripts"),
    "XILINX_DIR": str(Path.home() / "tools" / "Xilinx"),
    "VIVADO_VERSION": "2025.1",
    "JVM_CACHE": str(Path.home() / "chia_cache" / "jvm"),
    "ASIC_MEM": "12g",
    "WORK_DIR": str(Path.home() / "chia_demo"),
}


def fail(msg: str) -> None:
    print(f"LỖI: {msg}", file=sys.stderr)
    sys.exit(1)


def detect_ip() -> str:
    out = subprocess.run(["ip", "route", "get", "1.1.1.1"], capture_output=True, text=True).stdout.split()
    return out[out.index("src") + 1] if "src" in out else ""


def image_exists(image: str) -> bool:
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fpga", action="store_true", help="bật khối vivado")
    ap.add_argument("--asic", action="store_true", help="bật khối asic")
    ap.add_argument("--no-claude", action="store_true", help="tắt khối claude")
    ap.add_argument("--verilator-slots", type=int, default=1, help="số slot verilator_run (mặc định 1)")
    ap.add_argument("--out", default="cluster.gen.yaml", help="file config sinh ra (mặc định ./cluster.gen.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="không chạy chia up")
    ap.add_argument("--down", action="store_true",
                    help="tắt cluster của --out (chia down -y) với THIS_MACHINE tự dò; thiếu biến này thì chia down "
                         "SSH vào host rỗng và không gỡ được gì")
    args = ap.parse_args()

    if not args.dry_run and shutil.which("chia") is None:
        fail("không thấy lệnh `chia` trên PATH: chưa `conda activate chia_env`? "
             f"(python đang chạy: {sys.executable})")

    if args.down:
        if not os.environ.get("THIS_MACHINE"):
            os.environ["THIS_MACHINE"] = detect_ip() or fail("không dò được IP, hãy export THIS_MACHINE")
        if not Path(args.out).is_file():
            fail(f"không có {args.out}: cluster được bật từ thư mục khác? dùng --out")
        print(f"chia down {args.out} -y (THIS_MACHINE={os.environ['THIS_MACHINE']})", flush=True)
        sys.exit(subprocess.run(["chia", "down", args.out, "-y"]).returncode)

    for k, v in DEFAULTS.items():
        os.environ.setdefault(k, v)
    # Bazzite: /home là symlink tới /var/home. setup-chipyard.sh dựng Chipyard theo đường dẫn
    # thật (/var/home/...), và env.sh/conda env ghi cứng đường dẫn đó, nên mount cũng phải dùng nó.
    for k in ("CHIPYARD_FORK", "ORFS_DIR", "XILINX_DIR", "JVM_CACHE", "WORK_DIR"):
        os.environ[k] = os.path.realpath(os.environ[k])
    # Tạo trước thư mục cache, nếu không docker tạo chúng với chủ root và container không ghi được.
    for sub in ("coursier", "sbt", "ivy2"):
        (Path(os.environ["JVM_CACHE"]) / sub).mkdir(parents=True, exist_ok=True)
    Path(os.environ["WORK_DIR"]).mkdir(parents=True, exist_ok=True)
    if not os.environ.get("THIS_MACHINE"):
        os.environ["THIS_MACHINE"] = detect_ip() or fail("không dò được IP, hãy export THIS_MACHINE")

    fork = Path(os.environ["CHIPYARD_FORK"])
    if not fork.is_absolute():
        fail(f"CHIPYARD_FORK phải là đường dẫn tuyệt đối: {fork}")
    if not (fork / "env.sh").is_file():
        fail(f"{fork}/env.sh không có: Chipyard chưa dựng xong (scripts/setup-chipyard.sh)")

    enabled = {"chipyard": True, "verilator": True, "claude": not args.no_claude,
               "vivado": args.fpga, "asic": args.asic}

    if args.fpga:
        settings = Path(os.environ["XILINX_DIR"]) / os.environ["VIVADO_VERSION"] / "Vivado/settings64.sh"
        if not settings.is_file():
            fail(f"không thấy {settings}: kiểm VIVADO_VERSION")
    if args.asic:
        if not image_exists("chia-orfs:latest"):
            fail("chưa có image chia-orfs:latest: docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker")
        if not (Path(os.environ["ORFS_DIR"]) / "env.sh").is_file():
            fail(f"{os.environ['ORFS_DIR']}/env.sh không có: chạy scripts/setup-orfs.sh")
    if enabled["claude"] and not (Path.home() / ".claude/.claude.json").is_file():
        fail("thiếu ~/.claude/.claude.json: chạy `claude` rồi `cp ~/.claude.json ~/.claude/.claude.json`, hoặc dùng --no-claude")

    cfg = yaml.safe_load(TEMPLATE.read_text())
    for name, node in cfg["available_node_types"].items():
        node["num_workers"] = 1 if enabled.get(name) else 0
    if args.verilator_slots < 1:
        fail("--verilator-slots phải >= 1")
    cfg["available_node_types"]["verilator"]["resources"]["verilator_run"] = args.verilator_slots

    header = (f"# SINH TỰ ĐỘNG bởi scripts/chia-up-blocks.py từ {TEMPLATE.name}. Đừng sửa tay.\n"
              f"# Khối bật: {', '.join(n for n, on in enabled.items() if on)}\n"
              f"# CHIPYARD_FORK={fork}  ORFS_DIR={os.environ['ORFS_DIR']}  VIVADO_VERSION={os.environ['VIVADO_VERSION']}"
              f"  JVM_CACHE={os.environ['JVM_CACHE']}\n"
              f"# ASIC_MEM={os.environ['ASIC_MEM']}  verilator_run={args.verilator_slots}  WORK_DIR={os.environ['WORK_DIR']}\n")
    body = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=1000)
    # CHIA không thay ${VAR} trong worker_setup_commands (chỉ trong run_options), nên tự thay
    # các biến của template ở đây. ${THIS_MACHINE}, ${USER} để CHIA thay như các config khác.
    for k in ("CHIPYARD_FORK", "ORFS_DIR", "XILINX_DIR", "VIVADO_VERSION", "JVM_CACHE", "ASIC_MEM", "WORK_DIR"):
        body = body.replace("${" + k + "}", os.environ[k])
    out = Path(args.out)
    out.write_text(header + body)
    print(f"Đã ghi {out}. Khối bật: {', '.join(n for n, on in enabled.items() if on)}")

    if args.dry_run:
        print(out.read_text())
        return
    sys.exit(subprocess.run(["chia", "up", str(out), "-y"]).returncode)


if __name__ == "__main__":
    main()
