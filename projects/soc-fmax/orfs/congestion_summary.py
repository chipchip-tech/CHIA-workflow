#!/usr/bin/env python3
"""Tóm tắt congestion.rpt của global route ORFS (GRT-0116): số ô tràn theo lớp, theo vùng lưới 8x8 trên die, và
theo khối thiết kế (tiền tố tên net), để biết chỗ nghẽn nằm đâu. Chỉ đọc file.

  python orfs/congestion_summary.py ~/tools/OpenROAD-flow-scripts/flow/reports/sky130hd/soc_fmax/<variant>/congestion.rpt
"""
import re
import sys
from collections import Counter

BBOX = re.compile(r"bbox\s*=\s*\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)\s*-\s*\(\s*([-\d.]+)\s*,\s*([-\d.]+)\s*\)"
                  r"(?:\s*on Layer\s*(\S+))?")
NET = re.compile(r"net:(\S+)")


def block(name: str, depth: int) -> str:
    parts = re.split(r"[/.]", name.replace("\\", ""))
    return "/".join(parts[:depth])


def main(path: str, n: int = 8, depth: int = 4):
    boxes, nets, kinds = [], Counter(), Counter()
    cur_nets = []
    for line in open(path, errors="replace"):
        if line.startswith("violation type"):
            kinds[line.split(":", 1)[1].strip()] += 1
            cur_nets = []
        for m in NET.finditer(line):
            cur_nets.append(m.group(1))
        m = BBOX.search(line)
        if m:
            x1, y1, x2, y2 = map(float, m.group(1, 2, 3, 4))
            boxes.append(((x1 + x2) / 2, (y1 + y2) / 2, m.group(5) or "?"))
            for b in {block(x, depth) for x in cur_nets}:
                nets[b] += 1
    if not boxes:
        print("không đọc được bbox nào: gửi 30 dòng đầu file")
        return
    print(f"{len(boxes)} vùng tràn; loại: {dict(kinds)}")
    print("theo lớp:", dict(Counter(b[2] for b in boxes)))
    xs, ys = [b[0] for b in boxes], [b[1] for b in boxes]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    print(f"toạ độ tâm (um): x {x0:.0f}..{x1:.0f}, y {y0:.0f}..{y1:.0f}")
    grid = Counter()
    for x, y, _ in boxes:
        i = min(int((x - x0) / max(x1 - x0, 1) * n), n - 1)
        j = min(int((y - y0) / max(y1 - y0, 1) * n), n - 1)
        grid[(i, j)] += 1
    print(f"lưới {n}x{n} (hàng trên = y lớn):")
    for j in reversed(range(n)):
        print("  " + " ".join(f"{grid[(i, j)]:6d}" for i in range(n)))
    print(f"khối (tiền tố {depth} cấp của net qua vùng tràn), 25 khối nhiều nhất:")
    for b, c in nets.most_common(25):
        print(f"  {c:7d}  {b}")


if __name__ == "__main__":
    main(sys.argv[1])
