"""Luật nhận/loại một vòng và ngưỡng nhiễu (hàm thuần, không gọi Ray/CHIA: kiểm được trong sandbox).

Một vòng được nhận khi:
  1. gate pass: mọi test ISA pass (trừ test hỏng sẵn ở baseline), 8 benchmark pass, minstret bằng baseline;
  2. func_check đạt;
  3. chi phí cycle (trung bình nhân mcycle / mcycle baseline) <= Budget.cycle (1,03);
  4. diện tích logic sau place <= Budget.area (1,15) × baseline;
  5. so với bản tốt nhất hiện tại (không phải baseline, cùng chu kỳ ràng buộc): chu kỳ đạt được sau place
     (1000 / Fmax = chu kỳ − WNS, ns) không xấu đi quá ngưỡng nhiễu, và tốt lên hơn ngưỡng nhiễu, hoặc điểm họ (tổng
     slack các họ endpoint xấu nhất, âm) tăng hơn ngưỡng nhiễu.
Ngưỡng nhiễu đo bằng các lần place nhiễu nhẹ của một điểm (soc_fmax.py --noise cho baseline; loop.py đo lại trên bản
tốt nhất mỗi khi xuống bậc chu kỳ): ngưỡng = max(K × (lớn nhất − nhỏ nhất), mức tối thiểu), K = 1,5; tối thiểu
0,03 ns chu kỳ, 0,3 MHz và 0,2 ns điểm họ. Ngưỡng tính theo ns (từ 16/11/2026), không theo MHz: cùng một dao động
slack ứng với số MHz lớn hơn khi Fmax cao (0,367 ns là ±1,55 MHz ở 65 MHz nhưng ±3,7 MHz ở 100 MHz).
Trong đợt (loop.py --phase asic), vòng được cộng dồn theo phần ORFS của luật (decide_place, so với đầu chuỗi) + gate
nhanh ISA; cuối đợt các vòng cộng dồn qua gate đủ theo Fmax place giảm dần. Không dùng synth hay floorplan để chọn:
đường găng synth khác place, và floorplan bão hoà khi slack ≥ 0 (sửa timing dừng ở chu kỳ mục tiêu).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

NOISE_K = 1.5
MIN_FMAX_MHZ = 0.3
MIN_FAMILY_NS = 0.2
MIN_PERIOD_NS = 0.03


def period_of(fmax: float | None) -> float | None:
    """Chu kỳ đạt được (ns) từ Fmax (MHz)."""
    return 1000.0 / fmax if fmax else None


@dataclass
class Budget:
    cycle: float = 1.03
    area: float = 1.15


@dataclass
class Noise:
    fmax_mhz: float                    # chỉ để hiển thị (ngưỡng nhận là period_ns)
    family_ns: float
    samples: list = field(default_factory=list)   # [{name, fmax, family}]
    note: str = ""
    period_ns: float | None = None     # ngưỡng theo chu kỳ đạt được (ns); noise.json cũ không có: tính từ samples
    at_period_ns: float | None = None  # chu kỳ ràng buộc lúc đo (noise.json cũ: 10 ns)
    point: str = "baseline"            # điểm được đo nhiễu

    def __post_init__(self):
        if self.period_ns is None:
            ps = [period_of(s.get("fmax")) for s in self.samples if s.get("fmax")]
            self.period_ns = max(NOISE_K * (max(ps) - min(ps)), MIN_PERIOD_NS) if len(ps) >= 2 else MIN_PERIOD_NS
        if self.at_period_ns is None:
            self.at_period_ns = 10.0


@dataclass
class Point:
    """Một thiết kế đã đo (baseline, bản tốt nhất, hoặc ứng viên của một vòng)."""
    name: str
    rtl_hash: str = ""
    gate_ok: bool = False
    gate_reasons: list = field(default_factory=list)
    func_ok: bool = False
    func_reasons: list = field(default_factory=list)
    cycle_cost: float | None = None
    synth_fmax: float | None = None
    synth_family: float | None = None
    fmax: float | None = None          # sau place
    family: float | None = None        # sau place
    area: float | None = None          # diện tích logic sau place (um2)
    fp_fmax: float | None = None       # sau floorplan (chỉ để tham khảo)
    fp_family: float | None = None
    fp_area: float | None = None
    period_ns: float | None = None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Decision:
    accept: bool
    stage: str                         # gate | func | synth | place
    reasons: list = field(default_factory=list)
    d_fmax: float | None = None
    d_family: float | None = None


def noise_from(samples: list[dict], k: float = NOISE_K, min_fmax: float = MIN_FMAX_MHZ,
               min_family: float = MIN_FAMILY_NS, min_period: float = MIN_PERIOD_NS,
               at_period_ns: float = 10.0, point: str = "baseline") -> Noise:
    """samples: [{"name", "fmax", "family"}] gồm cả điểm gốc. Ngưỡng = max(k × khoảng, mức tối thiểu)."""
    f = [s["fmax"] for s in samples if s.get("fmax") is not None]
    g = [s["family"] for s in samples if s.get("family") is not None]
    if len(f) < 2:
        raise ValueError(f"cần ít nhất 2 lần đo để tính nhiễu, có {len(f)}")
    sf = max(f) - min(f)
    sp = 1000.0 / min(f) - 1000.0 / max(f)
    sg = (max(g) - min(g)) if len(g) >= 2 else 0.0
    note = (f"{point} @ {at_period_ns:g} ns: Fmax {min(f):.3f}..{max(f):.3f} MHz (khoảng {sf:.3f}, chu kỳ đạt được "
            f"khoảng {sp:.3f} ns), điểm họ {min(g, default=0):.3f}..{max(g, default=0):.3f} (khoảng {sg:.3f}), "
            f"{len(f)} lần đo")
    return Noise(fmax_mhz=max(k * sf, min_fmax), family_ns=max(k * sg, min_family), samples=samples, note=note,
                 period_ns=max(k * sp, min_period), at_period_ns=at_period_ns, point=point)


def gate_check(base_gate, gate, known_fail: list) -> tuple[bool, list]:
    """base_gate/gate: soc_nodes.GateResult (hoặc object có .isa, .bench)."""
    reasons = []
    bad_isa = sorted(n for n, ok in gate.isa.items() if not ok and n not in known_fail)
    missing = sorted(set(base_gate.isa) - set(gate.isa))
    if bad_isa:
        reasons.append(f"ISA hỏng: {', '.join(bad_isa[:10])}" + (f" (+{len(bad_isa) - 10})" if len(bad_isa) > 10 else ""))
    if missing:
        reasons.append(f"ISA thiếu: {', '.join(missing[:10])}")
    for n, b in base_gate.bench.items():
        c = gate.bench.get(n)
        if not c or not c.get("passed"):
            reasons.append(f"benchmark {n} hỏng")
        elif c.get("minstret") != b.get("minstret"):
            reasons.append(f"benchmark {n}: minstret {c.get('minstret')} ≠ baseline {b.get('minstret')}")
    return not reasons, reasons


def decide(base: Point, best: Point, cand: Point, noise: Noise, budget: Budget) -> Decision:
    if not cand.gate_ok:
        return Decision(False, "gate", list(cand.gate_reasons) or ["gate hỏng"])
    if not cand.func_ok:
        return Decision(False, "func", list(cand.func_reasons) or ["func_check hỏng"])
    if cand.cycle_cost is None or cand.cycle_cost > budget.cycle:
        return Decision(False, "gate", [f"chi phí cycle {cand.cycle_cost} > {budget.cycle}"])
    return decide_place(base, best, cand, noise, budget)


def decide_place(base: Point, best: Point, cand: Point, noise: Noise, budget: Budget) -> Decision:
    """Chỉ phần ORFS của decide (diện tích, Fmax, điểm họ); loop.py dùng để xếp hạng trước khi gate."""
    return decide_metric("place", base.area, (best.fmax, best.family), (cand.fmax, cand.family, cand.area),
                         noise, budget)


def decide_metric(stage: str, base_area, best: tuple, cand: tuple, noise: Noise, budget: Budget) -> Decision:
    (bf, bg), (cf, cg, ca) = best, cand
    if cf is None:
        return Decision(False, stage, [f"không có Fmax sau {stage}"])
    reasons = []
    if base_area and ca and ca > base_area * budget.area:
        return Decision(False, stage, [f"diện tích logic ({stage}) {ca:,.0f} > {budget.area} × baseline {base_area:,.0f} um2"])
    d_f = cf - bf
    d_p = 1000.0 / bf - 1000.0 / cf          # > 0: chu kỳ đạt được ngắn hơn (tốt hơn)
    d_g = (cg - bg) if cg is not None and bg is not None else None
    reasons.append(f"{stage}: Fmax {cf:.3f} vs {bf:.3f} MHz (Δ {d_f:+.3f}); chu kỳ đạt được {1000 / cf:.3f} vs "
                   f"{1000 / bf:.3f} ns (tốt lên {d_p:+.3f} ns, nhiễu ±{noise.period_ns:.3f})")
    if d_g is not None:
        reasons.append(f"{stage}: điểm họ {cg:.3f} vs {bg:.3f} (Δ {d_g:+.3f}, nhiễu ±{noise.family_ns:.3f})")
    if d_p < -noise.period_ns:
        return Decision(False, stage, ["Fmax giảm quá nhiễu"] + reasons, d_f, d_g)
    if d_p > noise.period_ns:
        return Decision(True, stage, ["Fmax tăng"] + reasons, d_f, d_g)
    if d_g is not None and d_g > noise.family_ns:
        return Decision(True, stage, ["điểm họ tăng (Fmax trong nhiễu)"] + reasons, d_f, d_g)
    return Decision(False, stage, ["trong nhiễu"] + reasons, d_f, d_g)
