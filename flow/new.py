#!/usr/bin/env python3
"""new.py - tạo khung project mới cho flow/ từ RTL và testbench có sẵn.

  python flow/new.py projects/<tên> --top <module> --rtl a.sv b.sv pkg.svh --tb tb_<module>.sv \\
      [--clock clk:10] [--pass "REGEX" ...] [--fail "REGEX" ...] [--steps sim,fpga,asic] \\
      [--part xc7a100tcsg324-1] [--platform sky130hd] [--title "..."]

Chép RTL vào rtl/ (header .svh/.vh cũng vào đó, được `include), testbench vào sim/, viết project.py và README.md.
Không viết gì khác: run_sim.sh, config.mk, constraint.sdc, build.tcl out-of-context... do flow sinh khi chạy.
Không có --pass thì testbench phải in dòng `RESULT: PASS|FAIL ...` (như demo ALU).
Quy ước để GLS (mô phỏng netlist) dùng lại được testbench: testbench chỉ đọc cổng của DUT (không tham chiếu tín hiệu
bên trong), và tự `include file khai báo kiểu/hằng nó dùng (không dựa vào khai báo nằm trong file RTL).
Và giữ reset >= 100 ns trước kích thích đầu tiên (GSR của netlist Xilinx, glbl.v).
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

TEMPLATE = '''"""project.py - khai báo cho flow/ (xem flow/README.md). Tạo bởi flow/new.py; sửa thoải mái."""

PROJECT = {{
    "dut": {top!r},
    "title": {title!r},
    "steps": {steps!r},
    "clocks": [{{"port": {clk!r}, "period_ns": {period}}}],
    "sim": {{
        "tb": {tb!r},
        "pass_patterns": {passes!r},          # None: testbench in dòng `RESULT: PASS|FAIL ...`
        "fail_patterns": {fails!r},
        "lint": "errors",                     # "strict": -Wall phải sạch
    }},
    "fpga": {{"part": {part!r}}},             # không có fpga/build.tcl: Vivado out-of-context, không bitstream
    "asic": {{"platform": {platform!r}}},
    "agent": {{
        "description": "TODO: describe the design in one or two sentences (interface, what it computes).",
        "sim_hints": "TODO: behaviours and corner cases the testbench should cover",
    }},
}}
'''

README = """# {name}

{title}

Tạo bởi `flow/new.py`. Nguồn: `rtl/` ({nrtl} file), `sim/{tbname}`; khai báo ở `project.py`.

```bash
python ~/CHIA-workflow/flow/run.py {rel} baseline
```
```bash
python ~/CHIA-workflow/flow/run.py {rel} agent --rounds 2
```

## Trạng thái

Chưa chạy.
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dir", help="thư mục project mới, ví dụ projects/ascon-core")
    ap.add_argument("--top", required=True, help="module top của RTL")
    ap.add_argument("--rtl", nargs="+", required=True, help="file RTL (.v .sv .svh .vh)")
    ap.add_argument("--tb", required=True, help="testbench Verilog/SystemVerilog tự kiểm")
    ap.add_argument("--clock", default="clk:10", help="cổng:chu_kỳ_ns (mặc định clk:10)")
    ap.add_argument("--pass", dest="passes", action="append", help="regex phải có trong log mô phỏng (lặp được)")
    ap.add_argument("--fail", dest="fails", action="append", help="regex không được có trong log (lặp được)")
    ap.add_argument("--steps", default="sim,fpga,asic")
    ap.add_argument("--part", default="xc7a100tcsg324-1")
    ap.add_argument("--platform", default="sky130hd")
    ap.add_argument("--title", default="")
    args = ap.parse_args()

    d = Path(args.dir).resolve()
    if d.exists() and any(d.iterdir()):
        raise SystemExit(f"LỖI: {d} đã có và không rỗng")
    port, _, period = args.clock.partition(":")
    files = [Path(f) for f in args.rtl]
    for f in files + [Path(args.tb)]:
        if not f.is_file():
            raise SystemExit(f"LỖI: không có {f}")
    text = "".join(f.read_text(errors="replace") for f in files)
    if not re.search(rf"\bmodule\s+{re.escape(args.top)}\b", text):
        raise SystemExit(f"LỖI: không thấy `module {args.top}` trong các file RTL")
    tbtext = Path(args.tb).read_text(errors="replace")
    if not re.search(rf"\b{re.escape(args.top)}\s+(?:#\s*\(.*?\)\s*)?\w+\s*\(", tbtext, re.S):
        print(f"CẢNH BÁO: testbench không có instance của {args.top}")
    if not args.passes and "RESULT:" not in tbtext:
        print("CẢNH BÁO: testbench không in `RESULT:` và chưa có --pass: flow sẽ không nhận ra PASS")
    hier = sorted(set(re.findall(r"\b(u_\w+|dut)\.(\w+)", tbtext)))
    if hier:
        print(f"CẢNH BÁO: testbench đọc tín hiệu bên trong DUT {hier[:5]}: GLS trên netlist sẽ lỗi (chỉ dùng cổng)")

    (d / "rtl").mkdir(parents=True)
    (d / "sim").mkdir()
    for f in files:
        shutil.copy2(f, d / "rtl" / f.name)
    shutil.copy2(args.tb, d / "sim" / Path(args.tb).name)
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    title = args.title or f"{args.top} (RTL block)"
    (d / "project.py").write_text(TEMPLATE.format(
        top=args.top, title=title, steps=steps, clk=port or "clk", period=float(period or 10),
        tb=f"sim/{Path(args.tb).name}", passes=args.passes, fails=args.fails or [], part=args.part,
        platform=args.platform))
    rel = d.relative_to(REPO).as_posix() if d.is_relative_to(REPO) else str(d)
    (d / "README.md").write_text(README.format(name=d.name, title=title, nrtl=len(files),
                                               tbname=Path(args.tb).name, rel=rel))
    sys.path.insert(0, str(REPO))
    from flow.project import Project                        # kiểm tra khai báo ngay
    p = Project(d)
    print(f"Đã tạo {d}: dut={p.dut}, rtl={p.cfg['rtl']}, tb={p.cfg['sim']['tb']} (instance {p.cfg['sim']['dut_instance']}),"
          f" FPGA {p.cfg['fpga']['mode']}, clock {p.clock()}")
    print("Việc còn lại: điền agent.description và agent.sim_hints trong project.py, rồi chạy baseline.")


if __name__ == "__main__":
    main()
