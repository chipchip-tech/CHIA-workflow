const pptxgen = require("pptxgenjs");
const pres = new pptxgen();
pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5, như mẫu
pres.title = "Demo: Sim -> FPGA -> ASIC with an Agentic CHIA Loop";
const F = "Times New Roman";
const K = { black: "000000", ink: "262626", mute: "5A5A5A", cap: "9A9A9A", line: "BFBFBF", head: "D9D9D9", soft: "F2F2F2", grid: "A6A6A6" };
pres.theme = { headFontFace: F, bodyFontFace: F };

function title(s, t, n) {
  s.addText(t, { x: 0.85, y: 0.45, w: 11.65, h: 0.7, fontFace: F, fontSize: 30, bold: true, color: K.black, margin: 0, isTextBox: true });
  s.addText(String(n), { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontFace: F, fontSize: 10, color: K.cap, align: "right", margin: 0, isTextBox: true });
}
function head(s, t, x, y, w) {
  s.addText(t, { x, y, w, h: 0.4, fontFace: F, fontSize: 17, bold: true, color: K.black, margin: 0, isTextBox: true });
}
// items: [label, text] hoặc text
function bullets(s, items, o) {
  const runs = [];
  items.forEach((it, i) => {
    const last = i === items.length - 1;
    const po = { bullet: { indent: 18 }, paraSpaceAfter: 6 };
    if (Array.isArray(it)) {
      runs.push({ text: it[0] + " ", options: { ...po, bold: true, color: K.black } });
      runs.push({ text: it[1], options: { color: K.ink, breakLine: !last } });
    } else runs.push({ text: it, options: { ...po, color: K.ink, breakLine: !last } });
  });
  s.addText(runs, { fontFace: F, fontSize: o.fs || 15, valign: "top", margin: 0, isTextBox: true, ...o, fs: undefined });
}
function caption(s, t, x, y, w, h) {
  s.addText(t, { x, y, w, h: h || 0.3, fontFace: F, fontSize: 12, italic: true, color: K.mute, margin: 0, isTextBox: true });
}
function box(s, a, b, x, y, w, h, dark) {
  s.addText([{ text: a, options: { bold: true, breakLine: true, fontSize: 12 } }, { text: b, options: { fontSize: 11 } }],
    { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, x, y, w, h, fontFace: F, align: "center", valign: "middle",
      color: dark ? "FFFFFF" : K.black, fill: { color: dark ? K.ink : "FFFFFF" }, line: { color: K.ink, width: 1 }, margin: 2 });
}
function arrow(s, x1, y, x2) {
  s.addShape(pres.shapes.LINE, { x: x1, y, w: x2 - x1, h: 0, line: { color: K.ink, width: 1.25, endArrowType: "triangle" } });
}
function table(s, rows, o) {
  const data = rows.map((r, i) => r.map((c, j) => ({ text: String(c), options: {
    bold: i === 0 || (o.boldFirstCol && j === 0), fill: { color: i === 0 ? K.head : "FFFFFF" } } })));
  s.addTable(data, { fontFace: F, fontSize: o.fs || 12.5, color: K.black, border: { type: "solid", pt: 0.75, color: K.grid },
    margin: [3, 6, 3, 6], valign: "middle", autoPage: false, ...o, boldFirstCol: undefined, fs: undefined });
}
function placeholder(s, t, x, y, w, h) {
  s.addText(t, { shape: pres.shapes.RECTANGLE, x, y, w, h, fontFace: F, fontSize: 12, italic: true, color: K.cap, align: "center",
    valign: "middle", fill: { color: K.soft }, line: { color: K.line, width: 1, dashType: "dash" }, margin: 6 });
}
function stat(s, big, small, x, y, w) {
  s.addText(big, { x, y, w, h: 0.6, fontFace: F, fontSize: 32, bold: true, color: K.black, margin: 0, isTextBox: true });
  s.addText(small, { x, y: y + 0.6, w, h: 0.45, fontFace: F, fontSize: 13, color: K.mute, margin: 0, valign: "top", isTextBox: true });
}

