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
  s.addText(t, { x, y, w, h: h || 0.3, fontFace: F, fontSize: 11, italic: true, color: K.cap, margin: 0, isTextBox: true });
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
  head(s, "Goal", 0.85, 1.35, 4.9);
  bullets(s, [
    ["Question:", "can CHIA run a complete open-source flow, Sim → FPGA → ASIC, with a Claude agent evaluating and optimizing each step?"],
    ["Design:", "small on purpose, so every step runs in minutes and ASIC reaches GDS."],
    ["Two demos:", "NoCG (plain clock enable) and CG (clock gating cell), same flow."],
    ["Three phases:", "1 baseline (no agent) · 2 agent, ≤ 2 rounds per step · 3 comparison table."],
  ], { x: 0.85, y: 1.8, w: 4.9, h: 3.6 });
  s.addText([{ text: "Baseline run (phase 1)", options: { bold: true, fontSize: 14, breakLine: true } },
             { text: "≈ 4 min per demo", options: { bold: true, fontSize: 30, breakLine: true } },
             { text: "Sim + FPGA bitstream + ASIC GDS · re-run gives identical numbers", options: { fontSize: 13 } }],
    { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x: 0.85, y: 5.25, w: 4.9, h: 1.45, fontFace: F, color: K.black,
      fill: { color: K.soft }, line: { color: K.soft }, margin: 10, valign: "middle" });
  table(s, [
    ["Item", "Choice"],
    ["Design", "8-bit ALU, 8 ops, 2-stage pipeline; CG variant gates input/output registers with an ICG"],
    ["Sim", "Verilator 5.022, self-checking testbench vs a reference model"],
    ["FPGA", "Vivado 2025.1, Arty A7-100T, 100 MHz; wrapper with LFSR stimulus + MISR check"],
    ["ASIC", "Yosys + OpenROAD (ORFS), sky130hd, 10 ns, RTL → GDS"],
    ["Agent", "Claude Code (Opus) on the claude block; edits files, cannot build"],
    ["Cluster", "5 blocks: chipyard, verilator, vivado, asic, claude; shared WORK_DIR"],
  ], { x: 6.1, y: 1.4, w: 6.4, colW: [1.1, 5.3], boldFirstCol: true });
  placeholder(s, "[Insert figure: CHIA timeline of one baseline run\n(profile/ChiaProfileCollector_profile.html)]", 6.1, 5.25, 6.4, 1.45);
}

// 3. Flow trên CHIA
{
  const s = pres.addSlide(); title(s, "2. The Flow on CHIA", 3);
  head(s, "Phase 1: each step is a CHIA task on the block that has its tool", 0.85, 1.3, 11.65);
  const p1 = [["sim_build", "chipyard · Verilator"], ["sim_run", "verilator · Vtb"], ["fpga_build", "vivado · synth → bit"], ["asic_stage ×6", "asic · synth → GDS"], ["results", "JSON + status.html"]];
  p1.forEach((b, i) => { const x = 0.85 + i * 2.38; box(s, b[0], b[1], x, 1.8, 1.95, 0.8, i === 4); if (i < 4) arrow(s, x + 1.95, 2.2, x + 2.38); });
  head(s, "Phase 2: one agent round", 0.85, 2.85, 11.65);
  const p2 = [["Copy best", "into round dir"], ["Agent edits", "claude block, no build"], ["Revert", "out-of-scope edits"], ["Re-measure", "real tool, same block"], ["Gate + rule", "accept / reject"]];
  p2.forEach((b, i) => { const x = 0.85 + i * 2.38; box(s, b[0], b[1], x, 3.35, 1.95, 0.8, i === 4); if (i < 4) arrow(s, x + 1.95, 3.75, x + 2.38); });
  caption(s, "Accepted round becomes the base of the next round and the next step; rejected round's reason goes into the next prompt", 0.85, 4.25, 11.65);
  table(s, [
    ["Step", "Agent may edit", "Gate", "Accept when"],
    ["Sim", "testbench", "RESULT: PASS", "number of checks increases"],
    ["FPGA", "build.tcl, sources.f, new .v in fpga/", "xsim PASS, .bit, WNS ≥ 0", "strict Pareto: no worse in WNS, dynamic power, LUT+FF; better in one"],
    ["ASIC", "config.mk only", "GDS, slack ≥ 0, DRC = 0", "no worse than 1 % in cell area, die area, power; better by > 1 % in one"],
  ], { x: 0.85, y: 4.7, w: 11.65, colW: [0.9, 3.0, 2.6, 5.15], boldFirstCol: true, fs: 12.5 });
}

