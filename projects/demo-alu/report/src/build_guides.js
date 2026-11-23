// build_guides.js - 4 deck báo cáo cho thầy hướng dẫn (tiếng Anh), cùng style với ProgressReport_22-11.pptx:
//   guides/0_Setup.pptx  cách đã setup CHIA (máy thật) + kế hoạch worker remote/cloud
//   guides/1_Sim.pptx, 2_FPGA.pptx, 3_ASIC.pptx  từng bước của demo ALU, ví dụ NoCG/CG
// Quy ước (phỏng vấn 23/11/2026): không dùng speaker notes, mọi thứ trình bày nằm trên slide; chữ ≥ 14 pt;
// mỗi slide một dòng kết luận đậm ở cuối; mỗi bước một dòng lệnh chính; chỉ giữ vài tên file quan trọng.
// Chạy: NODE_PATH=<thư mục có pptxgenjs>/node_modules node build_guides.js [thư mục ra]
// Số liệu lấy từ REPORT.md và docs/demo-task.md (chạy thật 20–21/11/2026).
const pptxgen = require("pptxgenjs");
const path = require("path");
const OUT = process.argv[2] || path.join(__dirname, "..", "guides");
const IMG = path.join(__dirname, "..", "img") + "/";
const F = "Times New Roman", M = "Courier New";
const K = { black: "000000", ink: "262626", mute: "5A5A5A", cap: "9A9A9A", line: "BFBFBF", head: "D9D9D9", soft: "F2F2F2", grid: "A6A6A6", bar: "A6A6A6" };
const FS = 14; // cỡ chữ nhỏ nhất của nội dung

