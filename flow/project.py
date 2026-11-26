"""project.py - đọc file khai báo project (`<project>/project.py`) và chuẩn bị thư mục của một lần chạy.

File khai báo là Python (chia_env dùng Python 3.10, chưa có tomllib), định nghĩa biến `PROJECT` (dict) và có thể có hàm
móc `fpga_synth_args(fpga_dir, opts) -> (list[str], dict)`. Xem flow/README.md cho mọi trường; DEFAULTS ở dưới là giá
trị khi project không ghi.
"""
from __future__ import annotations

import copy
import importlib.util
import re
import shutil
from pathlib import Path

KIT = Path(__file__).resolve().parent / "kit"
STEPS = ("sim", "fpga", "asic")

DEFAULTS = {
    "group": None,                      # thư mục nhóm trong WORK_DIR (mặc định: tên thư mục cha của project)
    "name": None,                       # tên project (mặc định: tên thư mục project)
    "dut": None,                        # BẮT BUỘC: module top của RTL
    "title": "",                        # một dòng mô tả, hiện trên trang status
    "steps": list(STEPS),               # bước bật: sim bắt buộc; fpga, asic tuỳ chọn
    "src_dirs": ["rtl", "sim", "fpga", "asic"],
    "sim": {
        "kind": "verilator",            # "verilator": testbench Verilog tự kiểm (sim/run_sim.sh); "chipyard": chưa làm
        "plusargs": {},                 # tên -> giá trị mặc định, truyền vào testbench dạng +tên=giá trị
        "fcov_module": None,            # module functional coverage (sim/fcov_<dut>.sv), testbench không được tham chiếu
        "lint": True,                   # gate lint: verilator --lint-only -Wall trên rtl/ phải sạch
    },
    "fpga": {
        "tool": "Vivado",
        "part": None,                   # BẮT BUỘC khi bật fpga, ví dụ xc7a100tcsg324-1
        "board": "",                    # chỉ để hiển thị
        "top": "fpga_top",
        "period_ns": 10.0,              # mặc định của --fpga-period
        "variants": {"rtl": {"replace": {}, "synth": [], "note": "RTL as is"}},
        "power_instance": None,         # instance dưới top để đọc công suất riêng (report_power -hierarchical)
        "opts": {},                     # tham số riêng của project, chuyển cho hàm móc fpga_synth_args
        "sweep_opt": None,              # fpga_sweep power: tên opt được quét
        "fixed": [],                    # file trong fpga/ agent không được sửa (ngoài file của kit)
        "saif_min_match_pct": 50,
    },
    "asic": {
        "platform": "sky130hd",
        "cell_prefix": "sky130_fd_sc_hd__",
        "tt_lib": "sky130_fd_sc_hd__tt_025C_1v80.lib",
        "corner_glob": "sky130_fd_sc_hd__[sf][sf]_*.lib",   # .lib corner thật (nếu có trong platforms/<p>/lib)
        "ocv_derate": 0.10,             # corner tt + OCV ±x khi không có .lib corner thật
        "icg_cell": "dlclkp",           # đếm ICG trong netlist
        "fixed_vars": ["PLATFORM", "DESIGN_NAME", "DESIGN_NICKNAME", "VERILOG_FILES", "SDC_FILE"],
        "check_slots": 0.25,            # resource asic mỗi task kiểm tra sau finish (DRC, LVS, STA chạy song song)
    },
    "agent": {
        "description": "",              # mô tả thiết kế cho prompt
        "variant": "",                  # thêm cho prompt (ví dụ có clock gating)
        "sim_hints": "",                # gợi ý hành vi cần kiểm ở bước sim
        "fpga_wrapper": "",             # mô tả vỏ FPGA (fpga_top) cho prompt; có thể dùng {tên} của info fpga
    },
}

# File của kit: chép vào thư mục chạy nếu project không có bản riêng (cùng đường dẫn tương đối).
KIT_FILES = {
    "sim/sim_main.cpp": "sim/sim_main.cpp",
    "fpga/power_saif.tcl": "fpga/power_saif.tcl",
    "asic/signoff.tcl": "asic/signoff.tcl",
    "asic/render_gds.py": "asic/render_gds.py",
    "asic/gls/cells_extra.v": "platforms/{platform}/cells_extra.v",
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) and k != "variants" else v
    return out