// 1. Bìa
{
  const s = pres.addSlide();
  s.addText("PROGRESS REPORT · 22/11/2026", { x: 0.4, y: 1.45, w: 8, h: 0.35, fontFace: F, fontSize: 13, color: K.mute, charSpacing: 3, margin: 0, isTextBox: true });
  s.addText([{ text: "Demo: Sim → FPGA → ASIC Flow", options: { breakLine: true } }, { text: "with an Agentic CHIA Loop" }],
    { x: 0.4, y: 2.0, w: 11, h: 1.5, fontFace: F, fontSize: 34, bold: true, color: K.black, valign: "middle", margin: 0, isTextBox: true });
  s.addText("8-bit ALU, with and without clock gating · Verilator, Vivado, Yosys + OpenROAD (sky130hd)",
    { x: 0.4, y: 3.55, w: 11, h: 0.4, fontFace: F, fontSize: 16, color: K.mute, margin: 0, isTextBox: true });
  s.addShape(pres.shapes.LINE, { x: 0.5, y: 4.3, w: 4.2, h: 0, line: { color: K.line, width: 1 } });
  s.addText([{ text: "Advisor: MSc. Phạm Thế Hùng", options: { breakLine: true } }, { text: "Student: Phan Thành Nhân" }],
    { x: 0.5, y: 4.5, w: 6, h: 0.8, fontFace: F, fontSize: 16, color: K.black, margin: 0, isTextBox: true });
}

// 2. Mục tiêu và thiết lập
{
  const s = pres.addSlide(); title(s, "1. Goal and Setup", 2);
  head(s, "Goal", 0.85, 1.3, 5.2);
  bullets(s, [
    "Can CHIA run a complete open-source flow (simulation, then FPGA and ASIC) while a Claude agent reads the results and optimizes each step?",
    "The design is an 8-bit ALU, small on purpose so each step takes minutes and the ASIC branch reaches GDS.",
    "Two variants run the same flow. NoCG holds register values with a plain enable (feedback mux). CG gates the clock of the input and output registers with an ICG cell.",
    "Three phases: baseline without agent, agent with at most 2 rounds per step, then a comparison.",
  ], { x: 0.85, y: 1.75, w: 5.2, h: 3.5, fs: 14 });
  s.addText([{ text: "Baseline (phase 1)", options: { bold: true, fontSize: 13, breakLine: true } },
             { text: "≈ 4 min per demo", options: { bold: true, fontSize: 28, breakLine: true } },
             { text: "FPGA ≈ 64 s, ASIC ≈ 2.5 min. Near-default settings (ORFS CORE_UTILIZATION 40, PLACE_DENSITY 0.65; Vivado default directives). Two runs give identical numbers.", options: { fontSize: 12 } }],
    { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x: 0.85, y: 4.85, w: 5.2, h: 1.6, fontFace: F, color: K.black,
      fill: { color: K.soft }, line: { color: K.soft }, margin: 10, valign: "middle" });
  table(s, [
    ["Item", "Choice"],
    ["Design", "8-bit ALU, 8 ops (ADD, SUB, AND, OR, XOR, SHL, SHR, SLT), 2-stage pipeline, zero / carry / valid flags"],
    ["CG variant", "icg module: latch + AND in simulation, sky130_fd_sc_hd__dlclkp in ASIC"],
    ["Sim", "Verilator 5.022, self-checking testbench against a reference model"],
    ["FPGA", "Vivado 2025.1, xc7a100tcsg324-1 (Arty A7-100T), 100 MHz; fpga_top wrapper drives inputs with an LFSR and checks outputs with a MISR"],
    ["ASIC", "Yosys 0.68 + OpenROAD (ORFS), sky130hd, 10 ns clock, RTL to GDS"],
    ["Agent", "Claude Code (Opus) on the claude block; edits a copy in the round's folder, cannot build"],
  ], { x: 6.5, y: 1.35, w: 6.0, colW: [1.2, 4.8], boldFirstCol: true, fs: 12 });
}