function deck(name, titleText) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.title = titleText;
  pres.theme = { headFontFace: F, bodyFontFace: F };
  let n = 0;
  const H = {
    pres,
    slide(t) {
      const s = pres.addSlide(); n += 1;
      if (t) s.addText(t, { x: 0.85, y: 0.45, w: 11.65, h: 0.7, fontFace: F, fontSize: 28, bold: true, color: K.black, margin: 0, isTextBox: true });
      s.addText(name, { x: 0.85, y: 7.0, w: 6, h: 0.3, fontFace: F, fontSize: 10, color: K.cap, margin: 0, isTextBox: true });
      s.addText(String(n), { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontFace: F, fontSize: 10, color: K.cap, align: "right", margin: 0, isTextBox: true });
      return s;
    },
    head(s, t, x, y, w) {
      s.addText(t, { x, y, w, h: 0.38, fontFace: F, fontSize: 17, bold: true, color: K.black, margin: 0, isTextBox: true });
    },
    bullets(s, items, o) {
      const runs = [];
      items.forEach((it, i) => {
        runs.push({ text: it, options: { bullet: { indent: 16 }, paraSpaceAfter: 6, color: K.ink, breakLine: i < items.length - 1 } });
      });
      s.addText(runs, { fontFace: F, fontSize: o.fs || 15, valign: "top", margin: 0, isTextBox: true, x: o.x, y: o.y, w: o.w, h: o.h });
    },
    para(s, t, o) {
      s.addText(t, { fontFace: F, fontSize: o.fs || 15, color: o.color || K.ink, valign: o.valign || "top", margin: 0, isTextBox: true, x: o.x, y: o.y, w: o.w, h: o.h, italic: !!o.italic, bold: !!o.bold, align: o.align });
    },
    // dòng kết luận đậm cuối slide
    msg(s, t) {
      s.addShape(pres.shapes.LINE, { x: 0.85, y: 6.2, w: 11.65, h: 0, line: { color: K.line, width: 1 } });
      s.addText("→  " + t, { x: 0.85, y: 6.27, w: 11.65, h: 0.55, fontFace: F, fontSize: 17, bold: true, color: K.black, valign: "middle", margin: 0, isTextBox: true });
    },
    // một dòng lệnh chính
    cmd(s, t, x, y, w) {
      s.addText([{ text: "Run  ", options: { fontFace: F, bold: true, color: K.mute } }, { text: t, options: { fontFace: M, color: K.black } }],
        { x, y, w, h: 0.45, fontSize: FS, fill: { color: K.soft }, line: { color: K.line, width: 0.75 }, valign: "middle", margin: [2, 8, 2, 8], isTextBox: true });
    },
    result(s, t, x, y, w, h, label) {
      s.addText([{ text: (label || "Result") + "  ", options: { bold: true, color: K.black } }, { text: t, options: { color: K.ink } }],
        { x, y, w, h: h || 0.5, fontFace: F, fontSize: FS, valign: "middle", margin: [4, 8, 4, 8], line: { color: K.ink, width: 1, dashType: "dash" }, isTextBox: true });
    },
    caption(s, t, x, y, w, h) {
      s.addText(t, { x, y, w, h: h || 0.32, fontFace: F, fontSize: FS, italic: true, color: K.mute, margin: 0, isTextBox: true });
    },
    box(s, a, b, x, y, w, h, dark, fs) {
      fs = fs || FS;
      const parts = [{ text: a, options: { bold: true, fontSize: fs, breakLine: !!b } }];
      if (b) parts.push({ text: b, options: { fontSize: FS } });
      s.addText(parts, { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, x, y, w, h, fontFace: F, align: "center", valign: "middle",
        color: dark ? "FFFFFF" : K.black, fill: { color: dark ? K.ink : "FFFFFF" }, line: { color: K.ink, width: 1 }, margin: 3 });
    },
    frame(s, x, y, w, h) {
      s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: K.soft }, line: { color: K.line, width: 0.75, dashType: "dash" } });
    },
    tag(s, t) {
      s.addText(t, { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x: 9.9, y: 0.55, w: 2.6, h: 0.45, fontFace: F, fontSize: FS, bold: true,
        align: "center", valign: "middle", color: K.black, fill: { color: "FFFFFF" }, line: { color: K.ink, width: 1.25, dashType: "dash" }, margin: 2 });
    },
    arrow(s, x1, y1, x2, y2) {
      const o = { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), line: { color: K.ink, width: 1.25, endArrowType: "triangle" } };
      if (x2 < x1) o.flipH = true;
      if (y2 < y1) o.flipV = true;
      s.addShape(pres.shapes.LINE, o);
    },
    table(s, rows, o) {
      const data = rows.map((r, i) => r.map((c, j) => ({ text: String(c), options: {
        bold: (i === 0 && !o.noHeader) || (o.boldFirstCol && j === 0), fill: { color: i === 0 && !o.noHeader ? K.head : "FFFFFF" },
        fontFace: (o.monoCols || []).includes(j) && i > 0 ? M : F } })));
      s.addTable(data, { fontFace: F, fontSize: o.fs || FS, color: K.black, border: { type: "solid", pt: 0.75, color: K.grid },
        margin: [3, 6, 3, 6], valign: "middle", autoPage: false, x: o.x, y: o.y, w: o.w, colW: o.colW, rowH: o.rowH });
    },
    hflow(s, items, y, h, o) {
      o = o || {};
      const x0 = o.x || 0.85, W = o.w || 11.65, g = o.g || 0.35;
      const w = (W - (items.length - 1) * g) / items.length;
      items.forEach(([a, b], i) => {
        const x = x0 + i * (w + g);
        H.box(s, a, b, x, y, w, h, (o.dark || []).includes(i), o.fs);
        if (i < items.length - 1) H.arrow(s, x + w, y + h / 2, x + w + g, y + h / 2);
      });
    },
    flow(s, cur, y) {
      const st = [["Sim", "Verilator"], ["FPGA", "Vivado 2025.1"], ["ASIC", "Yosys + OpenROAD"]];
      st.forEach(([a, b], i) => {
        const x = 0.85 + i * 3.3;
        H.box(s, a, b, x, y, 2.4, 0.8, a === cur);
        if (i < 2) H.arrow(s, x + 2.4, y + 0.4, x + 3.3, y + 0.4);
      });
      H.caption(s, "each step starts only after the gate of the previous one passes", 10.15, y + 0.05, 2.35, 0.75);
    },
    // vòng agent: chung cho 3 deck
    loop(s, y, edit, rule) {
      const g = 0.28, w = (11.65 - 5 * g) / 6;
      const st = [["1. Copy", "best version"], ["2. Claude edits", edit], ["3. CHIA reverts", "out-of-scope edits"],
                  ["4. Re-measure", "real tool"], ["5. Gate", "pass / fail"], ["6. Compare", rule]];
      st.forEach(([a, b], i) => {
        const x = 0.85 + i * (w + g);
        H.box(s, a, b, x, y, w, 1.1, i === 1);
        if (i < st.length - 1) H.arrow(s, x + w, y + 0.55, x + w + g, y + 0.55);
      });
    },
    // biểu đồ cột (thang xám), series = [{name, labels, values}]
    bars(s, series, o) {
      s.addChart(pres.charts.BAR, series, {
        x: o.x, y: o.y, w: o.w, h: o.h, barDir: o.dir || "col", barGrouping: "clustered", barGapWidthPct: 60,
        chartColors: o.colors || [K.bar, K.ink], showTitle: !!o.title, title: o.title, titleFontFace: F, titleFontSize: 15, titleColor: K.black,
        showValue: true, dataLabelPosition: o.labelPos || "outEnd", dataLabelFontFace: F, dataLabelFontSize: FS, dataLabelColor: K.black,
        dataLabelFormatCode: o.fmt || "General",
        catAxisLabelFontFace: F, catAxisLabelFontSize: FS, catAxisLabelColor: K.ink, catAxisLineShow: true,
        valAxisHidden: true, catAxisHidden: !!o.noCat, valGridLine: { style: "none" }, catGridLine: { style: "none" },
        valAxisMinVal: o.min, valAxisMaxVal: o.max,
        showLegend: !!o.legend, legendPos: "b", legendFontFace: F, legendFontSize: FS,
      });
    },
    legend(s, x, y) {
      [["NoCG", K.bar], ["CG", K.ink]].forEach(([t, c], i) => {
        s.addShape(pres.shapes.RECTANGLE, { x: x + i * 1.5, y: y + 0.07, w: 0.25, h: 0.25, fill: { color: c }, line: { color: c } });
        s.addText(t, { x: x + i * 1.5 + 0.33, y, w: 1.0, h: 0.38, fontFace: F, fontSize: FS, color: K.ink, margin: 0, valign: "middle", isTextBox: true });
      });
    },
    cover(kicker, t1, t2, sub) {
      const s = pres.addSlide(); n += 1;
      s.addText(kicker, { x: 0.4, y: 1.45, w: 9, h: 0.35, fontFace: F, fontSize: 14, color: K.mute, charSpacing: 3, margin: 0, isTextBox: true });
      s.addText([{ text: t1, options: { breakLine: true } }, { text: t2 }],
        { x: 0.4, y: 2.0, w: 12, h: 1.5, fontFace: F, fontSize: 34, bold: true, color: K.black, valign: "middle", margin: 0, isTextBox: true });
      s.addText(sub, { x: 0.4, y: 3.55, w: 12, h: 0.4, fontFace: F, fontSize: 16, color: K.mute, margin: 0, isTextBox: true });
      s.addShape(pres.shapes.LINE, { x: 0.5, y: 4.3, w: 4.2, h: 0, line: { color: K.line, width: 1 } });
      s.addText([{ text: "Advisor: MSc. Phạm Thế Hùng", options: { breakLine: true } }, { text: "Student: Phan Thành Nhân" }],
        { x: 0.5, y: 4.5, w: 6, h: 0.8, fontFace: F, fontSize: 16, color: K.black, margin: 0, isTextBox: true });
      s.addText("Report series: 0 Setup · 1 Sim · 2 FPGA · 3 ASIC", { x: 0.5, y: 6.6, w: 8, h: 0.3, fontFace: F, fontSize: 14, color: K.cap, margin: 0, isTextBox: true });
      return s;
    },
    save(file) { return pres.writeFile({ fileName: path.join(OUT, file) }); },
  };
  return H;
}

