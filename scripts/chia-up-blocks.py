#!/usr/bin/env python3
"""Bật cluster CHIA theo khối từ configs/cluster-blocks.template.yaml.

Mặc định bật chipyard + verilator + claude. Tuỳ chọn:
  --fpga        bật khối vivado
  --asic        bật khối asic (cần image chia-orfs:latest)
  --no-claude   tắt khối claude
  --dry-run     chỉ sinh cluster.gen.yaml và in ra, không `chia up`

Chạy trong env chia_env:
  python scripts/chia-up-blocks.py [--fpga] [--asic]
Tắt cluster:
  chia down cluster.gen.yaml -y
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = REPO / "configs" / "cluster-blocks.template.yaml"
DEFAULTS = {
    "CHIPYARD_FORK": str(Path.home() / "tools" / "RISCVTutorial"),
    "ORFS_DIR": str(Path.home() / "tools" / "OpenROAD-flow-scripts"),
    "XILINX_DIR": str(Path.home() / "tools" / "Xilinx"),
    "VIVADO_VERSION": "2025.1",
    "JVM_CACHE": str(Path.home() / "chia_cache" / "jvm"),
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
    ap.add_argument("--out", default="cluster.gen.yaml", help="file config sinh ra (mặc định ./cluster.gen.yaml)")
    ap.add_argument("--dry-run", action="store_true", help="không chạy chia up")
    args = ap.parse_args()

    for k, v in DEFAULTS.items():
        os.environ.setdefault(k, v)
    # Bazzite: /home là symlink tới /var/home. setup-chipyard-fork.sh dựng fork theo đường dẫn
    # thật (/var/home/...), và env.sh/conda env ghi cứng đường dẫn đó, nên mount cũng phải dùng nó.
    for k in ("CHIPYARD_FORK", "ORFS_DIR", "XILINX_DIR", "JVM_CACHE"):
        os.environ[k] = os.path.realpath(os.environ[k])
    # Tạo trước thư mục cache, nếu không docker tạo chúng với chủ root và container không ghi được.
    for sub in ("coursier", "sbt", "ivy2"):
        (Path(os.environ["JVM_CACHE"]) / sub).mkdir(parents=True, exist_ok=True)
    if not os.environ.get("THIS_MACHINE"):
        os.environ["THIS_MACHINE"] = detect_ip() or fail("không dò được IP, hãy export THIS_MACHINE")

    fork = Path(os.environ["CHIPYARD_FORK"])
    if not fork.is_absolute():
        fail(f"CHIPYARD_FORK phải là đường dẫn tuyệt đối: {fork}")
    if not (fork / "env.sh").is_file():
        fail(f"{fork}/env.sh không có: fork chưa dựng xong (scripts/setup-chipyard-fork.sh)")

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

    header = (f"# SINH TỰ ĐỘNG bởi scripts/chia-up-blocks.py từ {TEMPLATE.name}. Đừng sửa tay.\n"
              f"# Khối bật: {', '.join(n for n, on in enabled.items() if on)}\n"
              f"# CHIPYARD_FORK={fork}  ORFS_DIR={os.environ['ORFS_DIR']}  VIVADO_VERSION={os.environ['VIVADO_VERSION']}"
              f"  JVM_CACHE={os.environ['JVM_CACHE']}\n")
    body = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True, width=1000)
    # CHIA không thay ${VAR} trong worker_setup_commands (chỉ trong run_options), nên tự thay
    # các biến của template ở đây. ${THIS_MACHINE}, ${USER} để CHIA thay như các config khác.
    for k in ("CHIPYARD_FORK", "ORFS_DIR", "XILINX_DIR", "VIVADO_VERSION", "JVM_CACHE"):
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