// 3. CHIA và vòng agent
{
  const s = pres.addSlide(); title(s, "2. Inputs, Outputs and the Agent Loop", 3);
  table(s, [
    ["Step", "Block", "Input (written by us)", "Output"],
    ["Sim", "chipyard, verilator", "rtl/<demo>_alu.v, sim/tb_<demo>_alu.v, run_sim.sh", "log with RESULT: PASS + checks, VCD"],
    ["FPGA", "vivado", "RTL, fpga_top.v, tb_fpga_top.v, golden_sig.py, .xdc, build.tcl, sources.f", ".bit, timing / utilization / power reports"],
    ["ASIC", "asic", "RTL, config.mk, constraint.sdc", "GDS, layout.png, area / timing / power / DRC reports"],
    ["Agent", "claude", "TASK.md, context/ (best reports, round history)", "edited files, summary.md, changes.diff"],
    ["Flow", "head", "baseline.py or agent.py", "results/*.json, compare.md, status.html, profile/"],
  ], { x: 0.85, y: 1.3, w: 8.4, colW: [0.7, 1.35, 3.45, 2.9], boldFirstCol: true, fs: 10 });
  caption(s, "Each step is a @ChiaFunction(resources=...) that Ray sends to its block. Every run copies the inputs to ~/chia_demo/demo-alu/<demo>/<run_id>/ and writes all outputs there.", 0.85, 3.08, 8.4, 0.45);
  placeholder(s, "[Insert figure: CHIA timeline of one baseline run\n(profile/ChiaProfileCollector_profile.html)]", 9.55, 1.3, 2.95, 2.1);
  head(s, "One agent round (Sim → FPGA → ASIC, at most 2 rounds per step)", 0.85, 3.58, 11.65);
  const p2 = [["Copy best", "into the round's folder"], ["Agent edits", "reads reports + history"], ["CHIA reverts", "out-of-scope edits"], ["Re-measure", "real tool on its block"], ["Gate + rule", "accept or reject"]];
  p2.forEach((b, i) => { const x = 0.85 + i * 2.38; box(s, b[0], b[1], x, 3.98, 1.95, 0.6, i === 4); if (i < 4) arrow(s, x + 1.95, 4.28, x + 2.38); });
  caption(s, "Accepted round = base for the next round and step; a rejected round's reason goes into the next prompt. CHIA reverts every edit outside the allowed files before measuring.", 0.85, 4.62, 11.65, 0.4);
  table(s, [
    ["Step", "Agent may edit", "Gate", "Accept when", "Why only these files"],
    ["Sim", "sim/tb_<DUT>.v", "RESULT: PASS", "number of checks increases", "Tests the design harder without touching it"],
    ["FPGA", "build.tcl, sources.f, new .v in fpga/", "xsim PASS, bitstream, WNS ≥ 0", "no worse in WNS, dyn. power, LUT+FF; better in one (strict Pareto)", "FPGA-only build; new files keep rtl/ unchanged for ASIC"],
    ["ASIC", "asic/config.mk", "GDS, setup/hold slack ≥ 0, DRC = 0", "cell area, die area, power no worse than 1 %; one better by > 1 %", "ORFS knobs only: how to build, not what"],
    ["Locked", "rtl/, .xdc, .sdc, part, clock, fpga_top + MISR checker", "", "", "Shared by FPGA and ASIC, or define the target and the yardstick"],
  ], { x: 0.85, y: 5.05, w: 11.65, colW: [0.75, 2.4, 2.05, 3.35, 3.1], boldFirstCol: true, fs: 10.5 });
}

// 4. Baseline
{
  const s = pres.addSlide(); title(s, "3. Baseline Results", 4);
  table(s, [
    ["Step", "Metric", "NoCG", "CG"],
    ["Sim", "result, checks", "PASS, 6 272", "PASS, 6 272"],
    ["FPGA", "LUT / FF", "138 / 139", "140 / 141"],
    ["", "WNS / WHS (ns)", "4.579 / 0.024", "1.502 / 0.052"],
    ["", "total / dynamic power (W)", "0.099 / 0.002", "0.102 / 0.005"],
    ["ASIC", "cell area (µm²), util", "3 078, 44.8 %", "2 975, 46.9 %"],
    ["", "die area (µm²)", "7 534", "6 926"],
    ["", "cells (flop, ICG, clk buf)", "356 (31, 0, 9)", "306 (31, 2, 18)"],
    ["", "setup / hold slack (ns)", "6.815 / 0.493", "6.957 / 0.406"],
    ["", "power (mW), DRC", "0.368, 0", "0.324, 0"],
  ], { x: 0.85, y: 1.35, w: 6.6, colW: [0.8, 2.5, 1.65, 1.65], boldFirstCol: true });
  head(s, "Same RTL, opposite results", 7.95, 1.35, 4.55);
  stat(s, "4.58 → 1.50 ns", "FPGA WNS with CG: the clock passes a latch and a LUT, leaving half a cycle. Dynamic power 2 → 5 mW; Vivado warns of a gated clock.", 7.95, 1.85, 4.55);
  stat(s, "−12 %", "ASIC power with CG: the ICG replaces the feedback mux, so 50 fewer cells and cell area −3 %, but clock buffers double (9 → 18). OpenROAD CTS handles the ICG correctly.", 7.95, 3.55, 4.55);
  bullets(s, [
    "This is why an FPGA optimization must not reach the ASIC branch.",
    "Run time per demo: FPGA ≈ 64 s, ASIC ≈ 2.5 min.",
  ], { x: 7.95, y: 5.15, w: 4.55, h: 1.1, fs: 14 });
}