// ====================================================================================
// 0. SETUP
// ====================================================================================
async function setupDeck() {
  const H = deck("Part 0 · Setup", "Part 0: How the CHIA Framework Was Set Up");
  H.cover("PART 0 · FRAMEWORK SETUP", "Setting up CHIA:", "How the Framework Was Installed",
    "A single-machine cluster of tool containers, used for the ALU demo, and a plan for remote / cloud workers");

  // 2. CHIA chạy thế nào
  {
    const s = H.slide("How CHIA Runs Work");
    H.box(s, "Head (user's PC)", "Ray head · driver script", 3.9, 1.35, 5.5, 0.85, true);
    [["chipyard", "Chisel, Verilator build"], ["verilator", "run simulations"], ["vivado", "FPGA build"], ["asic", "Yosys + OpenROAD"], ["claude", "LLM agent"]]
      .forEach(([a, b], i) => {
        const x = 0.85 + i * 2.37;
        H.box(s, a + " worker", b, x, 2.75, 2.15, 0.95);
        H.arrow(s, 6.65, 2.2, x + 1.075, 2.75);
      });
    H.caption(s, "Each worker is one Docker container with one set of tools; here all of them run on the head machine.", 0.85, 3.8, 11.65);
    H.table(s, [["Term", "Meaning"],
      ["Worker", "Container with one tool set; it offers a resource name, e.g. vivado"],
      ["Task", "Python function marked with the resource it needs; Ray runs it on a matching worker"],
      ["Gate", "Check in the driver on a task's result; the next step starts only if it passes"]],
      { x: 0.85, y: 4.3, w: 11.65, colW: [1.6, 10.05], boldFirstCol: true });
    H.msg(s, "Each tool runs in its own container; the driver asks for a resource and Ray picks the container.");
  }

  // 3. Ví dụ một task
  {
    const s = H.slide("Example: One Simulation Task Through CHIA");
    H.box(s, "Head: driver script", "calls the run task, waits", 0.85, 1.45, 3.2, 1.1, true);
    H.box(s, "Ray scheduler", "who offers verilator_run?", 5.05, 1.45, 3.0, 1.1);
    H.box(s, "verilator worker", "runs the simulation", 9.05, 1.45, 3.45, 1.1);
    H.box(s, "Gate in the driver", "RESULT: PASS → next task", 0.85, 3.4, 3.2, 1.0);
    H.box(s, "Shared run folder", "binary, log, waveform", 9.05, 3.4, 3.45, 1.0);
    H.arrow(s, 4.05, 2.0, 5.05, 2.0); H.arrow(s, 8.05, 2.0, 9.05, 2.0);
    H.arrow(s, 10.775, 2.55, 10.775, 3.4); H.arrow(s, 9.05, 2.55, 4.05, 3.4);
    H.arrow(s, 2.45, 2.55, 2.45, 3.4);
    H.para(s, "(1)", { x: 4.25, y: 1.6, w: 0.6, h: 0.35, fs: 15, bold: true, align: "center" });
    H.para(s, "(2)", { x: 8.25, y: 1.6, w: 0.6, h: 0.35, fs: 15, bold: true, align: "center" });
    H.para(s, "(3)", { x: 10.85, y: 2.8, w: 0.6, h: 0.35, fs: 15, bold: true, align: "center" });
    H.para(s, "(4) result via get()", { x: 5.6, y: 3.1, w: 2.6, h: 0.35, fs: 15, bold: true, align: "center" });
    H.para(s, "(5)", { x: 2.55, y: 2.8, w: 0.6, h: 0.35, fs: 15, bold: true });
    H.table(s, [
      ["1", "The driver calls the run task, which needs the resource verilator_run"],
      ["2", "Ray sends the task to the container that offers it"],
      ["3", "The container runs the binary in the shared run folder and writes the log there"],
      ["4", "The result (PASS / FAIL, number of checks) comes back to the driver"],
      ["5", "The driver checks the gate; only on PASS does the next task start"]],
      { x: 0.85, y: 4.6, w: 11.65, colW: [0.5, 11.15], boldFirstCol: true, noHeader: true });
    H.msg(s, "The driver only names a resource; CHIA and Ray decide where the task runs and bring the result back.");
  }

  // 4. Máy và phiên bản
  {
    const s = H.slide("Machine and Software Used");
    H.head(s, "Host (head node)", 0.85, 1.3, 5.3);
    H.table(s, [["", "Value"], ["OS", "Bazzite-DX (Fedora Atomic)"], ["CPU / RAM", "20 cores / 23 GB"],
      ["Disk", "≈ 259 GB free"], ["Containers", "Docker, no sudo"], ["Other machines", "none yet (plan: slides 11-12)"]],
      { x: 0.85, y: 1.7, w: 5.3, colW: [1.75, 3.55], boldFirstCol: true });
    H.head(s, "Software", 6.55, 1.3, 5.95);
    H.table(s, [["Component", "Version"], ["Python", "3.10.19 (conda env)"], ["Ray", "2.54.0"], ["CHIA", "chialoops 2.0.1"],
      ["Chipyard", "1.15.0 (upstream, tag pinned)"], ["Verilator", "5.022 (from Chipyard)"], ["Vivado", "2025.1"],
      ["Yosys / OpenROAD", "0.68 / ORFS"], ["Agent", "Claude Code"]],
      { x: 6.55, y: 1.7, w: 5.95, colW: [2.3, 3.65], boldFirstCol: true });
    H.msg(s, "Every version is pinned: Ray needs exactly the same Python (3.10.19) on the head and in every worker.");
  }

  // 5. Quy trình
  {
    const s = H.slide("Setup Process");
    H.hflow(s, [["1. Host", "Docker, SSH, linger"], ["2. Python + CHIA", "conda env"], ["3. Chipyard", "1.15.0"],
      ["4. FPGA, ASIC, agent", "Vivado, ORFS, Claude"], ["5. Cluster", "up + smoke test"]], 1.4, 1.0, { dark: [4] });
    H.table(s, [["Step", "Check that passed"],
      ["1. Host", "Docker without sudo, SSH to itself, environment check script passes"],
      ["2. Python + CHIA", "Python 3.10.19, Ray 2.54.0, chia command works"],
      ["3. Chipyard", "RocketConfig builds in Verilator, a RISC-V test program exits with 0"],
      ["4. FPGA, ASIC, agent", "Vivado 2025.1 found in its container; ORFS runs an example to GDS; Claude logged in"],
      ["5. Cluster", "All resources visible; smoke test prints KẾT QUẢ: OK"]],
      { x: 0.85, y: 2.75, w: 11.65, colW: [2.6, 9.05], boldFirstCol: true });
    H.caption(s, "08/11 first install · 14/11 removed and reinstalled from the written steps · 15/11 shutdown checked · 20-21/11 ALU demo", 0.85, 5.55, 11.65);
    H.msg(s, "Reinstalled from scratch on 14/11/2026 by following the written guide (docs/reinstall.md); every check passed.");
  }

  // 6. Host + Python
  {
    const s = H.slide("Steps 1-2 · Host and Python Environment");
    H.table(s, [["Requirement", "Why CHIA needs it", "How it was set up"],
      ["Docker without sudo", "CHIA calls docker to start every worker", "Bazzite-DX docker group"],
      ["SSH to its own IP", "chia up logs into the head over SSH, even with one machine", "sshd on, key without password"],
      ["Linger", "Keeps the Ray head running after that SSH session ends", "loginctl enable-linger"],
      ["Python 3.10.19", "Must equal the Python inside the worker images", "conda env chia_env"],
      ["CHIA + Ray", "The framework and its cluster commands", "pip, chialoops 2.0.1"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [2.6, 5.45, 3.6], boldFirstCol: true });
    H.cmd(s, "pip install chialoops==2.0.1", 0.85, 4.0, 6.0);
    H.msg(s, "The host only needs Docker, SSH and one conda env; all hardware tools run inside containers.");
  }

  // 7. Công cụ trên host, mount vào worker
  {
    const s = H.slide("Tools Live on the Host, Workers Mount Them");
    H.frame(s, 0.85, 1.35, 4.6, 4.15);
    H.para(s, "Host  ~/tools/", { x: 1.0, y: 1.42, w: 4.3, h: 0.38, fs: 16, bold: true });
    [["chipyard/", "Chipyard 1.15.0"], ["Xilinx/2025.1/", "Vivado, read-only"], ["OpenROAD-flow-scripts/", "ORFS"]]
      .forEach(([a, b], i) => H.box(s, a, b, 1.05, 1.9 + i * 0.85, 4.2, 0.72));
    H.box(s, "Shared run folder", "~/chia_demo, read-write", 1.05, 4.6, 4.2, 0.72, true);
    H.arrow(s, 5.45, 3.4, 6.75, 3.4);
    H.para(s, "mounted at the same path", { x: 5.4, y: 2.6, w: 1.45, h: 0.75, fs: 14, align: "center" });
    [["chipyard worker", "mounts chipyard/"], ["verilator worker", "own image only"], ["vivado worker", "mounts chipyard/, Xilinx/"],
     ["asic worker", "mounts ORFS"], ["claude worker", "chipyard/ read-only"]]
      .forEach(([a, b], i) => H.box(s, a, b, 6.75, 1.35 + i * 0.84, 5.75, 0.72));
    H.caption(s, "every worker also mounts the shared run folder", 6.75, 5.6, 5.75);
    H.msg(s, "One copy of each tool on the host, edited with a normal editor, shared by every worker that needs it.");
  }

  // 8. Cài công cụ
  {
    const s = H.slide("Steps 3-4 · Installing the Tools");
    H.table(s, [["Tool", "Version", "Installed by", "Check that passed"],
      ["Chipyard", "1.15.0", "setup script: clone, pin tag, full build in a container", "RocketConfig builds, test exits 0"],
      ["Vivado", "2025.1", "Xilinx installer + Arty board files", "Arty A7-100T bitstream via a CHIA job"],
      ["ORFS", "Yosys 0.68 + OpenROAD", "chia-orfs image, ORFS copied to ~/tools", "example design to GDS"],
      ["Claude Code", "-", "one login on the host", "used by the demo agent"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.65, 2.0, 4.3, 3.7], boldFirstCol: true });
    H.cmd(s, "scripts/setup-chipyard.sh --verify", 0.85, 3.75, 6.0);
    H.caption(s, "The Chipyard build takes hours; Vivado, ORFS and the agent are optional per project.", 0.85, 4.4, 11.65);
    H.msg(s, "All four tools were installed and checked on the user's machine (14/11/2026).");
  }

  // 9. Cluster theo khối
  {
    const s = H.slide("Step 5 · The Block Cluster");
    H.table(s, [["Block", "Tools inside", "Enabled by", "Used in the demo for"],
      ["chipyard", "Chipyard, Verilator", "default", "Sim build"], ["verilator", "simulation runtime", "default", "Sim run"],
      ["vivado", "Vivado 2025.1", "--fpga", "FPGA build and check"], ["asic", "Yosys + OpenROAD", "--asic", "6 ASIC stages"],
      ["claude", "Claude Code", "default", "agent sessions"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.8, 3.6, 2.0, 4.25], boldFirstCol: true });
    H.hflow(s, [["Cluster script", "choose blocks"], ["Check", "tools present?"], ["Write config", "only chosen blocks"], ["chia up", "head + containers"], ["ray status", "all resources"]],
      4.0, 0.95, { dark: [0] });
    H.cmd(s, "python scripts/chia-up-blocks.py --fpga --asic", 0.85, 5.3, 7.0);
    H.msg(s, "One script brings up exactly the blocks a project needs, and shuts them all down with --down.");
  }

  // 10. Smoke test
  {
    const s = H.slide("Bring-up and Smoke Test");
    H.table(s, [["Block", "What the smoke test checks", "Result"],
      ["chipyard", "Chipyard environment loaded; RocketConfig built through CHIA", "SUCCESS=True"],
      ["verilator", "Simulation runtime present", "OK"],
      ["vivado", "Vivado found inside the container", "vivado v2025.1"],
      ["asic", "Yosys and OpenROAD present, flow folder writable", "Yosys 0.68, writable"],
      ["all", "Whole smoke job", "KẾT QUẢ: OK"],
      ["shutdown", "--down removes the Ray head and every container", "nothing left running"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.6, 6.5, 3.55], boldFirstCol: true });
    H.cmd(s, "chia job submit --working-dir . -- python blocks_smoke.py", 0.85, 4.35, 7.6);
    H.msg(s, "KẾT QUẢ: OK on 14/11/2026; the same cluster then ran the whole ALU demo (Parts 1-3).");
  }

  // 11. Remote: kiến trúc
  {
    const s = H.slide("Remote Workers: Architecture");
    H.tag(s, "PLAN · NOT TESTED");
    H.box(s, "HEAD (user's PC)", "Ray head · driver · local workers", 0.85, 1.45, 4.2, 1.05, true);
    H.box(s, "REMOTE (lab PC or cloud VM)", "SSH · Docker · worker containers", 8.3, 1.45, 4.2, 1.05);
    H.arrow(s, 5.05, 1.75, 8.3, 1.75);
    H.arrow(s, 8.3, 2.2, 5.05, 2.2);
    H.para(s, "SSH: start containers", { x: 5.1, y: 1.33, w: 3.15, h: 0.35, fs: 14, align: "center" });
    H.para(s, "Ray ports, both ways", { x: 5.1, y: 2.25, w: 3.15, h: 0.35, fs: 14, align: "center" });
    H.head(s, "The remote machine needs", 0.85, 2.95, 5.5);
    H.table(s, [["Item", "Value"], ["Software", "SSH server, Docker, rsync"], ["Not needed", "conda, CHIA"],
      ["Hardware", "x86_64, ≥ 16 GB RAM, ≥ 120 GB disk"]], { x: 0.85, y: 3.35, w: 5.5, colW: [1.6, 3.9], boldFirstCol: true });
    H.head(s, "What changes", 6.85, 2.95, 5.65);
    H.bullets(s, ["Job code: nothing; Ray places tasks by resource.",
      "Firewall: each machine trusts only the other's IP.",
      "Other network or cloud VM: connect through Tailscale."], { x: 6.85, y: 3.35, w: 5.65, h: 2.6 });
    H.msg(s, "A remote machine only needs SSH, Docker and open Ray ports; the demo code does not change.");
  }

  // 12. Remote: các bước
  {
    const s = H.slide("Remote Workers: Planned Steps");
    H.tag(s, "PLAN · NOT TESTED");
    H.hflow(s, [["1. Prepare remote", "script installs SSH, Docker, firewall"], ["2. Check from head", "check script: no [FAIL]"],
      ["3. Describe cluster", "head IP, remote IP, user"], ["4. Bring up", "expect 7 nodes"]], 1.4, 1.2, { dark: [3] });
    H.cmd(s, "chia up configs/cluster-lan.yaml", 0.85, 2.85, 6.0);
    H.table(s, [["", "Lab PC on the same LAN", "Cloud VM / other network"],
      ["Connection", "LAN IPs", "Tailscale on both machines"],
      ["Firewall", "trust the other machine's IP", "Ray ports closed to the Internet"],
      ["To verify", "needs a second PC", "needs a VM with Docker, ≥ 120 GB"]],
      { x: 0.85, y: 3.6, w: 11.65, colW: [1.9, 4.6, 5.15], boldFirstCol: true });
    H.msg(s, "Scripts and config are written; testing waits for a second machine (lab PC or cloud VM).");
  }

  // 13. Trạng thái
  {
    const s = H.slide("Setup Status");
    H.table(s, [["Item", "Status"],
      ["Single-machine cluster with five blocks", "verified 14/11/2026"],
      ["Reinstall from scratch with the written guide", "verified 14/11/2026"],
      ["Clean shutdown", "verified 15/11/2026"],
      ["Shared run folder between blocks", "verified 20/11/2026"],
      ["ALU demo: Sim, FPGA, ASIC, baseline and agent", "verified 20-21/11/2026"],
      ["Remote worker on a LAN", "written, not tested"],
      ["Cloud VM through Tailscale", "written, not tested"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [7.65, 4.0] });
    H.msg(s, "Next: run the remote plan on a second machine, then spread simulations and ASIC runs over both.");
  }
  await H.save("0_Setup.pptx");
}

// ====================================================================================
// 1. SIM
// ====================================================================================
async function simDeck() {
  const H = deck("Part 1 · Simulation", "Part 1: Simulation with Verilator");
  H.cover("PART 1 · SIMULATION", "Step 1 of the Flow:", "Simulation with Verilator",
    "Build and run the ALU testbench on CHIA, then let Claude extend the testbench");

  {
    const s = H.slide("Where Sim Fits");
    H.flow(s, "Sim", 1.35);
    H.head(s, "Input", 0.85, 2.55, 5.5);
    H.bullets(s, ["ALU RTL: 8 bit, 8 ops, 2-stage pipeline (locked).", "Self-checking testbench with its own reference model.",
      "Seed 1, 20 000 cycles, en active 30 % of cycles."], { x: 0.85, y: 2.95, w: 5.5, h: 3.0 });
    H.head(s, "Output", 6.85, 2.55, 5.65);
    H.bullets(s, ["RESULT line: number of checks (checked) and errors.", "Waveform of the whole run.", "Numbers saved as the reference for the agent."],
      { x: 6.85, y: 2.95, w: 5.65, h: 3.0 });
    H.msg(s, "Sim is the first gate: FPGA and ASIC only start after the testbench prints RESULT: PASS.");
  }

  {
    const s = H.slide("Running the Baseline Sim on CHIA");
    H.box(s, "Driver", "runs on the head", 0.85, 1.4, 2.6, 0.95, true);
    H.box(s, "Build task", "chipyard worker · Verilator", 4.2, 1.4, 3.3, 0.95);
    H.box(s, "Run task", "verilator worker", 4.2, 2.85, 3.3, 0.95);
    H.box(s, "Shared run folder", "binary, log, waveform", 8.4, 2.1, 4.1, 0.95);
    H.box(s, "Gate", "RESULT: PASS ?", 4.2, 4.3, 3.3, 0.8);
    H.box(s, "FPGA step", "Part 2", 8.4, 4.3, 4.1, 0.8);
    H.arrow(s, 3.45, 1.875, 4.2, 1.875); H.arrow(s, 5.85, 2.35, 5.85, 2.85); H.arrow(s, 5.85, 3.8, 5.85, 4.3);
    H.arrow(s, 7.5, 1.875, 8.4, 2.4); H.arrow(s, 8.4, 2.8, 7.5, 3.325); H.arrow(s, 7.5, 4.7, 8.4, 4.7);
    H.para(s, "writes", { x: 7.45, y: 1.6, w: 1.0, h: 0.32, fs: 14, align: "center" });
    H.para(s, "reads", { x: 7.45, y: 3.45, w: 1.0, h: 0.32, fs: 14, align: "center" });
    H.para(s, "pass", { x: 7.5, y: 4.3, w: 0.9, h: 0.32, fs: 14, align: "center" });
    H.result(s, "both demos: PASS, 6 272 checks, 0 errors", 0.85, 2.85, 2.95, 1.25);
    H.cmd(s, "python run_demo.py NoCG baseline --stages sim", 0.85, 5.45, 7.0);
    H.msg(s, "Build and run are split over two containers that share one run folder.");
  }

  {
    const s = H.slide("Agent: Improve the Testbench");
    H.loop(s, 1.4, "testbench only", "checks must rise");
    H.head(s, "Rules for the agent", 0.85, 2.85, 5.6);
    H.bullets(s, ["Edits only the testbench; the RTL is the reference.", "Edits to other files are undone before measuring.",
      "Each round works in its own copy; prompt, diff and summary are kept."], { x: 0.85, y: 3.25, w: 5.6, h: 2.1 });
    H.head(s, "Gate and rule", 6.85, 2.85, 5.65);
    H.table(s, [["", "Sim"], ["Gate", "Verilator build + run prints RESULT: PASS"], ["Accept", "checked greater than the best so far"], ["Rounds", "up to 2"]],
      { x: 6.85, y: 3.25, w: 5.65, colW: [1.3, 4.35], boldFirstCol: true });
    H.cmd(s, "python run_demo.py NoCG agent --rounds 2", 0.85, 5.45, 7.0);
    H.msg(s, "A round counts only if Verilator still passes and the number of checks goes up.");
  }

  {
    const s = H.slide("Example · NoCG vs CG: Same Test, Different Clocking");
    H.head(s, "NoCG: clock enable", 0.85, 1.3, 5.6);
    H.box(s, "a, b", null, 0.85, 1.75, 1.0, 0.65);
    H.box(s, "MUX", "en ? a : a_q", 2.2, 1.75, 1.65, 0.65);
    H.box(s, "FF  a_q", "clock: clk", 4.2, 1.75, 2.25, 1.4, true);
    H.box(s, "clk", null, 0.85, 2.5, 1.0, 0.65);
    H.arrow(s, 1.85, 2.075, 2.2, 2.075); H.arrow(s, 3.85, 2.075, 4.2, 2.075); H.arrow(s, 1.85, 2.825, 4.2, 2.825);
    H.head(s, "CG: gated clock", 6.85, 1.3, 5.65);
    H.box(s, "clk, en", null, 6.85, 1.75, 1.2, 0.65);
    H.box(s, "ICG", "latch + AND", 8.4, 1.75, 1.7, 0.65);
    H.box(s, "FF  a_q", "clock: gclk, no mux", 10.45, 1.75, 2.05, 1.4, true);
    H.box(s, "a, b", null, 6.85, 2.5, 1.2, 0.65);
    H.arrow(s, 8.05, 2.075, 8.4, 2.075); H.arrow(s, 10.1, 2.075, 10.45, 2.075); H.arrow(s, 8.05, 2.825, 10.45, 2.825);
    H.caption(s, "en = 0: the mux feeds a_q back", 0.85, 3.3, 5.6);
    H.caption(s, "en = 0: the clock stops, the flip-flop holds", 6.85, 3.3, 5.65);
    H.table(s, [["Baseline Sim", "NoCG", "CG"], ["Result", "PASS", "PASS"], ["checked / errors", "6 272 / 0", "6 272 / 0"]],
      { x: 0.85, y: 4.0, w: 11.65, colW: [3.0, 4.325, 4.325], boldFirstCol: true });
    H.msg(s, "Same test, same result: Sim cannot see the cost of clock gating; FPGA and ASIC can.");
  }

  {
    const s = H.slide("Example · What the Agent Added");
    H.table(s, [["", "NoCG", "CG"], ["Baseline checks", "6 272", "6 272"], ["After 2 rounds", "542 046 (×86)", "1 509 090 (×241)"], ["Still PASS", "yes", "yes"]],
      { x: 0.85, y: 1.35, w: 6.2, colW: [2.2, 2.0, 2.0], boldFirstCol: true });
    H.head(s, "Round 1: cases not checked before", 0.85, 3.3, 6.2);
    H.bullets(s, ["valid exactly 2 cycles after en", "edge values 7F/80, FF/00; large shifts", "back-to-back and idle en patterns; reset mid-run"],
      { x: 0.85, y: 3.7, w: 6.2, h: 2.3 });
    H.head(s, "Round 2: exhaustive", 7.35, 1.35, 5.15);
    H.bullets(s, ["ADD, SUB, SLT over all 256 × 256 operand pairs."], { x: 7.35, y: 1.75, w: 5.15, h: 1.2 });
    H.head(s, "CG only: glitch check", 7.35, 3.3, 5.15);
    H.bullets(s, ["Counts gated-clock edges every cycle: 1 when en = 1, 0 when idle.", "Tests directly that the ICG never makes a spurious pulse."],
      { x: 7.35, y: 3.7, w: 5.15, h: 2.3 });
    H.msg(s, "All 4 rounds accepted; on CG the agent added a clock-gating check without being asked.");
  }

  {
    const s = H.slide("Takeaways and Limits");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["CHIA splits one step over two containers and gates the rest of the flow on its result.",
      "The agent found real gaps in the testbench; Verilator confirmed every new check."], { x: 0.85, y: 1.7, w: 5.6, h: 4.2 });
    H.head(s, "Limits", 6.85, 1.3, 5.65);
    H.bullets(s, ["checked counts comparisons, not coverage.", "Bigger tests cost time and disk (waveform ≈ 90 MB).",
      "Two rounds, one run each; the agent is not deterministic."], { x: 6.85, y: 1.7, w: 5.65, h: 4.2 });
    H.msg(s, "Next, Part 2: the same ALU goes through Vivado to a bitstream.");
  }
  await H.save("1_Sim.pptx");
}

// ====================================================================================
// 2. FPGA
// ====================================================================================
async function fpgaDeck() {
  const H = deck("Part 2 · FPGA", "Part 2: FPGA with Vivado");
  H.cover("PART 2 · FPGA", "Step 2 of the Flow:", "FPGA with Vivado 2025.1",
    "Self-checking wrapper, bitstream on the vivado worker, then agent rounds under a strict rule");

  {
    const s = H.slide("Where FPGA Fits");
    H.flow(s, "FPGA", 1.35);
    H.head(s, "Self-checking wrapper (fpga_top)", 0.85, 2.55, 11.65);
    const y = 3.0;
    H.box(s, "LFSR", "random op, a, b, en", 0.85, y, 2.3, 1.0);
    H.arrow(s, 3.15, y + 0.5, 3.75, y + 0.5);
    H.box(s, "ALU", "same RTL as Sim", 3.75, y, 2.6, 1.0, true);
    H.arrow(s, 6.35, y + 0.5, 6.95, y + 0.5);
    H.box(s, "MISR", "signature of all results", 6.95, y, 2.5, 1.0);
    H.arrow(s, 9.45, y + 0.5, 10.05, y + 0.5);
    H.box(s, "4 LEDs", "PASS · FAIL · done", 10.05, y, 2.45, 1.0);
    H.bullets(s, ["Arty A7-100T, 100 MHz; Vivado 2025.1 in batch mode.", "Gate: bitstream written and WNS ≥ 0. No board yet: the flow ends at the bitstream."],
      { x: 0.85, y: 4.35, w: 11.65, h: 1.6 });
    H.msg(s, "The wrapper makes the ALU testable on a board with only clock, reset and 4 LEDs.");
  }

  {
    const s = H.slide("Running the FPGA Build on CHIA");
    H.hflow(s, [["read", "sources, pins"], ["synth", null], ["opt", null], ["place", null], ["route", null], ["reports", "timing, util, power"], ["bitstream", null]],
      1.4, 0.95, { g: 0.25, dark: [6] });
    H.caption(s, "build.tcl, run by one task on the vivado worker (≈ 64 s per demo)", 0.85, 2.45, 11.65);
    H.head(s, "Locked vs editable by the agent", 0.85, 2.95, 6.2);
    H.table(s, [["Part", "Agent"], ["ALU RTL, wrapper, pins, clock, checker", "locked"], ["build.tcl (Vivado options)", "may edit"],
      ["Source list, new FPGA-only .v files", "may edit"]], { x: 0.85, y: 3.35, w: 6.2, colW: [4.6, 1.6] });
    H.head(s, "Measured from the reports", 7.45, 2.95, 5.05);
    H.bullets(s, ["WNS (gate: ≥ 0)", "LUT + FF", "Dynamic power", "Vivado warnings"], { x: 7.45, y: 3.35, w: 5.05, h: 1.9 });
    H.cmd(s, "python run_demo.py NoCG baseline --stages sim,fpga", 0.85, 5.45, 7.4);
    H.msg(s, "One CHIA task builds the bitstream and reads WNS, LUT + FF, power and warnings.");
  }

  {
    const s = H.slide("Agent: Tune the FPGA Build");
    H.loop(s, 1.4, "build.tcl, FPGA-only files", "strict Pareto");
    H.head(s, "Why these files only", 0.85, 2.85, 5.6);
    H.bullets(s, ["The RTL is shared with ASIC; FPGA tricks stay in FPGA-only files.", "A functional check in xsim runs before the build."],
      { x: 0.85, y: 3.25, w: 5.6, h: 2.1 });
    H.head(s, "Gate and rule", 6.85, 2.85, 5.65);
    H.table(s, [["", "FPGA"], ["Gate", "xsim check PASS, bitstream, WNS ≥ 0"], ["Accept", "WNS, power, LUT+FF all no worse, one better"], ["Rounds", "up to 2"]],
      { x: 6.85, y: 3.25, w: 5.65, colW: [1.3, 4.35], boldFirstCol: true });
    H.cmd(s, "python run_demo.py NoCG agent --rounds 2", 0.85, 5.45, 7.0);
    H.msg(s, "Strict Pareto: a round is accepted only if no metric gets worse.");
  }

  {
    const s = H.slide("Example · Baseline: Clock Gating Hurts on FPGA");
    H.legend(s, 0.85, 1.3);
    const cw = 2.15;
    [["WNS (ns)", [4.579, 1.502], 6], ["Dyn. power (mW)", [2, 5], 6.5], ["LUT + FF", [277, 281], 340]].forEach(([t, v, mx], i) => {
      H.bars(s, [{ name: "NoCG", labels: [""], values: [v[0]] }, { name: "CG", labels: [""], values: [v[1]] }],
        { x: 0.85 + i * (cw + 0.05), y: 1.75, w: cw, h: 4.2, title: t, min: 0, max: mx, noCat: true });
    });
    H.head(s, "Why CG is worse here", 7.75, 1.35, 4.75);
    H.bullets(s, ["FPGA flip-flops expect clock enables, not clocks gated by logic.", "The latch + LUT on the clock leaves half a cycle: WNS drops ≈ 3 ns.",
      "Extra logic on the clock: more power, 3 more warnings.", "Still passes the gate, so the agent can try to fix it."], { x: 7.75, y: 1.75, w: 4.75, h: 4.3 });
    H.msg(s, "Clock gating hurts on FPGA: WNS 4.58 → 1.50 ns, dynamic power 2 → 5 mW.");
  }

  {
    const s = H.slide("Example · Four Agent Rounds, None Accepted");
    H.table(s, [["Round", "What the agent did", "Measured", "Result"],
      ["NoCG r1", "Retiming, hand-mapped LUTs, timing options", "WNS +0.36 ns; power +3 mW; LUT+FF +25", "rejected"],
      ["NoCG r2", "One shared adder for ADD / SUB / SLT", "LUT+FF −15; WNS −0.37 ns", "rejected"],
      ["CG r1", "Gated clock turned into clock enable at synthesis", "WNS ×3; power −60 %; LUT+FF +3", "rejected"],
      ["CG r2", "FPGA-only ALU with clock enables + retiming", "WNS ×3; LUT+FF +31", "rejected"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.3, 4.9, 4.1, 1.35], boldFirstCol: true });
    H.bullets(s, ["Each round made at least one metric worse, so the baseline was kept.",
      "CG r1 fixed the real problem but costs 3 LUT+FF (+1.1 %); the rule was fixed before the run and kept."], { x: 0.85, y: 4.35, w: 11.65, h: 1.7 });
    H.msg(s, "The strict rule blocked a good trade-off (CG r1); choosing the rule is a design decision.");
  }

  {
    const s = H.slide("Takeaways and Limits");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["The same RTL builds to a bitstream on the vivado worker, with a built-in self-check.",
      "The agent reads real Vivado reports and proposes concrete fixes, checked in xsim first."], { x: 0.85, y: 1.7, w: 5.6, h: 4.2 });
    H.head(s, "Limits", 6.85, 1.3, 5.65);
    H.bullets(s, ["Strict Pareto rejects good trade-offs.", "Power is Vivado's estimate without simulation activity.", "No board yet."],
      { x: 6.85, y: 1.7, w: 5.65, h: 4.2 });
    H.msg(s, "Next, Part 3: the same RTL goes to a sky130 GDS, where clock gating pays off.");
  }
  await H.save("2_FPGA.pptx");
}