// 4. Baseline
{
  const s = pres.addSlide(); title(s, "3. Baseline: Clock Gating Cuts Both Ways", 4);
  table(s, [
    ["Step", "Metric", "NoCG", "CG"],
    ["Sim", "result, checks", "PASS, 6 272", "PASS, 6 272"],
    ["FPGA", "LUT / FF", "138 / 139", "140 / 141"],
    ["", "WNS (ns)", "4.579", "1.502"],
    ["", "dynamic power (W)", "0.002", "0.005"],
    ["ASIC", "cell area (µm²)", "3 078", "2 975"],
    ["", "die area (µm²)", "7 534", "6 926"],
    ["", "cells (flop, ICG, clk buf)", "356 (31, 0, 9)", "306 (31, 2, 18)"],
    ["", "power (mW)", "0.368", "0.324"],
    ["", "setup WS (ns), DRC", "6.815, 0", "6.957, 0"],
  ], { x: 0.85, y: 1.35, w: 6.6, colW: [0.8, 2.6, 1.6, 1.6], boldFirstCol: true });
  stat(s, "4.58 → 1.50 ns", "FPGA WNS with clock gating: latch + LUT in the clock path leaves half a cycle", 7.95, 1.45, 4.55);
  stat(s, "−12 %", "ASIC power with clock gating; 50 fewer cells (no feedback mux), 2× clock buffers", 7.95, 2.85, 4.55);
  bullets(s, [
    "Same RTL, opposite results: an FPGA optimization must not leak into ASIC.",
    "RTL, XDC and SDC are fixed for the agent; FPGA-only changes go into new files under fpga/.",
    "Tools are deterministic: two baseline runs give identical numbers.",
  ], { x: 7.95, y: 4.3, w: 4.55, h: 2.4, fs: 14 });
}

// 5. Kết quả agent
{
  const s = pres.addSlide(); title(s, "4. Agent Results vs Baseline", 5);
  s.addChart(pres.charts.BAR, [
    { name: "Baseline", labels: ["NoCG", "CG"], values: [7534, 6926] },
    { name: "Agent", labels: ["NoCG", "CG"], values: [4642, 5505] },
  ], { x: 0.85, y: 1.35, w: 6.4, h: 4.3, barDir: "col", barGrouping: "clustered", chartColors: ["BFBFBF", "595959"],
    showTitle: true, title: "ASIC die area (µm²)", titleFontFace: F, titleFontSize: 14, titleColor: K.black,
    showValue: true, dataLabelPosition: "outEnd", dataLabelFormatCode: "#,##0", dataLabelFontFace: F, dataLabelFontSize: 13, dataLabelFontBold: true, dataLabelColor: K.black,
    catAxisLabelFontFace: F, catAxisLabelFontSize: 13, catAxisLabelColor: K.black, valAxisLabelFontFace: F, valAxisLabelFontSize: 11, valAxisLabelColor: K.mute,
    valGridLine: { color: "E5E5E5", size: 0.5 }, catGridLine: { style: "none" }, showLegend: true, legendPos: "b", legendFontFace: F, legendFontSize: 12 });
  caption(s, "2 rounds per step, both demos; all accepted ASIC rounds pass GDS, slack ≥ 0, DRC = 0", 0.85, 5.75, 6.4);
  stat(s, "×86 / ×241", "Sim checks (NoCG / CG), testbench still PASS", 7.75, 1.45, 4.75);
  stat(s, "−38.4 % / −20.5 %", "ASIC die area (NoCG / CG); CG power −6.8 %", 7.75, 2.75, 4.75);
  stat(s, "0 of 4", "FPGA rounds accepted under the strict Pareto rule", 7.75, 4.05, 4.75);
  bullets(s, [["Steps accepted:", "sim-r1, sim-r2, asic-r1, asic-r2 in both demos; about 22 min per demo."]], { x: 7.75, y: 5.4, w: 4.75, h: 0.9, fs: 14 });
}