// 5. Kết quả agent + Sim
{
  const s = pres.addSlide(); title(s, "4. Agent Results vs Baseline", 5);
  table(s, [
    ["Metric", "NoCG: baseline → agent", "CG: baseline → agent"],
    ["Sim checks (still PASS)", "6 272 → 542 046 (×86)", "6 272 → 1 509 090 (×241)"],
    ["FPGA", "2 rounds rejected, baseline kept", "2 rounds rejected, baseline kept"],
    ["ASIC cell area (µm²)", "3 077.95 → 3 021.65 (−1.8 %)", "2 975.35 → 2 835.22 (−4.7 %)"],
    ["ASIC die area (µm²)", "7 534.24 → 4 641.70 (−38.4 %)", "6 925.57 → 5 504.90 (−20.5 %)"],
    ["ASIC power (mW)", "0.368 → 0.361 (−1.8 %)", "0.324 → 0.302 (−6.8 %)"],
    ["ASIC setup slack (ns), DRC", "6.815 → 7.034, 0", "6.957 → 7.042, 0"],
  ], { x: 0.85, y: 1.35, w: 7.6, colW: [2.3, 2.65, 2.65], boldFirstCol: true, fs: 12 });
  caption(s, "agent.py --rounds 2, both demos finished OK. Accepted: sim-r1, sim-r2, asic-r1, asic-r2. About 22 min per demo.", 0.85, 3.6, 7.6, 0.45);
  s.addChart(pres.charts.BAR, [
    { name: "Baseline", labels: ["NoCG", "CG"], values: [7534, 6926] },
    { name: "Agent", labels: ["NoCG", "CG"], values: [4642, 5505] },
  ], { x: 8.8, y: 1.3, w: 3.7, h: 3.2, barDir: "col", barGrouping: "clustered", chartColors: ["BFBFBF", "595959"],
    showTitle: true, title: "ASIC die area (µm²)", titleFontFace: F, titleFontSize: 13, titleColor: K.black,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "#,##0", dataLabelFontFace: F, dataLabelFontSize: 11, dataLabelFontBold: true, dataLabelColor: K.black,
    catAxisLabelFontFace: F, catAxisLabelFontSize: 12, catAxisLabelColor: K.black, valAxisLabelFontFace: F, valAxisLabelFontSize: 10, valAxisLabelColor: K.mute,
    valGridLine: { color: "E5E5E5", size: 0.5 }, catGridLine: { style: "none" }, showLegend: true, legendPos: "b", legendFontFace: F, legendFontSize: 11 });
  head(s, "Sim: what the agent added to the testbench", 0.85, 4.3, 11.65);
  bullets(s, [
    "Round 1: cases the original testbench missed: valid exactly 2 cycles after input, edge values (7F/80, FF/00), shifts with b[7:3] ≠ 0, enable patterns, reset mid-run.",
    "Round 2: every operand pair (256 × 256) for ADD, SUB and SLT.",
    "CG only: counts clock edges after the ICG every cycle, to check the latch never creates a glitch.",
  ], { x: 0.85, y: 4.75, w: 11.65, h: 1.8, fs: 14 });
}

// 6. FPGA
{
  const s = pres.addSlide(); title(s, "5. FPGA: All Four Agent Rounds Rejected", 6);
  table(s, [
    ["Round", "What the agent did", "Measured", "Result"],
    ["NoCG r1", "Retiming, hand-mapped LUT6_2/CARRY4, Explore directives", "WNS 4.58 → 4.94 ns; dyn. power 2 → 5 mW; LUT+FF 277 → 302", "Rejected"],
    ["NoCG r2", "Keep input registers, one shared adder for ADD/SUB/SLT", "LUT+FF 277 → 262; WNS 4.58 → 4.21 ns", "Rejected"],
    ["CG r1", "ICG as an AND gate + gated_clock_conversion, so Vivado turns gating into FF clock enables", "WNS 1.50 → 4.50 ns; dyn. power 5 → 2 mW; LUT+FF 281 → 284", "Rejected"],
    ["CG r2", "FPGA-only ALU using clock enables, no latch on the clock, plus retiming", "WNS 1.50 → 4.44 ns; LUT+FF 281 → 312", "Rejected"],
  ], { x: 0.85, y: 1.35, w: 11.65, colW: [1.1, 4.6, 4.75, 1.2], boldFirstCol: true });
  bullets(s, [
    "Each proposal improves one metric and worsens another, so the strict Pareto rule rejects all four and the baseline stays.",
    "Before submitting, the agent checked its logic with a Python model over all 524 288 op × a × b cases (0 errors). Its resource predictions were still wrong; only the real Vivado run showed that.",
    "The agent learns from rejection: in NoCG r2 it read why r1 failed (dynamic power) and went back to keeping the input registers.",
    "CG r1 is the notable trade-off. It fixed the real FPGA problem of clock gating (WNS ×3, dynamic power −60 %) at the cost of 3 LUT+FF cells. The rule was fixed before the run and is not relaxed after seeing results.",
  ], { x: 0.85, y: 3.55, w: 11.65, h: 3.0, fs: 15 });
}