// ====================================================================================
// 3. ASIC
// ====================================================================================
async function asicDeck() {
  const H = deck("Part 3 · ASIC", "Part 3: ASIC with Yosys and OpenROAD");
  H.cover("PART 3 · ASIC", "Step 3 of the Flow:", "RTL to GDS with Yosys + OpenROAD",
    "OpenROAD-flow-scripts on sky130hd, six CHIA tasks, then the agent tunes config.mk");

  {
    const s = H.slide("Where ASIC Fits");
    H.flow(s, "ASIC", 1.35);
    H.head(s, "Six ORFS stages, one CHIA task each", 0.85, 2.55, 11.65);
    [["synth", "Yosys"], ["floorplan", "die, I/O"], ["place", "cells"], ["cts", "clock tree"], ["route", "wires"], ["finish", "GDS"]].forEach(([a, b], i) => {
      const x = 0.85 + i * 1.98;
      H.box(s, a, b, x, 3.0, 1.65, 0.9, i === 5);
      if (i < 5) H.arrow(s, x + 1.65, 3.45, x + 1.98, 3.45);
    });
    H.bullets(s, ["sky130hd, 10 ns clock (same 100 MHz as FPGA), ≈ 2.5 min per demo.", "Gate: GDS written, setup and hold slack ≥ 0, DRC = 0."],
      { x: 0.85, y: 4.3, w: 11.65, h: 1.6 });
    H.msg(s, "One task per stage: the timeline shows each stage, and a failure stops where it happened.");
  }

  {
    const s = H.slide("Running the ASIC Flow on CHIA");
    H.head(s, "How it runs", 0.85, 1.3, 5.3);
    H.bullets(s, ["Each stage is one ORFS make call on the asic worker.", "All results stay in the run folder, not in ORFS.",
      "Reads slack, cell and die area, power, DRC."], { x: 0.85, y: 1.7, w: 5.3, h: 3.4 });
    H.head(s, "Baseline config.mk", 6.55, 1.3, 5.95);
    H.table(s, [["Variable", "NoCG", "CG"], ["DESIGN_NAME", "NoCG_alu", "CG_alu"], ["Extra file", "-", "sky130 ICG cell"],
      ["CORE_UTILIZATION", "40", "40"], ["PLACE_DENSITY", "0.65", "0.65"]],
      { x: 6.55, y: 1.7, w: 5.95, colW: [2.55, 1.6, 1.8], monoCols: [0] });
    H.cmd(s, "python run_demo.py NoCG baseline", 0.85, 4.0, 7.0);
    H.msg(s, "config.mk says how to build; the RTL, PDK and clock stay fixed.");
  }

  {
    const s = H.slide("Agent: Tune config.mk");
    H.loop(s, 1.4, "config.mk only", "1 % rule");
    H.head(s, "Why config.mk only", 0.85, 2.85, 5.6);
    H.bullets(s, ["Design, sources and clock are what is measured, so they are locked.", "Locked variables are checked before the six stages run."],
      { x: 0.85, y: 3.25, w: 5.6, h: 2.1 });
    H.head(s, "Gate and rule", 6.85, 2.85, 5.65);
    H.table(s, [["", "ASIC"], ["Gate", "GDS, slack ≥ 0, DRC = 0"], ["Accept", "area, die, power ≤ 1 % worse; one > 1 % better"], ["Rounds", "up to 2"]],
      { x: 6.85, y: 3.25, w: 5.65, colW: [1.3, 4.35], boldFirstCol: true });
    H.cmd(s, "python run_demo.py NoCG agent --rounds 2", 0.85, 5.45, 7.0);
    H.msg(s, "The agent only changes how ORFS builds the same design.");
  }

  {
    const s = H.slide("Example · Baseline: Clock Gating Pays Off on ASIC");
    H.table(s, [["Baseline", "NoCG", "CG"], ["Cell area (µm²)", "3 078", "2 975"], ["Die area (µm²)", "7 534", "6 926"],
      ["Cells (ICG, clock buffers)", "356 (0, 9)", "306 (2, 18)"], ["Setup slack (ns)", "6.82", "6.96"], ["Power (mW)", "0.368", "0.324"], ["DRC", "0", "0"]],
      { x: 0.85, y: 1.35, w: 6.6, colW: [3.0, 1.8, 1.8], boldFirstCol: true });
    H.head(s, "Why CG is better here", 7.85, 1.35, 4.65);
    H.bullets(s, ["The ICG replaces the feedback mux of each register: 50 fewer cells.", "Cost: twice as many clock buffers.",
      "OpenROAD CTS handled the ICG cells correctly."], { x: 7.85, y: 1.75, w: 4.65, h: 4.3 });
    H.msg(s, "Clock gating pays off on ASIC: 50 fewer cells, power −12 %. The opposite of FPGA.");
  }

  {
    const s = H.slide("Example · What the Agent Changed");
    H.legend(s, 0.85, 1.3);
    H.bars(s, [{ name: "NoCG", labels: ["Die area", "Cell area", "Power"], values: [-38.4, -1.8, -1.8] },
      { name: "CG", labels: ["Die area", "Cell area", "Power"], values: [-20.5, -4.7, -6.8] }],
      { x: 0.85, y: 1.75, w: 6.3, h: 4.3, title: "Change vs baseline (%)", fmt: '0.0"%"', min: -45, max: 0 });
    H.head(s, "What the agent set", 7.55, 1.35, 4.95);
    H.bullets(s, ["Both: synthesis for area (ABC_AREA).", "NoCG: utilization 40 → 65 %, density 0.65 → 0.85.",
      "CG: utilization 40 → 50 %, fewer I/O buffers.", "All rounds: slack still positive, DRC 0."], { x: 7.55, y: 1.75, w: 4.95, h: 4.3 });
    H.msg(s, "Same RTL, tighter packing: die area −38 % (NoCG), −21 % (CG), timing still met.");
  }

  {
    const s = H.slide("Example · Layouts, Same Scale");
    const S = 2.45;
    [["NoCG_baseline", 0.85, 1, "NoCG baseline"], ["NoCG_agent", 3.85, Math.sqrt(4641.70 / 7534.24), "NoCG agent"],
     ["CG_baseline", 6.85, Math.sqrt(6925.57 / 7534.24), "CG baseline"], ["CG_agent", 9.85, Math.sqrt(5504.90 / 7534.24), "CG agent"]].forEach(([f, x, k, t]) => {
      const d = S * k, y = 1.5;
      s.addImage({ path: IMG + f + "_layout.png", x: x + (S - d) / 2, y: y + S - d, w: d, h: d });
      s.addText(t, { x: x - 0.1, y: y + S + 0.12, w: S + 0.2, h: 0.35, fontFace: F, fontSize: 15, bold: true, color: K.ink, align: "center", margin: 0, isTextBox: true });
    });
    [["7 534 µm²", 0.85], ["4 642 µm²", 3.85], ["6 926 µm²", 6.85], ["5 505 µm²", 9.85]].forEach(([t, x]) =>
      s.addText(t, { x: x - 0.1, y: 4.45, w: S + 0.2, h: 0.32, fontFace: F, fontSize: 14, color: K.ink, align: "center", margin: 0, isTextBox: true }));
    H.caption(s, "Picture side ∝ √(die area), relative to the NoCG baseline.", 0.85, 5.1, 11.65);
    H.msg(s, "The agent shrank the die by packing the same cells tighter, not by changing the design.");
  }

  {
    const s = H.slide("Takeaways and Limits");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["CHIA takes the same RTL from Sim to a sky130 GDS, one task per stage, with a gate.",
      "Tuning only config.mk, the agent cut die area by 20-38 % with timing still met."], { x: 0.85, y: 1.7, w: 5.6, h: 4.2 });
    H.head(s, "Limits", 6.85, 1.3, 5.65);
    H.bullets(s, ["Small design (≈ 300 cells), fixed RTL.", "Power estimated without simulation activity.", "Two rounds, one run each."],
      { x: 6.85, y: 1.7, w: 5.65, h: 4.2 });
    H.msg(s, "Clock gating helps on ASIC and hurts on FPGA; fixed RTL keeps the two branches apart.");
  }
  await H.save("3_ASIC.pptx");
}

(async () => {
  require("fs").mkdirSync(OUT, { recursive: true });
  await setupDeck(); await simDeck(); await fpgaDeck(); await asicDeck();
  console.log("written to", OUT);
})();