class Project:
    """Khai báo project đã gộp mặc định. `cfg` (dict, pickle được) là phần task trên worker cần."""

    def __init__(self, path: str | Path):
        self.dir = Path(path).resolve()
        f = self.dir / "project.py"
        if not f.is_file():
            raise SystemExit(f"LỖI: không có {f}")
        spec = importlib.util.spec_from_file_location(f"flow_project_{self.dir.name}", f)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if not isinstance(getattr(mod, "PROJECT", None), dict):
            raise SystemExit(f"LỖI: {f} không định nghĩa PROJECT = {{...}}")
        self.mod = mod
        c = _merge(DEFAULTS, mod.PROJECT)
        c["name"] = c["name"] or self.dir.name
        c["group"] = c["group"] or self.dir.parent.name
        self.cfg = c
        self._check()

    def _check(self) -> None:
        c, err = self.cfg, []
        if not c["dut"]:
            err.append("thiếu dut")
        bad = [s for s in c["steps"] if s not in STEPS]
        if bad or "sim" not in c["steps"]:
            err.append(f"steps phải gồm sim và thuộc {STEPS} (đang là {c['steps']})")
        if c["sim"]["kind"] != "verilator":
            err.append(f"sim.kind={c['sim']['kind']!r} chưa hỗ trợ (mới có 'verilator'; 'chipyard' là việc sau)")
        if "fpga" in c["steps"] and not c["fpga"]["part"]:
            err.append("bật fpga nhưng thiếu fpga.part")
        for d in c["src_dirs"]:
            if not (self.dir / d).is_dir() and (d == "rtl" or d in c["steps"]):
                err.append(f"thiếu thư mục {d}/")
        if err:
            raise SystemExit(f"LỖI khai báo project {self.dir}: " + "; ".join(err))

    # --- thuộc tính hay dùng ---
    name = property(lambda s: s.cfg["name"])
    group = property(lambda s: s.cfg["group"])
    dut = property(lambda s: s.cfg["dut"])
    steps = property(lambda s: list(s.cfg["steps"]))

    def run_root(self, work_dir: str | Path) -> Path:
        return Path(work_dir) / self.group / self.name

    def plusargs(self, overrides: dict | None = None) -> list[str]:
        vals = {**self.cfg["sim"]["plusargs"], **(overrides or {})}
        return [f"+{k}={v}" for k, v in vals.items()]

    # --- thư mục của lần chạy ---
    def copy_sources(self, dst: Path, src: Path | None = None, ignore=("build",)) -> None:
        """Chép cây nguồn (src_dirs) từ project (hoặc từ src, ví dụ bản tốt nhất của agent) sang dst, rồi thêm file của
        kit mà cây chưa có."""
        src = src or self.dir
        for sub in self.cfg["src_dirs"]:
            if (src / sub).is_dir():
                shutil.copytree(src / sub, dst / sub, ignore=shutil.ignore_patterns(*ignore))
        for rel, kit_rel in KIT_FILES.items():
            top = rel.split("/")[0]
            if top not in self.cfg["src_dirs"] or (top != "sim" and top not in self.cfg["steps"]):
                continue
            target = dst / rel
            k = KIT / kit_rel.format(platform=self.cfg["asic"]["platform"])
            if not target.exists() and k.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(k, target)

    def prepare_fpga(self, run_dir: str | Path, period_ns: float, variant: str, opts: dict | None = None) -> dict:
        """Ghi các file cố định của lần chạy vào <run_dir>/fpga: clock.xdc (chu kỳ), sources.f (biến thể),
        synth_opts.tcl (tuỳ chọn synth_design của biến thể + của hàm móc fpga_synth_args)."""
        f = self.cfg["fpga"]
        if variant not in f["variants"]:
            raise SystemExit(f"LỖI: biến thể FPGA {variant!r} không có; chọn trong {', '.join(f['variants'])}")
        v = f["variants"][variant]
        fpga = Path(run_dir, "fpga")
        (fpga / "clock.xdc").write_text(
            "## clock.xdc - chu kỳ clock của bước FPGA (flow ghi theo --fpga-period; agent không sửa được).\n"
            f"create_clock -name sys_clk -period {period_ns:.3f} [get_ports clk]\n")
        srcs = (fpga / "sources.f").read_text().splitlines()
        (fpga / "sources.f").write_text("\n".join(v.get("replace", {}).get(ln.strip(), ln) for ln in srcs) + "\n")
        o = {**f["opts"], **(opts or {})}
        args, info = list(v.get("synth", [])), {}
        hook = getattr(self.mod, "fpga_synth_args", None)
        if hook:
            extra, info = hook(fpga, o)
            args += list(extra)
        (fpga / "synth_opts.tcl").write_text(
            f"# synth_opts.tcl - biến thể {variant} ({v.get('note', '')}), opts {o}. Flow ghi, cố định.\n"
            "set synth_args {" + " ".join(args) + "}\n")
        return {"period_ns": period_ns, "variant": variant, "opts": o, "synth_args": args, **info}

    def variant_of(self, fpga_cfg: dict | None) -> dict:
        return self.cfg["fpga"]["variants"].get((fpga_cfg or {}).get("variant"), {})


def parse_kv(items: list[str] | None) -> dict:
    """['a=1', 'b=x'] -> {'a': 1, 'b': 'x'} (số được đổi sang int/float)."""
    out = {}
    for it in items or []:
        if "=" not in it:
            raise SystemExit(f"LỖI: {it!r} phải có dạng tên=giá_trị")
        k, v = it.split("=", 1)
        if re.fullmatch(r"-?\d+", v):
            v = int(v)
        elif re.fullmatch(r"-?\d*\.\d+", v):
            v = float(v)
        out[k.strip()] = v
    return out
