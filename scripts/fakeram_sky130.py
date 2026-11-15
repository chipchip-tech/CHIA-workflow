#!/usr/bin/env python3
"""Sinh SRAM giả (fakeram) cho sky130hd từ file *.mems.conf của Chipyard.

Mỗi dòng của mems.conf (do firtool --repl-seq-mem ghi) là một SyncReadMem đã được thay bằng module
`<name>` (thường đuôi `_ext`). Script sinh, cho mỗi macro, đúng tên module và đúng tên chân như
Verilog Chipyard sinh ra (RW0_addr, RW0_clk, ..., R0_data, W0_mask), nên không cần wrapper:

  <out>/fakeram_sky130.lef    LEF: CLASS BLOCK, chân tín hiệu met3 ở cạnh trái (thiếu chỗ thì cả cạnh
                              phải), sọc VPWR/VGND met4 dọc thân macro (để PDN của ORFS nối met4-met5),
                              OBS met1-met4 phủ toàn bộ.
  <out>/fakeram_sky130.lib    Liberty (NLDM): clock -> dữ liệu ra = --access-ns (+ theo tải), setup/hold
                              của mọi chân vào so với clock của cổng đó. Corner ghi danh nghĩa tt_025C_1v80.
  <out>/fakeram_sky130.gds    GDS: mỗi macro chỉ có đường bao (lớp prBoundary 235/4), cho bước gộp GDS.
  <out>/fakeram_sky130_bb.v   Verilog blackbox (synth/lint).
  <out>/fakeram.json          tham số + kích thước từng macro (ghi vào báo cáo).

Kích thước: diện tích = số bit x --um2-per-bit (mặc định 3: ô nhớ sky130 ~1,9 um2 + ngoại vi), hình
chữ nhật theo --aspect (rộng/cao), nới ra khi cạnh không đủ chỗ cho chân. Đây là mô hình giả: timing và
diện tích là giả định, giống nhau cho mọi lần đo, KHÔNG phải số của macro OpenRAM thật (macro OpenRAM
sky130 nhỏ cỡ ~20 um2/bit).

  python3 scripts/fakeram_sky130.py --conf <...>.top.mems.conf --out <dir> [--only a,b] [--access-ns 1.5]

Dùng như thư viện: `parse_conf(text)`, `generate(entries, out_dir, Params(...))`.
Chỉ dùng thư viện chuẩn của Python (chạy được cả trên host lẫn trong image ORFS).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import math
import re
import struct
import sys
from pathlib import Path

VERSION = "fr1"          # đổi khi đổi mô hình (đi vào tag cache của project)

# sky130hd (platforms/sky130hd/sky130hd.tracks, sky130_fd_sc_hd.tlef)
MFG = 0.005              # manufacturing grid, um
SITE_H = 2.72            # chiều cao hàng std cell
MET3_PITCH = 0.68        # met3 ngang
MET3_W = 0.30
PIN_DEPTH = 0.80         # chiều sâu hình chữ nhật chân, tính từ cạnh vào trong
PWR_W = 1.60             # độ rộng sọc nguồn met4 (như pdn.tcl của sky130hd)
PWR_PITCH = 13.60        # khoảng cách giữa hai sọc cùng loại là 2 * PWR_PITCH
PRBNDRY = (235, 4)       # sky130 prBoundary.boundary


@dataclasses.dataclass
class Params:
    access_ns: float = 1.5        # clk -> dữ liệu ra, ở tải 0
    setup_ns: float = 0.2
    hold_ns: float = 0.05
    um2_per_bit: float = 3.0
    aspect: float = 1.0           # rộng / cao
    min_side_um: float = 20.0
    pin_pitch_tracks: int = 2     # chân cách nhau bao nhiêu track met3
    out_res_ns_per_pf: float = 1.5  # trễ thêm theo tải (kiểu buf_4)
    in_cap_pf: float = 0.005
    clk_cap_pf: float = 0.02


@dataclasses.dataclass
class Port:
    name: str                     # tên chân, vd RW0_wdata
    direction: str                # input | output
    width: int
    use: str = "SIGNAL"           # SIGNAL | CLOCK
    clock: str | None = None      # chân clock của cổng này (cho chân không phải clock)


@dataclasses.dataclass
class Mem:
    name: str
    depth: int
    width: int
    ports: list[str]              # các loại cổng trong conf: rw, mrw, read, write, mwrite
    mask_gran: int | None = None

    @property
    def bits(self) -> int:
        return self.depth * self.width

    @property
    def addr_bits(self) -> int:
        return max(1, math.ceil(math.log2(self.depth)))

    @property
    def mask_bits(self) -> int:
        return self.width // self.mask_gran if self.mask_gran else 0

    def pins(self) -> list[Port]:
        """Chân theo quy ước tên của Chipyard (vlsi_mem_gen / firtool *_ext)."""
        out: list[Port] = []
        cnt = {"rw": 0, "r": 0, "w": 0}
        for kind in self.ports:
            masked = kind.startswith("m")
            base = kind[1:] if masked else kind
            if base == "rw":
                p = f"RW{cnt['rw']}"; cnt["rw"] += 1
                clk = f"{p}_clk"
                out += [Port(f"{p}_addr", "input", self.addr_bits, clock=clk),
                        Port(f"{p}_en", "input", 1, clock=clk),
                        Port(clk, "input", 1, use="CLOCK"),
                        Port(f"{p}_wmode", "input", 1, clock=clk),
                        Port(f"{p}_wdata", "input", self.width, clock=clk),
                        Port(f"{p}_rdata", "output", self.width, clock=clk)]
                if masked:
                    out.append(Port(f"{p}_wmask", "input", self.mask_bits, clock=clk))
            elif base == "read":
                p = f"R{cnt['r']}"; cnt["r"] += 1
                clk = f"{p}_clk"
                out += [Port(f"{p}_addr", "input", self.addr_bits, clock=clk),
                        Port(f"{p}_en", "input", 1, clock=clk),
                        Port(clk, "input", 1, use="CLOCK"),
                        Port(f"{p}_data", "output", self.width, clock=clk)]
            elif base == "write":
                p = f"W{cnt['w']}"; cnt["w"] += 1
                clk = f"{p}_clk"
                out += [Port(f"{p}_addr", "input", self.addr_bits, clock=clk),
                        Port(f"{p}_en", "input", 1, clock=clk),
                        Port(clk, "input", 1, use="CLOCK"),
                        Port(f"{p}_data", "input", self.width, clock=clk)]
                if masked:
                    out.append(Port(f"{p}_mask", "input", self.mask_bits, clock=clk))
            else:
                raise ValueError(f"{self.name}: loại cổng lạ {kind!r}")
        return out


_CONF_RE = re.compile(r"name\s+(\S+)\s+depth\s+(\d+)\s+width\s+(\d+)\s+ports\s+(\S+)(?:\s+mask_gran\s+(\d+))?")


def parse_conf(text: str) -> list[Mem]:
    mems = []
    for line in text.splitlines():
        m = _CONF_RE.search(line)
        if m:
            mems.append(Mem(m.group(1), int(m.group(2)), int(m.group(3)), m.group(4).split(","),
                            int(m.group(5)) if m.group(5) else None))
    return mems


# ---------------------------------------------------------------------------
# Kiểm chéo với Verilog (tuỳ chọn): chân sinh ra phải khớp header module trong RTL
# ---------------------------------------------------------------------------

_DECL_RE = re.compile(r"\b(input|output)\s+(?:wire\s+|reg\s+|logic\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)")


def verilog_ports(text: str, module: str) -> dict[str, tuple[str, int]] | None:
    m = re.search(rf"^\s*module\s+{re.escape(module)}\b(.*?)\bendmodule", text, re.S | re.M)
    if not m:
        return None
    body = re.sub(r"//[^\n]*", "", m.group(1))
    out: dict[str, tuple[str, int]] = {}
    # ANSI: "input [5:0] a, b," -> b mang hướng và độ rộng của a
    head_end = body.find(");")
    head = body[:head_end] if head_end >= 0 else body
    d, w = None, 1
    for tok in head.replace("(", ",").split(","):
        tok = " ".join(tok.split())
        mm = re.match(r"(?:(input|output)\s+)?(?:wire\s+|reg\s+|logic\s+)?(?:\[\s*(\d+)\s*:\s*(\d+)\s*\]\s*)?([A-Za-z_]\w*)$", tok)
        if not mm:
            continue
        if mm.group(1):
            d = mm.group(1)
            w = abs(int(mm.group(2)) - int(mm.group(3))) + 1 if mm.group(2) else 1
        elif mm.group(2):
            w = abs(int(mm.group(2)) - int(mm.group(3))) + 1
        if d:
            out[mm.group(4)] = (d, w)
    if not out:
        for d, msb, lsb, name in _DECL_RE.findall(body):
            out.setdefault(name, (d, abs(int(msb) - int(lsb)) + 1 if msb else 1))
    return out


def check_against_verilog(mems: list[Mem], verilog: str) -> list[str]:
    """Danh sách lệch (rỗng = khớp). Macro không có trong Verilog thì bỏ qua."""
    issues = []
    for mem in mems:
        vp = verilog_ports(verilog, mem.name)
        if vp is None:
            continue
        mine = {p.name: (p.direction, p.width) for p in mem.pins()}
        if mine != vp:
            only_v = sorted(set(vp) - set(mine))
            only_m = sorted(set(mine) - set(vp))
            diff_w = sorted(k for k in set(vp) & set(mine) if vp[k] != mine[k])
            issues.append(f"{mem.name}: chỉ trong Verilog {only_v}, chỉ trong fakeram {only_m}, khác hướng/độ rộng {diff_w}")
    return issues


# ---------------------------------------------------------------------------
# Hình học
# ---------------------------------------------------------------------------

def snap(x: float, grid: float = MFG) -> float:
    return round(round(x / grid) * grid, 3)


def bit_names(p: Port) -> list[str]:
    return [p.name] if p.width == 1 else [f"{p.name}[{i}]" for i in range(p.width)]


@dataclasses.dataclass
class Macro:
    mem: Mem
    width_um: float
    height_um: float
    pins: list[tuple[str, str, str, str, float]]   # (bit, direction, use, side L|R, y)
    power: list[tuple[str, float]]                 # (VPWR|VGND, x trái của sọc)

    @property
    def area_um2(self) -> float:
        return self.width_um * self.height_um


def layout(mem: Mem, prm: Params) -> Macro:
    bits = [(b, p.direction, p.use) for p in mem.pins() for b in bit_names(p)]
    pitch = MET3_PITCH * prm.pin_pitch_tracks
    area = mem.bits * prm.um2_per_bit
    h = max(prm.min_side_um, math.sqrt(area / prm.aspect))
    w = max(prm.min_side_um, area / h)
    margin = 2 * pitch
    need_one = len(bits) * pitch + 2 * margin
    sides = 1 if need_one <= h else 2
    need = math.ceil(len(bits) / sides) * pitch + 2 * margin
    if need > h:                                    # nới chiều cao, giữ diện tích nếu được
        h = need
        w = max(prm.min_side_um, area / h)
    h = math.ceil(h / SITE_H) * SITE_H              # bội số hàng std cell
    w = math.ceil(w / (2 * PWR_PITCH)) * (2 * PWR_PITCH) if w > 4 * PWR_PITCH else math.ceil(w / 0.46) * 0.46
    h, w = snap(h), snap(w)
    pins = []
    per_side = math.ceil(len(bits) / sides)
    for i, (b, d, use) in enumerate(bits):
        side = "L" if i < per_side else "R"
        k = i if side == "L" else i - per_side
        y = snap(margin + k * pitch + MET3_PITCH / 2 - MET3_W / 2)
        pins.append((b, d, use, side, y))
    power = []
    x, n = PWR_PITCH / 2, 0
    while x + PWR_W <= w - PWR_PITCH / 4:
        power.append(("VPWR" if n % 2 == 0 else "VGND", snap(x)))
        x += PWR_PITCH
        n += 1
    if not any(net == "VGND" for net, _ in power):          # macro hẹp: ít nhất một sọc mỗi loại
        power = [("VPWR", snap(w * 0.25 - PWR_W / 2)), ("VGND", snap(w * 0.75 - PWR_W / 2))]
    return Macro(mem, w, h, pins, power)


# ---------------------------------------------------------------------------
# LEF
# ---------------------------------------------------------------------------

def lef_text(macros: list[Macro]) -> str:
    o = ['VERSION 5.8 ;\nBUSBITCHARS "[]" ;\nDIVIDERCHAR "/" ;\n\n']
    for m in macros:
        o.append(f"MACRO {m.mem.name}\n  CLASS BLOCK ;\n  FOREIGN {m.mem.name} 0 0 ;\n  ORIGIN 0 0 ;\n"
                 f"  SIZE {m.width_um:.3f} BY {m.height_um:.3f} ;\n  SYMMETRY X Y R90 ;\n")
        for b, d, use, side, y in m.pins:
            x0 = 0.0 if side == "L" else snap(m.width_um - PIN_DEPTH)
            o.append(f"  PIN {b}\n    DIRECTION {d.upper()} ;\n    USE {use} ;\n    PORT\n      LAYER met3 ;\n"
                     f"        RECT {x0:.3f} {y:.3f} {x0 + PIN_DEPTH:.3f} {y + MET3_W:.3f} ;\n    END\n  END {b}\n")
        for net, use in (("VPWR", "POWER"), ("VGND", "GROUND")):
            o.append(f"  PIN {net}\n    DIRECTION INOUT ;\n    USE {use} ;\n")
            for n2, x in m.power:
                if n2 == net:
                    o.append(f"    PORT\n      LAYER met4 ;\n        RECT {x:.3f} 0.000 {x + PWR_W:.3f} {m.height_um:.3f} ;\n    END\n")
            o.append(f"  END {net}\n")
        o.append("  OBS\n")
        for layer in ("met1", "met2", "met4"):
            o.append(f"    LAYER {layer} ;\n      RECT 0.000 0.000 {m.width_um:.3f} {m.height_um:.3f} ;\n")
        # met3: chừa dải ngoài cùng (PIN_DEPTH) ở cạnh có chân để router đi vào chân
        o.append(f"    LAYER met3 ;\n      RECT {PIN_DEPTH + 0.2:.3f} 0.000 {m.width_um - PIN_DEPTH - 0.2:.3f} {m.height_um:.3f} ;\n")
        o.append(f"  END\nEND {m.mem.name}\n\n")
    o.append("END LIBRARY\n")
    return "".join(o)


# ---------------------------------------------------------------------------
# Liberty
# ---------------------------------------------------------------------------

LOADS = (0.0005, 0.005, 0.02, 0.05, 0.1, 0.2, 0.5)     # pF


def _row(vals) -> str:
    return ", ".join(f"{v:.4f}" for v in vals)


def liberty_text(macros: list[Macro], prm: Params) -> str:
    widths = sorted({p.width for m in macros for p in m.mem.pins() if p.width > 1})
    delay = _row(prm.access_ns + prm.out_res_ns_per_pf * c for c in LOADS)
    trans = _row(0.05 + 2 * prm.out_res_ns_per_pf * c for c in LOADS)

    def t_in(clk):
        out = ""
        for typ, v in (("setup_rising", prm.setup_ns), ("hold_rising", prm.hold_ns)):
            out += (f'      timing () {{\n        related_pin : "{clk}";\n        timing_type : {typ};\n'
                    f'        rise_constraint (scalar) {{ values ("{v}"); }}\n'
                    f'        fall_constraint (scalar) {{ values ("{v}"); }}\n      }}\n')
        return out

    def t_out(clk):
        return (f'      timing () {{\n        related_pin : "{clk}";\n        timing_type : rising_edge;\n'
                f'        timing_sense : non_unate;\n'
                f'        cell_rise (load_tpl) {{ values ("{delay}"); }}\n'
                f'        cell_fall (load_tpl) {{ values ("{delay}"); }}\n'
                f'        rise_transition (load_tpl) {{ values ("{trans}"); }}\n'
                f'        fall_transition (load_tpl) {{ values ("{trans}"); }}\n      }}\n')

    cells = []
    for m in macros:
        body = [f"  cell ({m.mem.name}) {{\n    area : {m.area_um2:.3f};\n    is_macro_cell : true;\n"
                "    dont_touch : true;\n    dont_use : true;\n    interface_timing : true;\n"
                '    pg_pin (VPWR) { voltage_name : VPWR; pg_type : primary_power; }\n'
                '    pg_pin (VGND) { voltage_name : VGND; pg_type : primary_ground; }\n']
        for p in m.mem.pins():
            if p.use == "CLOCK":
                body.append(f"    pin ({p.name}) {{\n      direction : input;\n      capacitance : {prm.clk_cap_pf};\n"
                            f"      clock : true;\n      related_power_pin : VPWR;\n      related_ground_pin : VGND;\n    }}\n")
                continue
            if p.direction == "input":
                attrs = f"      direction : input;\n      capacitance : {prm.in_cap_pf};\n"
                t = t_in(p.clock)
            else:
                attrs = f"      direction : output;\n      max_capacitance : {LOADS[-1]};\n"
                t = t_out(p.clock)
            attrs += "      related_power_pin : VPWR;\n      related_ground_pin : VGND;\n"
            if p.width == 1:
                body.append(f"    pin ({p.name}) {{\n{attrs}{t}    }}\n")
            else:
                body.append(f"    bus ({p.name}) {{\n      bus_type : bus_{p.width};\n{attrs}{t}    }}\n")
        body.append("  }\n")
        cells.append("".join(body))
    types = "".join(f"  type (bus_{w}) {{\n    base_type : array;\n    data_type : bit;\n    bit_width : {w};\n"
                    f"    bit_from : {w - 1};\n    bit_to : 0;\n    downto : true;\n  }}\n" for w in widths)
    head = (f"/* fakeram sky130hd {VERSION}: access {prm.access_ns} ns, setup {prm.setup_ns} ns, hold {prm.hold_ns} ns. "
            "Timing gia dinh, khong phai OpenRAM. */\n"
            "library (fakeram_sky130_tt_025C_1v80) {\n  technology (cmos);\n  delay_model : table_lookup;\n"
            '  time_unit : "1ns";\n  voltage_unit : "1V";\n  current_unit : "1mA";\n'
            '  pulling_resistance_unit : "1kohm";\n  leakage_power_unit : "1nW";\n  capacitive_load_unit (1, pf);\n'
            "  input_threshold_pct_rise : 50;\n  input_threshold_pct_fall : 50;\n"
            "  output_threshold_pct_rise : 50;\n  output_threshold_pct_fall : 50;\n"
            "  slew_lower_threshold_pct_rise : 20;\n  slew_lower_threshold_pct_fall : 20;\n"
            "  slew_upper_threshold_pct_rise : 80;\n  slew_upper_threshold_pct_fall : 80;\n"
            "  nom_process : 1;\n  nom_voltage : 1.8;\n  nom_temperature : 25;\n"
            "  operating_conditions (tt_025C_1v80) { process : 1; voltage : 1.8; temperature : 25; }\n"
            "  default_operating_conditions : tt_025C_1v80;\n"
            "  default_max_transition : 1.5;\n  default_fanout_load : 1;\n"
            "  voltage_map (VPWR, 1.8);\n  voltage_map (VGND, 0);\n"
            "  lu_table_template (load_tpl) {\n    variable_1 : total_output_net_capacitance;\n"
            f'    index_1 ("{_row(LOADS)}");\n  }}\n')
    return head + types + "".join(cells) + "}\n"


# ---------------------------------------------------------------------------
# GDS (GDSII nhị phân, chỉ đường bao) và Verilog blackbox
# ---------------------------------------------------------------------------

def _rec(rtype: int, dtype: int, data: bytes = b"") -> bytes:
    if len(data) % 2:
        data += b"\0"
    return struct.pack(">HBB", 4 + len(data), rtype, dtype) + data


def _real8(x: float) -> bytes:
    """Số thực 8 byte kiểu excess-64 cơ số 16 của GDSII."""
    if x == 0:
        return b"\0" * 8
    sign = 0x80 if x < 0 else 0
    x = abs(x)
    exp = 0
    while x >= 1:
        x /= 16.0
        exp += 1
    while x < 1 / 16:
        x *= 16.0
        exp -= 1
    mant = int(round(x * (1 << 56)))
    if mant >= 1 << 56:
        mant >>= 4
        exp += 1
    return struct.pack(">B", sign | (exp + 64)) + mant.to_bytes(7, "big")


def gds_bytes(macros: list[Macro], libname: str = "fakeram_sky130") -> bytes:
    ts = struct.pack(">12h", 2026, 1, 1, 0, 0, 0, 2026, 1, 1, 0, 0, 0)
    o = _rec(0x00, 0x02, struct.pack(">h", 600))           # HEADER
    o += _rec(0x01, 0x02, ts)                              # BGNLIB
    o += _rec(0x02, 0x06, libname.encode())                # LIBNAME
    o += _rec(0x03, 0x05, _real8(1e-3) + _real8(1e-9))     # UNITS: 1 dbu = 1 nm
    for m in macros:
        w, h = int(round(m.width_um * 1000)), int(round(m.height_um * 1000))
        o += _rec(0x05, 0x02, ts)                          # BGNSTR
        o += _rec(0x06, 0x06, m.mem.name.encode())         # STRNAME
        o += _rec(0x08, 0x00)                              # BOUNDARY
        o += _rec(0x0D, 0x02, struct.pack(">h", PRBNDRY[0]))   # LAYER
        o += _rec(0x0E, 0x02, struct.pack(">h", PRBNDRY[1]))   # DATATYPE
        o += _rec(0x10, 0x03, struct.pack(">10i", 0, 0, w, 0, w, h, 0, h, 0, 0))  # XY
        o += _rec(0x11, 0x00)                              # ENDEL
        o += _rec(0x07, 0x00)                              # ENDSTR
    o += _rec(0x04, 0x00)                                  # ENDLIB
    return o


def blackbox_text(macros: list[Macro]) -> str:
    o = [f"// fakeram sky130hd {VERSION}: blackbox, sinh boi scripts/fakeram_sky130.py\n"]
    for m in macros:
        ports = m.mem.pins()
        decl = ",\n".join(f"  {p.direction} {'' if p.width == 1 else f'[{p.width - 1}:0] '}{p.name}" for p in ports)
        o.append(f"(* blackbox *)\nmodule {m.mem.name} (\n{decl}\n);\nendmodule\n\n")
    return "".join(o)


# ---------------------------------------------------------------------------

def generate(mems: list[Mem], out_dir: str | Path, prm: Params) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    macros = [layout(m, prm) for m in sorted(mems, key=lambda m: m.name)]
    (out / "fakeram_sky130.lef").write_text(lef_text(macros))
    (out / "fakeram_sky130.lib").write_text(liberty_text(macros, prm))
    (out / "fakeram_sky130.gds").write_bytes(gds_bytes(macros))
    (out / "fakeram_sky130_bb.v").write_text(blackbox_text(macros))
    info = {"version": VERSION, "params": dataclasses.asdict(prm),
            "macros": [{"name": m.mem.name, "depth": m.mem.depth, "width": m.mem.width, "ports": m.mem.ports,
                        "mask_gran": m.mem.mask_gran, "bits": m.mem.bits, "n_pins": len(m.pins),
                        "w_um": m.width_um, "h_um": m.height_um, "area_um2": round(m.area_um2, 1)} for m in macros],
            "total_area_um2": round(sum(m.area_um2 for m in macros), 1),
            "total_bits": sum(m.mem.bits for m in macros)}
    (out / "fakeram.json").write_text(json.dumps(info, indent=2))
    return info


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--conf", required=True, help="file *.mems.conf của build")
    ap.add_argument("--out", required=True, help="thư mục ghi kết quả")
    ap.add_argument("--only", default="", help="chỉ các macro này (phân cách bởi dấu phẩy)")
    ap.add_argument("--verilog", action="append", default=[], help="file Verilog để kiểm chéo tên/độ rộng chân")
    for f in dataclasses.fields(Params):
        ap.add_argument("--" + f.name.replace("_", "-"), type=type(f.default), default=f.default)
    a = ap.parse_args()
    mems = parse_conf(Path(a.conf).read_text())
    if a.only:
        want = set(a.only.split(","))
        missing = want - {m.name for m in mems}
        if missing:
            sys.exit(f"LỖI: không có trong {a.conf}: {sorted(missing)}")
        mems = [m for m in mems if m.name in want]
    if not mems:
        sys.exit(f"LỖI: không đọc được macro nào từ {a.conf}")
    if a.verilog:
        issues = check_against_verilog(mems, "\n".join(Path(v).read_text() for v in a.verilog))
        if issues:
            sys.exit("LỖI chân không khớp Verilog:\n  " + "\n  ".join(issues))
    prm = Params(**{f.name: getattr(a, f.name) for f in dataclasses.fields(Params)})
    info = generate(mems, a.out, prm)
    for m in info["macros"]:
        print(f"{m['name']:40s} {m['depth']:>6}x{m['width']:<4} {','.join(m['ports']):10s} "
              f"{m['w_um']:8.1f} x {m['h_um']:8.1f} um  {m['n_pins']:4d} chân")
    print(f"{len(info['macros'])} macro, {info['total_bits']} bit, {info['total_area_um2'] / 1e6:.3f} mm2 -> {a.out}")


if __name__ == "__main__":
    main()