// 6. FPGA
{
  const s = pres.addSlide(); title(s, "5. FPGA: Four Proposals, None Wins on Every Metric", 6);
  table(s, [
    ["Round", "What the agent did", "Measured", "Result"],
    ["NoCG r1", "Retiming, hand-mapped LUT6_2/CARRY4, Explore directives", "WNS 4.58 → 4.94 ns; dyn. power 2 → 5 mW; LUT+FF 277 → 302", "Rejected"],
    ["NoCG r2", "Keep input registers, one shared adder for ADD/SUB/SLT", "LUT+FF 277 → 262; WNS 4.58 → 4.21 ns", "Rejected"],
    ["CG r1", "ICG as AND + gated_clock_conversion (gating → FF clock enable)", "WNS 1.50 → 4.50 ns; dyn. power 5 → 2 mW; LUT+FF 281 → 284", "Rejected"],
    ["CG r2", "FPGA-only ALU with clock enable, no latch on clock + retiming", "WNS 1.50 → 4.44 ns; LUT+FF 281 → 312", "Rejected"],
  ], { x: 0.85, y: 1.35, w: 11.65, colW: [1.1, 4.6, 4.75, 1.2], boldFirstCol: true });
  bullets(s, [
    ["Self-check without tools:", "the agent wrote Python models and checked 524 288 op × a × b cases (0 errors); resource predictions were still wrong, only real measurement tells."],
    ["Learns from rejection:", "NoCG r2 read why r1 failed (power) and went back to keeping input registers."],
    ["CG r1 is the notable trade-off:", "it fixed the real FPGA problem of clock gating (WNS ×3, dynamic power −60 %) at the cost of 3 LUT+FF cells. The rule was fixed before the run and is kept; a WNS-first rule would need a new run."],
  ], { x: 0.85, y: 3.75, w: 11.65, h: 2.8, fs: 14 });
}

// 7. ASIC
{
  const s = pres.addSlide(); title(s, "6. ASIC: Agent Tunes the ORFS Config", 7);
  table(s, [
    ["Round", "NoCG", "CG"],
    ["r1", "ABC_AREA=1, CORE_UTILIZATION 40 → 55, PLACE_DENSITY 0.65 → 0.75: die −27.5 %", "ABC_AREA=1 only (CG route needed 6 DRC iterations): power −7.1 %"],
    ["r2", "CORE_UTILIZATION 55 → 65, PLACE_DENSITY 0.75 → 0.85: die further −15 %", "CORE_UTILIZATION 40 → 50, DONT_BUFFER_PORTS=1: cell area −4 %, die −19.5 %"],
  ], { x: 0.85, y: 1.35, w: 6.6, colW: [0.7, 2.95, 2.95], boldFirstCol: true, fs: 12 });
  bullets(s, [
    "Every round passes the gate; setup slack even improves slightly.",
    "Cell count +26–31 %: area-driven ABC uses more, smaller cells.",
    "The agent notes the limit of this step: flops and clock tree are about 70 % of NoCG power, set by RTL, not by config.mk.",
  ], { x: 0.85, y: 3.2, w: 6.6, h: 2.4, fs: 14 });
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
    ["Flow works end to end:", "CHIA schedules Sim, FPGA (bitstream) and ASIC (GDS) on 5 blocks, each step gated."],
    ["Agent helps at every step:", "Sim checks ×86–241, ASIC die −20 to −38 %; on FPGA it found the clock-gating problem."],
    ["Real measurement is required:", "agent predictions were partly wrong; the propose → measure → accept loop never ends worse than baseline."],
    ["FPGA and ASIC stay separate:", "clock gating helps ASIC, hurts FPGA."],
  ], { x: 0.85, y: 1.8, w: 5.6, h: 4.9, fs: 14 });
  head(s, "Limitations", 6.9, 1.35, 5.6);
  bullets(s, [
    "Tiny design (~300 cells), fixed RTL: little room to optimize.",
    "\"checks\" counts comparisons, not coverage; no gate-level sim or LVS after the agent's ASIC changes.",
    "Power is estimated (OpenSTA default activity, Vivado vector-less).",
    "One agent sample per round; no board yet (stops at bitstream).",
  ], { x: 6.9, y: 1.8, w: 5.6, h: 2.6, fs: 14 });
  head(s, "Next steps", 6.9, 4.05, 5.6);
  table(s, [
    ["Task", "Status"],
    ["Video of a run with the live status page", "Not started"],
    ["Gate-level sim of the ASIC netlist, Verilator coverage", "Proposed"],
    ["Demo 2: PicoRV32 mini-SoC, same loop and blocks", "Optional"],
  ], { x: 6.9, y: 4.5, w: 5.6, colW: [4.1, 1.5], fs: 12.5 });
}

pres.writeFile({ fileName: "/home/user/CHIA-workflow/projects/demo-alu/report/ProgressReport_22-11.pptx" }).then(f => console.log("wrote", f));