// 7. ASIC
{
  const s = pres.addSlide(); title(s, "6. ASIC: Agent Tunes the ORFS Config", 7);
  table(s, [
    ["Round", "NoCG", "CG"],
    ["r1", "ABC_AREA=1, CORE_UTILIZATION 40 → 55, PLACE_DENSITY 0.65 → 0.75: die −27.5 %", "ABC_AREA=1 only, because the earlier CG route needed 6 iterations to clear DRC: power −7.1 %"],
    ["r2", "CORE_UTILIZATION 55 → 65, PLACE_DENSITY 0.75 → 0.85: die a further −15 %", "CORE_UTILIZATION 40 → 50, DONT_BUFFER_PORTS=1: cell area −4 %, die −19.5 %"],
  ], { x: 0.85, y: 1.35, w: 6.6, colW: [0.7, 2.95, 2.95], boldFirstCol: true, fs: 12 });
  bullets(s, [
    "The agent may edit only config.mk.",
    "ABC_AREA=1 makes Yosys synthesize for area, since timing has plenty of slack. Higher utilization and density pack cells tighter, so the die shrinks.",
    "Every round passes the gate; setup slack even improves slightly.",
    "Cell count rises 26 to 31 % because area-driven synthesis picks more, smaller cells.",
    "The agent noted the limit of this step: flops and the clock tree are about 70 % of NoCG power, set by the RTL, not by config.mk.",
  ], { x: 0.85, y: 3.15, w: 6.6, h: 3.5, fs: 14 });
  placeholder(s, "[Insert figure: NoCG baseline layout\nasic/layout.png]", 7.95, 1.35, 2.2, 2.6);
  placeholder(s, "[Insert figure: NoCG agent layout\nasic/layout.png]", 10.3, 1.35, 2.2, 2.6);
  placeholder(s, "[Insert figure: CG baseline layout]", 7.95, 4.1, 2.2, 2.6);
  placeholder(s, "[Insert figure: CG agent layout]", 10.3, 4.1, 2.2, 2.6);
}

// 8. Kết luận
{
  const s = pres.addSlide(); title(s, "Conclusions, Limitations and Next Steps", 8);
  head(s, "Conclusions", 0.85, 1.35, 5.6);
  bullets(s, [
    "The flow runs end to end: CHIA schedules Sim (Verilator), FPGA (Vivado, bitstream) and ASIC (Yosys + OpenROAD, GDS) on 5 blocks, with a gate at every step.",
    "The agent contributes at every step: Sim checks ×86 to ×241, ASIC die −20 to −38 %, CG power −6.8 %. No FPGA round was accepted, but it found the clock-gating problem.",
    "Real measurement is required: some agent predictions were wrong, and the propose, measure, accept-or-reject loop never ends worse than baseline.",
    "FPGA and ASIC must stay separate: clock gating helps ASIC and hurts FPGA. With rtl/ fixed, FPGA-only edits do not reach ASIC.",
  ], { x: 0.85, y: 1.8, w: 5.6, h: 5.0, fs: 14 });
  head(s, "Limitations", 6.9, 1.35, 5.6);
  bullets(s, [
    "Tiny design (about 300 cells) and fixed RTL leave little room to optimize.",
    "Sim \"checks\" count comparisons, not coverage; no gate-level sim or LVS after the agent's ASIC changes.",
    "Power is estimated: OpenSTA default activity, Vivado vector-less.",
    "One agent sample per round; no board test yet (FPGA stops at bitstream).",
  ], { x: 6.9, y: 1.8, w: 5.6, h: 2.6, fs: 13.5 });
  head(s, "Next steps", 6.9, 4.0, 5.6);
  table(s, [
    ["Task", "Status"],
    ["Video of a run with the live status page", "Not started"],
    ["Gate-level sim of the ASIC netlist, Verilator coverage", "Proposed"],
    ["Demo 2: PicoRV32 mini-SoC, same loop and blocks", "Optional"],
  ], { x: 6.9, y: 4.45, w: 5.6, colW: [4.1, 1.5], fs: 12.5 });
}

pres.writeFile({ fileName: "/home/user/CHIA-workflow/projects/demo-alu/report/ProgressReport_22-11.pptx" }).then(f => console.log("wrote", f));
