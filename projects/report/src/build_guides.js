// build_guides.js - 4 deck báo cáo cho thầy hướng dẫn (tiếng Anh), nền trắng, Times New Roman, thang xám:
//   guides/0_Setup.pptx  cách đã setup CHIA (máy thật) + kế hoạch worker remote/cloud
//   guides/1_Sim.pptx, 2_FPGA.pptx, 3_ASIC.pptx  từng bước của demo ALU, ví dụ NoCG/CG
// Quy ước (phỏng vấn 23/11/2026): không dùng speaker notes, mọi thứ trình bày nằm trên slide; chữ ≥ 14 pt;
// mỗi slide một dòng kết luận đậm ở cuối; mỗi bước một dòng lệnh chính; chỉ giữ vài tên file quan trọng.
// Chạy: xem report/REPORT.md mục "Build" (cài pptxgenjs, build, fix_pptx.py, validate, render).
// Số liệu lấy từ REPORT.md và docs/demo-task.md (chạy thật 20–21/11/2026).
const pptxgen = require("pptxgenjs");
const path = require("path");
const OUT = process.argv[2] || path.join(__dirname, "..", "guides");
const IMG = path.join(__dirname, "..", "img") + "/";
// Style "Paper and Instruments" (theo Impeccable, chốt 28/11/2026): giấy trắng, mực đen, Calibri; mono chỉ cho lệnh/log/đường dẫn.
// Màu có nghĩa: vàng đậm = bước hiện tại (không làm màu chữ), xanh patina = xong / kết quả nhận, đỏ son = lỗi.
const F = "Calibri", M = "Courier New";
const K = { black: "111111", ink: "1F1F1F", mute: "5E5E5E", cap: "7A7A7A", line: "E3E3E3", edge: "8C8C8C", head: "FFFFFF",
  soft: "F0F0F0", grid: "E3E3E3", bar: "BDBDBD", patina: "1E6E6A", patinaPale: "E3F1F0", verm: "B33A1E", gold: "C98F00", goldEdge: "7A5600" };
const FS = 14; // cỡ chữ thường; nhỏ nhất 12 pt (bảng, lệnh, log)

function deck(name, titleText) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.title = titleText;
  pres.theme = { headFontFace: F, bodyFontFace: F };
  let n = 0;
  const T = (s, t, o) => s.addText(t, Object.assign({ fontFace: F, color: K.ink, margin: 0, isTextBox: true }, o));
  const H = {
    pres,
    slide(t) {
      const s = pres.addSlide(); n += 1;
      s.background = { color: "FFFFFF" };
      if (t) T(s, t, { x: 0.85, y: 0.5, w: 8.4, h: 0.75, fontSize: 30, bold: true, color: K.black, fit: "shrink" });
      T(s, name, { x: 0.85, y: 7.0, w: 6, h: 0.3, fontSize: 10, color: K.cap });
      T(s, String(n), { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontSize: 10, color: K.cap, align: "right" });
      return s;
    },
    // nhãn mục: mono, chữ hoa, giãn chữ
    head(s, t, x, y, w, color) {
      T(s, t.toUpperCase(), { x, y, w, h: 0.3, fontFace: M, fontSize: 12, bold: true, charSpacing: 2, color: color || K.mute });
    },
    dot(s, x, y, color) {
      s.addShape(pres.shapes.OVAL, { x, y, w: 0.13, h: 0.13, fill: { color }, line: { color } });
    },
    bullets(s, items, o) {
      const runs = items.map((it, i) => ({ text: it, options: { bullet: { indent: 14 }, paraSpaceAfter: 5, color: K.ink, breakLine: i < items.length - 1 } }));
      T(s, runs, { fontSize: o.fs || 15, valign: "top", x: o.x, y: o.y, w: o.w, h: o.h });
    },
    para(s, t, o) {
      T(s, t, { fontSize: o.fs || 15, color: o.color || K.ink, valign: o.valign || "top", x: o.x, y: o.y, w: o.w, h: o.h, italic: !!o.italic, bold: !!o.bold, align: o.align });
    },
    // dòng kết luận đậm cuối slide (không mũi tên, không gạch)
    msg(s, t) {
      T(s, t, { x: 0.85, y: 6.25, w: 11.65, h: 0.5, fontSize: 17, bold: true, color: K.black, valign: "middle" });
    },
    // khối lệnh: nền xám nhạt, mono; t là chuỗi hoặc mảng dòng
    cmd(s, t, x, y, w, fs) {
      const lines = Array.isArray(t) ? t : [t];
      const f = fs || 13, h = 0.14 + lines.length * f / 50;
      s.addText(lines.map((l, i) => ({ text: l, options: { breakLine: i < lines.length - 1 } })), {
        shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x, y, w, h, fontFace: M, fontSize: f, color: K.black,
        fill: { color: K.soft }, line: { color: K.line, width: 0.75 }, valign: "middle", margin: [4, 12, 4, 12] });
      return y + h;
    },
    result(s, t, x, y, w, h, label) {
      s.addText([{ text: (label || "Result") + "  ", options: { bold: true, color: K.patina } }, { text: t, options: { color: K.ink } }],
        { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, x, y, w, h: h || 0.5, fontFace: F, fontSize: FS, valign: "middle", margin: [4, 10, 4, 10], fill: { color: K.patinaPale }, line: { color: K.patinaPale } });
    },
    caption(s, t, x, y, w, h) {
      T(s, t, { x, y, w, h: h || 0.32, fontSize: 13, color: K.mute });
    },
    // trích nguyên văn log thật
    evidence(s, src, lines, x, y, w) {
      const h = 0.2 + 0.24 * lines.length;
      H.head(s, "From " + src, x, y, w);
      s.addText(lines.map((l, i) => ({ text: l, options: { breakLine: i < lines.length - 1 } })),
        { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x, y: y + 0.34, w, h, fontFace: M, fontSize: 12, color: K.black,
          fill: { color: K.soft }, line: { color: K.line, width: 0.75 }, valign: "middle", margin: [3, 10, 3, 10] });
    },
    box(s, a, b, x, y, w, h, dark, fs) {
      fs = fs || FS;
      const parts = [{ text: a, options: { bold: true, fontSize: fs, breakLine: !!b } }];
      if (b) parts.push({ text: b, options: { fontSize: 13, color: dark ? "E6E6E6" : K.mute } });
      s.addText(parts, { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, x, y, w, h, fontFace: F, align: "center", valign: "middle",
        color: dark ? "FFFFFF" : K.black, fill: { color: dark ? K.ink : "FFFFFF" }, line: { color: dark ? K.ink : K.edge, width: 0.75 }, margin: 3 });
    },
    frame(s, x, y, w, h) {
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { rectRadius: 0.1, x, y, w, h, fill: { color: K.soft }, line: { color: K.line, width: 0.75 } });
    },
    // nhãn trạng thái lặng (vd PLAN · NOT TESTED)
    tag(s, t, x, y) {
      s.addText(t, { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.04, x: x || 9.9, y: y || 0.62, w: 2.6, h: 0.36, fontFace: M, fontSize: 12, bold: true,
        charSpacing: 1, align: "center", valign: "middle", color: K.mute, fill: { color: K.soft }, line: { color: K.soft }, margin: 2 });
    },
    arrow(s, x1, y1, x2, y2) {
      const o = { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1), line: { color: K.edge, width: 1.25, endArrowType: "triangle" } };
      if (x2 < x1) o.flipH = true;
      if (y2 < y1) o.flipV = true;
      s.addShape(pres.shapes.LINE, o);
    },
    // bảng: chỉ kẻ ngang mảnh; o.mono: cột dùng font mono
    table(s, rows, o) {
      const hdr = !o.noHeader;
      const data = rows.map((r, i) => r.map((c, j) => ({ text: String(c), options: {
        bold: (i === 0 && hdr) || (o.boldFirstCol && j === 0), color: i === 0 && hdr ? K.mute : K.ink,
        fontFace: ((o.monoCols || []).includes(j) && (i > 0 || !hdr)) ? M : F,
        border: [{ type: "none" }, { type: "none" }, { type: "solid", pt: 0.75, color: K.line }, { type: "none" }] } })));
      s.addTable(data, { fontFace: F, fontSize: o.fs || FS, color: K.ink, margin: [3, 6, 3, 0], valign: "middle", autoPage: false,
        x: o.x, y: o.y, w: o.w, colW: o.colW, rowH: o.rowH });
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
    // dải tiến độ: chấm + số; bước hiện tại chấm vàng
    progress(s, n, total) {
      const sp = total > 7 ? 0.36 : 0.5, x0 = 12.5 - total * sp;
      for (let i = 0; i < total; i++) {
        const x = x0 + i * sp, cur = i + 1 === n;
        s.addShape(pres.shapes.OVAL, { x: x + sp / 2 - 0.08, y: 0.6, w: 0.16, h: 0.16, fill: { color: cur ? K.gold : K.line }, line: { color: cur ? K.goldEdge : K.line, width: 0.5 } });
        T(s, String(i + 1), { x, y: 0.82, w: sp, h: 0.26, fontFace: M, fontSize: 12, bold: cur, align: "center", color: cur ? K.black : K.cap });
      }
    },
    // slide một bước: Where, lệnh (cả bề ngang), What it does | Done when, tuỳ chọn Other distros / Output, If it fails
    step(o) {
      const s = H.slide(o.title);
      H.progress(s, o.n, o.total);
      const L = 0.85, W = 11.65, LW = 6.85, R = 8.2, RW = 4.3;
      H.head(s, "Where", L, 1.47, 1.2);
      T(s, o.where, { x: L + 1.05, y: 1.42, w: W - 1.05, h: 0.36, fontSize: 15 });
      let y = H.cmd(s, o.cmds, L, 1.9, W, o.cmdFs) + 0.32;
      const top = y;
      H.head(s, "What it does", L, y, LW);
      H.bullets(s, o.does, { x: L, y: y + 0.36, w: LW, h: 1.5, fs: 14 });
      // Done when
      const dh = 0.62 + 0.24 * o.done.log.length;
      s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: R, y: top - 0.05, w: RW, h: dh, rectRadius: 0.08, fill: { color: K.patinaPale }, line: { color: K.patinaPale } });
      H.dot(s, R + 0.18, top + 0.1, K.patina);
      H.head(s, "Done when", R + 0.4, top + 0.02, 3, K.patina);
      const dl = [{ text: o.done.say, options: { fontFace: F, fontSize: 14, color: K.ink, breakLine: o.done.log.length > 0 } }];
      o.done.log.forEach((l, i) => dl.push({ text: l, options: { fontFace: M, fontSize: 12, color: K.black, breakLine: i < o.done.log.length - 1 } }));
      T(s, dl, { x: R + 0.18, y: top + 0.32, w: RW - 0.3, h: dh - 0.42, valign: "top" });
      let ry = top - 0.05 + dh + 0.25;
      if (o.alt) {
        H.head(s, o.altLabel || "Other distros · not tested", R, ry, RW);
        H.table(s, o.alt, { x: R, y: ry + 0.32, w: RW, colW: [1.0, RW - 1.0], noHeader: true, boldFirstCol: true, monoCols: [1], fs: 12 });
      } else if (o.out) {
        H.head(s, "Output", R, ry, RW);
        T(s, o.out.map((l, i) => ({ text: l, options: { breakLine: i < o.out.length - 1 } })), { x: R, y: ry + 0.34, w: RW, h: 0.24 * o.out.length + 0.1, fontFace: M, fontSize: 12, valign: "top" });
      }
      if (o.fails) {
        const fy = 6.62 - 0.31 * o.fails.length;
        H.dot(s, L, fy - 0.36, K.verm);
        H.head(s, "If it fails", L + 0.22, fy - 0.44, 4, K.verm);
        H.table(s, o.fails, { x: L, y: fy - 0.1, w: W, colW: [4.3, W - 4.3], noHeader: true, boldFirstCol: true, fs: 12 });
      }
      return s;
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
    // biểu đồ cột: xám + một màu nhấn (patina)
    bars(s, series, o) {
      s.addChart(pres.charts.BAR, series, {
        x: o.x, y: o.y, w: o.w, h: o.h, barDir: o.dir || "col", barGrouping: "clustered", barGapWidthPct: 70,
        chartColors: o.colors || [K.bar, K.patina], showTitle: !!o.title, title: o.title, titleFontFace: F, titleFontSize: 14, titleColor: K.mute,
        showValue: true, dataLabelPosition: o.labelPos || "outEnd", dataLabelFontFace: F, dataLabelFontSize: 13, dataLabelColor: K.black,
        dataLabelFormatCode: o.fmt || "General",
        catAxisLabelFontFace: F, catAxisLabelFontSize: FS, catAxisLabelColor: K.ink, catAxisLineShow: true, catAxisLineColor: K.edge,
        valAxisHidden: true, catAxisHidden: !!o.noCat, valGridLine: { style: "none" }, catGridLine: { style: "none" },
        valAxisMinVal: o.min, valAxisMaxVal: o.max,
        showLegend: !!o.legend, legendPos: "b", legendFontFace: F, legendFontSize: 13, legendColor: K.ink,
      });
    },
    legend(s, x, y) {
      [["NoCG", K.bar], ["CG", K.patina]].forEach(([t, c], i) => {
        s.addShape(pres.shapes.RECTANGLE, { x: x + i * 1.5, y: y + 0.08, w: 0.22, h: 0.22, fill: { color: c }, line: { color: c } });
        T(s, t, { x: x + i * 1.5 + 0.32, y, w: 1.0, h: 0.38, fontSize: FS, valign: "middle" });
      });
    },
    // bìa: không eyebrow; tiêu đề + phụ đề + người trình bày
    cover(kicker, t1, t2, sub) {
      const s = pres.addSlide(); n += 1;
      s.background = { color: "FFFFFF" };
      T(s, [{ text: t1, options: { breakLine: true } }, { text: t2 }], { x: 0.85, y: 1.9, w: 11.6, h: 1.6, fontSize: 36, bold: true, color: K.black, valign: "bottom" });
      T(s, sub, { x: 0.85, y: 3.65, w: 11.6, h: 0.45, fontSize: 18, color: K.mute });
      s.addShape(pres.shapes.OVAL, { x: 0.85, y: 4.55, w: 0.16, h: 0.16, fill: { color: K.gold }, line: { color: K.goldEdge, width: 0.5 } });
      T(s, [{ text: "Advisor: MSc. Phạm Thế Hùng", options: { breakLine: true } }, { text: "Student: Phan Thành Nhân" }],
        { x: 0.85, y: 4.85, w: 6, h: 0.75, fontSize: 16, color: K.ink });
      T(s, kicker + "  ·  Report series: 0 Setup, 1 Sim, 2 FPGA, 3 ASIC", { x: 0.85, y: 6.85, w: 10, h: 0.3, fontSize: 12, color: K.cap });
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
  const H = deck("Part 0 · Setup", "Part 0: Setting Up CHIA, Step by Step");
  H.cover("Part 0 · Setup", "Setting Up CHIA,", "Step by Step",
    "From a fresh Linux machine to a working cluster of tool containers; tested on Bazzite-DX");
  const TOTAL = 10;

  // Cách CHIA chạy
  {
    const s = H.slide("How CHIA Runs Work");
    H.box(s, "Head (your PC)", "Ray head · driver script", 3.9, 1.4, 5.5, 0.85, true);
    [["chipyard", "Chisel, Verilator build"], ["verilator", "run simulations"], ["vivado", "FPGA build"], ["asic", "Yosys + OpenROAD"], ["claude", "LLM agent"]]
      .forEach(([a, b], i) => {
        const x = 0.85 + i * 2.37;
        H.box(s, a + " worker", b, x, 2.8, 2.15, 0.95);
        H.arrow(s, 6.65, 2.25, x + 1.075, 2.8);
      });
    H.caption(s, "Each worker is one Docker container with one set of tools; here all of them run on the head machine.", 0.85, 3.9, 11.65);
    H.table(s, [["Term", "Meaning"],
      ["Worker", "Container with one tool set; it offers a resource name, e.g. vivado"],
      ["Task", "Python function marked with the resource it needs; Ray runs it on a matching worker"],
      ["Gate", "Check in the driver on a task's result; the next step starts only if it passes"]],
      { x: 0.85, y: 4.4, w: 11.65, colW: [1.6, 10.05], boldFirstCol: true });
  }

  // Ví dụ một task
  {
    const s = H.slide("Example: One Simulation Task Through CHIA");
    H.box(s, "Head: driver script", "calls the run task, waits", 0.85, 1.45, 3.2, 1.1, true);
    H.box(s, "Ray scheduler", "who offers verilator_run?", 5.05, 1.45, 3.0, 1.1);
    H.box(s, "verilator worker", "runs the simulation", 9.05, 1.45, 3.45, 1.1);
    H.box(s, "Gate in the driver", "RESULT: PASS -> next task", 0.85, 3.4, 3.2, 1.0);
    H.box(s, "Shared run folder", "binary, log, waveform", 9.05, 3.4, 3.45, 1.0);
    H.arrow(s, 4.05, 2.0, 5.05, 2.0); H.arrow(s, 8.05, 2.0, 9.05, 2.0);
    H.arrow(s, 10.775, 2.55, 10.775, 3.4); H.arrow(s, 9.05, 2.55, 4.05, 3.4);
    H.arrow(s, 2.45, 2.55, 2.45, 3.4);
    [["1", 4.3, 1.6], ["2", 8.3, 1.6], ["3", 10.9, 2.8], ["4  result via get()", 5.6, 3.05], ["5", 2.6, 2.8]].forEach(([t, x, y]) =>
      H.para(s, t, { x, y, w: t.length > 2 ? 2.6 : 0.4, h: 0.32, fs: 14, bold: true, color: K.mute }));
    H.table(s, [
      ["1", "The driver calls the run task, which needs the resource verilator_run"],
      ["2", "Ray sends the task to the container that offers it"],
      ["3", "The container runs the binary in the shared run folder and writes the log there"],
      ["4", "The result (PASS / FAIL, number of checks) comes back to the driver"],
      ["5", "The driver checks the gate; only on PASS does the next task start"]],
      { x: 0.85, y: 4.65, w: 11.65, colW: [0.5, 11.15], boldFirstCol: true, noHeader: true });
  }

  // Yêu cầu
  {
    const s = H.slide("Before You Start: Prerequisites");
    H.head(s, "Machine", 0.85, 1.45, 5.6);
    H.table(s, [["CPU", "x86_64; tested with 20 cores"], ["RAM", "32 GB recommended; 23 GB worked"], ["Disk", "≥ 150 GB free (Chipyard image and build)"],
      ["OS", "Linux with systemd; tested on Bazzite-DX (Fedora Atomic)"], ["Network", "access to GitHub, conda-forge, ghcr.io"]],
      { x: 0.85, y: 1.8, w: 5.6, colW: [1.2, 4.4], noHeader: true, boldFirstCol: true });
    H.head(s, "Installed by you, outside these steps", 6.9, 1.45, 5.6);
    H.table(s, [["Vivado 2025.1", "at ~/tools/Xilinx/2025.1 (AMD installer; free edition covers Artix-7 100T); only for FPGA"],
      ["Claude Code", "logged in once on the host (~/.claude); only for the agent"], ["git, curl, unzip", "from the distro"]],
      { x: 6.9, y: 1.8, w: 5.6, colW: [1.6, 4.0], noHeader: true, boldFirstCol: true });
    H.head(s, "Versions that must match", 0.85, 4.35, 11.65);
    H.table(s, [["Python 3.10.19", "Ray 2.54.0", "chialoops 2.0.1", "Chipyard 1.15.0", "Verilator 5.022", "Yosys 0.68 + ORFS"]],
      { x: 0.85, y: 4.7, w: 11.65, colW: [1.95, 1.75, 1.95, 2.0, 1.9, 2.1], noHeader: true, monoCols: [0, 1, 2, 3, 4, 5], fs: 13 });
    H.msg(s, "Ray checks Python down to the patch level: the head must run 3.10.19, the same as every worker image.");
  }

  // Lộ trình
  {
    const s = H.slide("The Ten Steps");
    H.table(s, [["Step", "What", "Time", "Done when"],
      ["1", "Docker without sudo", "10 min", "docker run hello-world works"],
      ["2", "SSH to itself, linger", "5 min", "passwordless ssh to own IP"],
      ["3", "conda env + CHIA", "10 min", "Python 3.10.19, ray 2.54.0"],
      ["4", "Unpack project, check host", "2 min", "All required checks passed."],
      ["5", "Build Chipyard 1.15.0", "several hours", "VERIFY_OK"],
      ["6", "SoC config (base for projects)", "5 min", "MIXIN_OK"],
      ["7", "ORFS image (ASIC tools)", "20 min", "VERIFY_OK"],
      ["8", "Bring up the block cluster", "5 min", "five worker containers running"],
      ["9", "Smoke test through CHIA", "15-30 min", "KẾT QUẢ: OK"],
      ["10", "Shut down", "1 min", "no container left"]],
      { x: 0.85, y: 1.4, w: 11.65, colW: [0.8, 4.2, 1.8, 4.85], boldFirstCol: true, monoCols: [3], fs: 13 });
    H.caption(s, "Steps 1-4 prepare the host and differ by distro; steps 5-10 run the same scripts on any Linux.", 0.85, 5.75, 11.65);
  }

  H.step({ n: 1, total: TOTAL, title: "Step 1 · Docker Without sudo",
    where: "host terminal; needs one reboot or re-login",
    cmds: ["ujust dx-group                # Bazzite-DX: adds you to the docker group", "systemctl reboot", "docker run --rm hello-world"],
    does: ["CHIA calls docker for every worker, without sudo.", "On plain Bazzite, first rebase to bazzite-dx (docs/setup-bazzite.md, part 1)."],
    done: { say: "The test container prints:", log: ["Hello from Docker!"] },
    alt: [["Ubuntu", "sudo apt install docker.io"], ["", "sudo usermod -aG docker $USER"], ["Fedora", "sudo dnf install moby-engine"], ["", "sudo usermod -aG docker $USER"]],
    fails: [["permission denied ... docker.sock", "you are not in the docker group yet: log out and in again"]] });

  H.step({ n: 2, total: TOTAL, title: "Step 2 · SSH to Itself and Linger",
    where: "host terminal",
    cmds: ["sudo systemctl enable --now sshd", "ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 && cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys",
      "loginctl enable-linger $USER", "ssh $USER@$(hostname -I | awk '{print $1}') 'bash --login -c \"docker ps\"'"],
    does: ["chia up logs into the head over SSH, even on one machine.", "Linger keeps the Ray head alive after that SSH session closes."],
    done: { say: "The last command lists containers without asking for a password.", log: [] },
    alt: [["Ubuntu", "sudo apt install openssh-server"], ["", "sudo systemctl enable --now ssh"]],
    fails: [["worker: Failed to connect to GCS", "linger is off; run loginctl enable-linger $USER"], ["asks for a password", "key missing from ~/.ssh/authorized_keys or ssh-agent"]] });

  H.step({ n: 3, total: TOTAL, title: "Step 3 · Python Environment and CHIA",
    where: "host terminal; same on every distro",
    cmds: ["curl -LO https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-x86_64.sh",
      "bash Miniforge3-Linux-x86_64.sh -b -p ~/miniforge3 && ~/miniforge3/bin/conda init bash", "conda create -n chia_env python=3.10.19 -y",
      "conda env config vars set PYTHONNOUSERSITE=1 -n chia_env", "conda activate chia_env && pip install chialoops==2.0.1", "python -V; ray --version"],
    cmdFs: 12,
    does: ["One env for CHIA and Ray, pinned to the workers' Python.", "PYTHONNOUSERSITE stops pip from mixing in ~/.local packages."],
    done: { say: "The last command prints:", log: ["Python 3.10.19", "ray, version 2.54.0"] },
    fails: [["conda: command not found", "open a new terminal after conda init"]] });

  H.step({ n: 4, total: TOTAL, title: "Step 4 · Unpack and Check the Host",
    where: "host terminal; CHIA-workflow.zip is the archive you were given",
    cmds: ["unzip ~/Downloads/CHIA-workflow.zip -d ~          # must give ~/CHIA-workflow/scripts/...",
      "cd ~/CHIA-workflow && chmod +x scripts/*.sh", "scripts/check-bazzite-env.sh"],
    does: ["Puts the scripts, configs and demo projects at ~/CHIA-workflow; later steps assume this path.",
      "The check covers docker, sshd, passwordless SSH, linger, chia_env, disk and RAM; on another distro the OS line is only a WARN."],
    done: { say: "The script ends with:", log: ["All required checks passed."] },
    fails: [["unzip made ~/CHIA-workflow-<something>", "mv ~/CHIA-workflow-* ~/CHIA-workflow"],
      ["Permission denied on a script", "the archive lost the run bit: chmod +x scripts/*.sh"],
      ["[FAIL] linger off / sshd not active", "go back to step 2"], ["[FAIL] Python must be 3.10.x", "go back to step 3"]] });

  H.step({ n: 5, total: TOTAL, title: "Step 5 · Build Chipyard 1.15.0",
    where: "host terminal; runs in a temporary container; keep the machine awake",
    cmds: ["cd ~/CHIA-workflow && scripts/setup-chipyard.sh --verify"],
    does: ["Clones Chipyard to ~/tools/chipyard, pins tag 1.15.0, runs its 11 setup steps.", "Log: ~/tools/chipyard/setup.log. Run only one build at a time."],
    done: { say: "After several hours the script prints:", log: ["VERIFY_OK", "== Xong"] },
    out: ["~/tools/chipyard/", "  .conda-env/riscv-tools/", "  setup.log"],
    fails: [["stops at step 8-9 (FireMarshal, mount -o loop)", "skip 8-9, then rerun them with --privileged (command printed by the script)"],
      ["CondaHTTPError at step 1", "network; run the two commands the script prints"],
      ["any other step", "the script prints the command to resume from that step"]] });

  H.step({ n: 6, total: TOTAL, title: "Step 6 · Install the Base SoC Config",
    where: "host terminal",
    cmds: ["cd ~/CHIA-workflow && scripts/install-chipyard-config.sh"],
    does: ["Adds MinimalSoCConfig (1 Rocket core + 128 KiB L2) to Chipyard.", "Base config for SoC projects; the ALU demo does not need it."],
    done: { say: "The script prints:", log: ["MIXIN_OK"] } });

  H.step({ n: 7, total: TOTAL, title: "Step 7 · ASIC Tools (ORFS Image)",
    where: "host terminal",
    cmds: ["cd ~/CHIA-workflow && docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker", "scripts/setup-orfs.sh --verify"],
    does: ["Builds the asic worker image: Yosys + OpenROAD with Ray inside.", "Copies OpenROAD-flow-scripts to ~/tools and runs a small design to GDS."],
    done: { say: "The second command prints:", log: ["VERIFY_OK"] },
    out: ["~/tools/OpenROAD-flow-scripts/  (≈ 1.4 GB)"] });

  // Công cụ trên host, mount vào worker
  {
    const s = H.slide("How the Tools Reach the Workers");
    H.frame(s, 0.85, 1.4, 4.6, 4.15);
    H.head(s, "Host  ~/tools/", 1.05, 1.55, 4.2);
    [["chipyard/", "Chipyard 1.15.0"], ["Xilinx/2025.1/", "Vivado, read-only"], ["OpenROAD-flow-scripts/", "ORFS"]]
      .forEach(([a, b], i) => H.box(s, a, b, 1.05, 1.95 + i * 0.85, 4.2, 0.72));
    H.box(s, "Shared run folder", "~/chia_demo, read-write", 1.05, 4.65, 4.2, 0.72, true);
    H.arrow(s, 5.45, 3.45, 6.75, 3.45);
    H.para(s, "mounted at the same path", { x: 5.4, y: 2.65, w: 1.45, h: 0.75, fs: 13, align: "center", color: K.mute });
    [["chipyard worker", "mounts chipyard/"], ["verilator worker", "own image only"], ["vivado worker", "mounts chipyard/, Xilinx/"],
     ["asic worker", "mounts ORFS"], ["claude worker", "chipyard/ read-only"]]
      .forEach(([a, b], i) => H.box(s, a, b, 6.75, 1.4 + i * 0.84, 5.75, 0.72));
    H.caption(s, "Every worker also mounts the shared run folder. Paths are the real ones (on Bazzite /var/home/...).", 0.85, 5.75, 11.65);
  }

  H.step({ n: 8, total: TOTAL, title: "Step 8 · Bring Up the Block Cluster",
    where: "host terminal, inside chia_env",
    cmds: ["conda activate chia_env && cd ~/CHIA-workflow", "python scripts/chia-up-blocks.py --fpga --asic", "docker ps --format '{{.Names}}'"],
    does: ["Checks the tools, writes cluster.gen.yaml with only the chosen blocks, runs chia up.",
      "Options: --fpga, --asic, --no-claude; WORK_DIR=... moves the run folder; ASIC_MEM=20g."],
    done: { say: "docker ps lists one container per block:", log: ["chia-chipyard-<user>-0", "chia-verilator-<user>-0", "... vivado, asic, claude"] },
    fails: [["script stops before chia up", "it names the missing tool: Chipyard env.sh, Vivado settings64.sh, chia-orfs image, Claude login"],
      ["chia: command not found", "conda activate chia_env (the base env has no chia)"]] });

  H.step({ n: 9, total: TOTAL, title: "Step 9 · Smoke Test Through CHIA",
    where: "host terminal, inside chia_env, cluster up",
    cmds: ["cd ~/CHIA-workflow/examples && chia job submit --working-dir . -- python blocks_smoke.py --clean"],
    does: ["One job touches every block: builds RocketConfig on chipyard, finds Vivado, runs Yosys.", "The first build downloads the JVM cache, so it is slow."],
    done: { say: "The job log shows:", log: ["SUCCESS=True", "vivado v2025.1", "KẾT QUẢ: OK"] },
    fails: [["Ctrl+C does not stop the job", "chia job stop <id>"], ["build hangs, java at 0 % CPU", "dead connection to Maven; the JVM cache mount and timeouts fix it, rerun"]] });

  H.step({ n: 10, total: TOTAL, title: "Step 10 · Shut Down",
    where: "host terminal, in ~/CHIA-workflow",
    cmds: ["python scripts/chia-up-blocks.py --down", "docker ps"],
    does: ["Stops the Ray head and removes every worker container.", "Tools and the run folder stay on the host."],
    done: { say: "docker ps shows no chia-* container.", log: [] },
    fails: [["containers still running after chia down", "call the script with --down, not chia down directly (it needs THIS_MACHINE)"]] });

  // Remote: kiến trúc
  {
    const s = H.slide("Remote Workers: Architecture");
    H.tag(s, "PLAN · NOT TESTED");
    H.box(s, "HEAD (your PC)", "Ray head · driver · local workers", 0.85, 1.45, 4.2, 1.05, true);
    H.box(s, "REMOTE (lab PC or cloud VM)", "SSH · Docker · worker containers", 8.3, 1.45, 4.2, 1.05);
    H.arrow(s, 5.05, 1.75, 8.3, 1.75);
    H.arrow(s, 8.3, 2.2, 5.05, 2.2);
    H.para(s, "SSH: start containers", { x: 5.1, y: 1.33, w: 3.15, h: 0.35, fs: 13, align: "center", color: K.mute });
    H.para(s, "Ray ports, both ways", { x: 5.1, y: 2.25, w: 3.15, h: 0.35, fs: 13, align: "center", color: K.mute });
    H.head(s, "The remote machine needs", 0.85, 3.0, 5.5);
    H.table(s, [["Software", "SSH server, Docker, rsync"], ["Not needed", "conda, CHIA"],
      ["Hardware", "x86_64, ≥ 16 GB RAM, ≥ 120 GB disk"]], { x: 0.85, y: 3.35, w: 5.5, colW: [1.6, 3.9], noHeader: true, boldFirstCol: true });
    H.head(s, "What changes", 6.85, 3.0, 5.65);
    H.bullets(s, ["Job code: nothing; Ray places tasks by resource.",
      "Firewall: each machine trusts only the other's IP.",
      "Other network or cloud VM: connect through Tailscale."], { x: 6.85, y: 3.35, w: 5.65, h: 2.6 });
  }

  // Remote: các bước
  {
    const s = H.slide("Remote Workers: Planned Steps");
    H.tag(s, "PLAN · NOT TESTED");
    H.table(s, [["Step", "Where", "Command"],
      ["1. Prepare the remote", "head, then remote", "scp scripts/prepare-remote-worker.sh <user>@<remote>:  then  bash prepare-remote-worker.sh <head-ip>"],
      ["2. Open the firewall", "both", "sudo firewall-cmd --permanent --zone=trusted --add-source=<other-ip> (ufw: sudo ufw allow from <ip>)"],
      ["3. SSH without password", "head", "ssh-copy-id <user>@<remote>; ssh-add"],
      ["4. Check the remote", "head", "bash scripts/check-remote-worker.sh <user>@<remote> <head-ip>"],
      ["5. Bring up", "head", "chia up configs/cluster-lan.yaml   (add workers later: chia up --add ...)"]],
      { x: 0.85, y: 1.45, w: 11.65, colW: [2.4, 1.7, 7.55], boldFirstCol: true, monoCols: [2], fs: 12 });
    H.table(s, [["", "Lab PC on the same LAN", "Cloud VM / other network"],
      ["Connection", "LAN IPs", "Tailscale on both machines"],
      ["To verify", "needs a second PC", "needs a VM with Docker, ≥ 120 GB"]],
      { x: 0.85, y: 4.4, w: 11.65, colW: [1.9, 4.6, 5.15], boldFirstCol: true });
    H.caption(s, "Details: docs/usage-remote.md", 0.85, 5.75, 11.65);
  }

  // Mẫu flow dùng chung
  {
    const s = H.slide("After Setup: A New Design Needs Three Files");
    H.hflow(s, [["RTL", "the design"], ["Testbench", "self-checking"], ["project.py", "≈ 20 lines"]], 1.45, 0.9, { w: 6.2, dark: [2] });
    H.arrow(s, 7.05, 1.9, 7.55, 1.9);
    H.box(s, "flow/ generates", "sim script, config.mk, SDC, Vivado build.tcl, SAIF and GLS scripts", 7.55, 1.45, 4.95, 0.9);
    H.head(s, "5 testbench rules", 0.85, 2.75, 6.2);
    H.bullets(s, ["Prints its own PASS / FAIL (RESULT: line or a regex).", "Reads only DUT ports, so it also runs on netlists (GLS).",
      "Types and constants in a separate .svh file.", "Reset ≥ 100 ns (Xilinx global reset).", "One clock; SV that Verilator 5.022 and Yosys 0.68 read."],
      { x: 0.85, y: 3.1, w: 6.2, h: 2.6, fs: 14 });
    H.head(s, "4 commands", 7.3, 2.75, 5.2);
    H.cmd(s, ["python flow/new.py projects/<p> --top <mod> ...", "python flow/run.py projects/<p> baseline",
      "python flow/run.py projects/<p> agent --rounds 2", "python flow/clean_runs.py --only <p>"], 7.3, 3.1, 5.2, 12);
    H.caption(s, "Parts 1-3 walk through these commands for the ALU demo.", 7.3, 4.4, 5.2, 0.6);
  }

  // ASCON chứng minh mẫu flow
  {
    const s = H.slide("Proof: ASCON Through the Same Flow");
    H.head(s, "What was added", 0.85, 1.45, 5.6);
    H.bullets(s, ["Ascon-AEAD128 decrypt + tag check, 32-bit bus.", "RTL (2 files), testbench, project.py (≈ 20 lines).",
      "No change to flow/: same driver, tasks, gates and agent rules as the ALU."], { x: 0.85, y: 1.8, w: 5.6, h: 1.8 });
    H.head(s, "Testbench fixes it needed", 0.85, 3.7, 5.6);
    H.bullets(s, ["Read fsm_q inside the DUT -> wait for auth_valid.", "Package split into ascon_pkg.svh.", "Key loaded at 45 ns -> reset ≥ 100 ns (GSR)."],
      { x: 0.85, y: 4.05, w: 5.6, h: 1.3 });
    H.head(s, "Result", 6.85, 1.45, 5.65);
    H.table(s, [["Run", "Result"], ["Baseline, Sim -> FPGA -> ASIC", "OK, 13.3 min"], ["Baseline again", "identical numbers"], ["Agent, 1 round per step", "OK; Sim accepted"]],
      { x: 6.85, y: 1.8, w: 5.65, colW: [3.35, 2.3], boldFirstCol: true });
    H.cmd(s, "python flow/run.py projects/ascon-core baseline", 6.85, 3.7, 5.65);
  }

  // Trạng thái
  {
    const s = H.slide("Setup Status");
    H.table(s, [["Item", "Status"],
      ["Steps 1-10 on Bazzite-DX, reinstalled from scratch", "verified 14/11/2026"],
      ["Clean shutdown with --down", "verified 15/11/2026"],
      ["Shared run folder between blocks", "verified 20/11/2026"],
      ["ALU demo: Sim, FPGA, ASIC, baseline and agent", "verified 20-26/11/2026"],
      ["Shared flow/ with a second design (ASCON)", "verified 26/11/2026"],
      ["Steps 1-2 on Ubuntu / Fedora", "not tested"],
      ["Remote worker on a LAN, cloud VM through Tailscale", "written, not tested"]],
      { x: 0.85, y: 1.45, w: 11.65, colW: [7.65, 4.0] });
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
    H.bullets(s, ["RESULT line: PASS / FAIL and errors.", "Coverage: functional bins (fixed file) + line, toggle, branch.", "Lint (verilator -Wall), waveform."],
      { x: 6.85, y: 2.95, w: 5.65, h: 3.0 });
    H.msg(s, "Sim is the first gate: FPGA and ASIC start only after lint is clean and the testbench passes.");
  }

  {
    const s = H.slide("Running the Baseline Sim on CHIA");
    H.box(s, "Driver", "runs on the head", 0.85, 1.4, 2.6, 0.95, true);
    H.box(s, "Build task", "chipyard worker · Verilator", 4.2, 1.4, 3.3, 0.95);
    H.box(s, "Run task", "verilator worker", 4.2, 2.85, 3.3, 0.95);
    H.box(s, "Shared run folder", "binary, log, waveform", 8.4, 2.1, 4.1, 0.95);
    H.box(s, "Gate", "lint + PASS ?", 4.2, 4.3, 3.3, 0.8);
    H.box(s, "FPGA step", "Part 2", 8.4, 4.3, 4.1, 0.8);
    H.arrow(s, 3.45, 1.875, 4.2, 1.875); H.arrow(s, 5.85, 2.35, 5.85, 2.85); H.arrow(s, 5.85, 3.8, 5.85, 4.3);
    H.arrow(s, 7.5, 1.875, 8.4, 2.4); H.arrow(s, 8.4, 2.8, 7.5, 3.325); H.arrow(s, 7.5, 4.7, 8.4, 4.7);
    H.para(s, "writes", { x: 7.45, y: 1.6, w: 1.0, h: 0.32, fs: 14, align: "center" });
    H.para(s, "reads", { x: 7.45, y: 3.45, w: 1.0, h: 0.32, fs: 14, align: "center" });
    H.para(s, "pass", { x: 7.5, y: 4.3, w: 0.9, h: 0.32, fs: 14, align: "center" });
    H.result(s, "ALU NoCG and CG: both PASS", 0.85, 2.85, 2.95, 1.25);
    H.cmd(s, "python flow/run.py projects/demo-alu/NoCG baseline", 0.85, 5.45, 7.0);
    H.msg(s, "Build and run are split over two containers that share one run folder.");
  }

  {
    const s = H.slide("Agent: Improve the Testbench");
    H.loop(s, 1.4, "testbench only", "coverage must rise");
    H.head(s, "Rules for the agent", 0.85, 2.85, 5.6);
    H.bullets(s, ["Edits only the testbench; the RTL is the reference.", "Edits to other files are undone before measuring.",
      "Each round works in its own copy; prompt, diff and summary are kept."], { x: 0.85, y: 3.25, w: 5.6, h: 2.1 });
    H.head(s, "Gate and rule", 6.85, 2.85, 5.65);
    H.table(s, [["", "Sim"], ["Gate", "lint + testbench PASS"], ["Accept", "no coverage drops, more bins covered"], ["Rounds", "up to 2"]],
      { x: 6.85, y: 3.25, w: 5.65, colW: [1.3, 4.35], boldFirstCol: true });
    H.cmd(s, "python flow/run.py projects/demo-alu/NoCG agent --rounds 2", 0.85, 5.45, 8.4);
    H.msg(s, "More comparisons alone do not count: a round must cover a case not covered before.");
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
    H.table(s, [["Baseline Sim", "ALU NoCG", "ALU CG"], ["Result · lint", "PASS · 0 warnings", "PASS · 0 warnings"],
      ["Functional bins", "51/55", "51/57"], ["Line · branch", "15/16 · 8/8", "14/15 · 8/10"]],
      { x: 0.85, y: 3.85, w: 11.65, colW: [3.65, 4.0, 4.0], boldFirstCol: true });
    H.evidence(s, "the Sim log, NoCG baseline", ["RESULT: PASS dut=NoCG_alu seed=1 cycles=20249 ... checked=6272 errors=0"], 0.85, 5.35, 11.65);
    H.msg(s, "Same test, same result: Sim cannot see the cost of clock gating; FPGA and ASIC can.");
  }

  {
    const s = H.slide("Example · What the Agent Added");
    H.table(s, [["Round 1", "Baseline", "Agent"], ["ALU NoCG: bins", "51/55", "55/55"], ["ALU CG: bins · branch", "51/57 · 8/10", "57/57 · 10/10"],
      ["ALU comparisons", "6 272", "6 277 / 6 305"]],
      { x: 0.85, y: 1.35, w: 6.6, colW: [2.8, 1.9, 1.9], boldFirstCol: true });
    H.head(s, "What it added", 7.85, 1.35, 4.65);
    H.bullets(s, ["10-15 targeted checks: reset mid-run, en changing while the clock is high.", "Round 2: nothing left to cover, no edit.",
      "The same tests later ran on the ASIC netlist (GLS): PASS."], { x: 7.85, y: 1.75, w: 4.65, h: 4.2 });
    H.evidence(s, "the Sim log, CG after sim-r1", ["FCOV: hit=57 total=57", "FCOV_MISS: -"], 0.85, 3.95, 6.6);
    H.caption(s, "Under the old rule (count comparisons) the agent added 0.5-1.5 million comparisons.", 0.85, 5.15, 6.6, 0.7);
    H.msg(s, "Full functional coverage in one round with a few dozen checks; every round 1 accepted.");
  }


  {
    const s = H.slide("Takeaways and Limits");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["CHIA splits one step over two containers and gates the rest of the flow on its result.",
      "Coverage tells the agent where the gaps are; it closed them in one round."], { x: 0.85, y: 1.7, w: 5.6, h: 4.2 });
    H.head(s, "Limits", 6.85, 1.3, 5.65);
    H.bullets(s, ["Functional bins are hand-written.",
      "One clock; no CDC / RDC check.", "Two rounds, one run each; the agent is not deterministic."], { x: 6.85, y: 1.7, w: 5.65, h: 4.2 });
    H.msg(s, "Next, Part 2: the same ALU goes through Vivado to a bitstream.");
  }
  {
    const s = H.slide("Second Design · ASCON in the Sim Step");
    H.bars(s, [{ name: "baseline", labels: ["Line", "Toggle", "Branch"], values: [71.4, 87.5, 73.3] },
      { name: "agent", labels: ["Line", "Toggle", "Branch"], values: [97.1, 98.4, 95.0] }],
      { x: 0.85, y: 1.3, w: 6.3, h: 3.35, title: "ASCON coverage, baseline vs sim-r1 (%)", fmt: '0.0"%"', min: 0, max: 110, legend: true });
    H.head(s, "Same step, other settings", 7.55, 1.35, 4.95);
    H.bullets(s, ["PASS from a regex in project.py ([PASS], no [FAIL]); lint: 0 errors.", "No functional bins: the rule uses line / toggle / branch.",
      "sim-r1: the agent wrote an Ascon-AEAD128 reference model and 10 corner cases (empty AD/MSG, wrong tag, stalls)."],
      { x: 7.55, y: 1.75, w: 4.95, h: 2.9 });
    H.evidence(s, "the agent's summary, ASCON sim-r1 (before any run)", ["\"If it has a bug, the new checks will print [FAIL] and the round will be",
      " rejected, not accepted with wrong values.\""], 0.85, 4.85, 11.65);
    H.msg(s, "The Sim step and its agent rule worked unchanged on a second design.");
  }
  await H.save("1_Sim.pptx");
}

// ====================================================================================
// 2. FPGA
// ====================================================================================
async function fpgaDeck() {
  const H = deck("Part 2 · FPGA", "Part 2: FPGA with Vivado");
  H.cover("PART 2 · FPGA", "Step 2 of the Flow:", "FPGA with Vivado 2025.1",
    "Self-checking wrapper, sign-off checks on the vivado worker, SAIF power, then agent rounds");

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
    H.bullets(s, ["Arty A7-100T at 188.7 MHz (5.3 ns); ends at the bitstream (no board yet).",
      "Gate: WNS ≥ 0, methodology 0 critical, gate-level sim PASS on the routed netlist."],
      { x: 0.85, y: 4.35, w: 11.65, h: 1.6 });
    H.msg(s, "The wrapper makes the ALU testable on a board with only clock, reset and 4 LEDs.");
  }

  {
    const s = H.slide("Running the FPGA Build on CHIA");
    H.hflow(s, [["read", "sources, pins"], ["synth", null], ["opt", null], ["place", null], ["route", null], ["reports", "timing, util, power"], ["bitstream", null]],
      1.4, 0.95, { g: 0.25, dark: [6] });
    H.caption(s, "build.tcl, run by one task on the vivado worker; then netlist sim writes SAIF for power", 0.85, 2.45, 11.65);
    H.head(s, "Locked vs editable by the agent", 0.85, 2.95, 6.2);
    H.table(s, [["Part", "Agent"], ["ALU RTL, wrapper, pins, clock, checker", "locked"], ["build.tcl (Vivado options)", "may edit"],
      ["Source list, new FPGA-only .v files", "may edit"]], { x: 0.85, y: 3.35, w: 6.2, colW: [4.6, 1.6] });
    H.head(s, "Measured from the reports", 7.45, 2.95, 5.05);
    H.bullets(s, ["WNS and methodology (gate)", "LUT + FF", "Dynamic power from SAIF (real activity)", "Gate-level sim (gate)"], { x: 7.45, y: 3.35, w: 5.05, h: 1.9 });
    H.cmd(s, "python flow/run.py projects/demo-alu/CG baseline", 0.85, 5.45, 7.4);
    H.msg(s, "Power is measured from simulated activity (SAIF), not Vivado's vector-less guess.");
  }

  {
    const s = H.slide("Agent: Tune the FPGA Build");
    H.loop(s, 1.4, "build.tcl, FPGA-only files", "LUT+FF ≤ 3 % worse");
    H.head(s, "Why these files only", 0.85, 2.85, 5.6);
    H.bullets(s, ["The RTL is shared with ASIC; FPGA tricks stay in FPGA-only files.", "A functional check in xsim runs before the build."],
      { x: 0.85, y: 3.25, w: 5.6, h: 2.1 });
    H.head(s, "Gate and rule", 6.85, 2.85, 5.65);
    H.table(s, [["", "FPGA"], ["Gate", "WNS ≥ 0, methodology, GLS PASS"], ["Accept", "WNS, SAIF power no worse; LUT+FF ≤ 3 % worse"], ["Rounds", "up to 2"]],
      { x: 6.85, y: 3.25, w: 5.65, colW: [1.3, 4.35], boldFirstCol: true });
    H.cmd(s, "python flow/run.py projects/demo-alu/NoCG agent --rounds 2", 0.85, 5.45, 8.4);
    H.msg(s, "Rule fixed before the run; if the best so far fails sign-off, a round that passes wins.");
  }

  {
    const s = H.slide("Example · Baseline: Clock Gating Hurts on FPGA");
    H.legend(s, 0.85, 1.3);
    const cw = 2.15;
    [["WNS (ns)", [0.588, 0], 0.8], ["SAIF power (mW)", [5.575, 6.602], 8], ["LUT + FF", [280, 283], 340]].forEach(([t, v, mx], i) => {
      H.bars(s, [{ name: "NoCG", labels: [""], values: [v[0]] }, { name: "CG", labels: [""], values: [v[1]] }],
        { x: 0.85 + i * (cw + 0.05), y: 1.75, w: cw, h: 4.2, title: t, min: 0, max: mx, noCat: true });
    });
    H.head(s, "Why CG is worse here", 7.75, 1.35, 4.75);
    H.bullets(s, ["At 188.7 MHz, all at 30 % activity.", "The latch + LUT on the clock: WNS 0 only by borrowing through the latch.",
      "Methodology: 2 critical (TIMING-14, LUT on the clock tree), so CG fails sign-off.", "Fmax: NoCG 227 MHz, CG latch 128 MHz."], { x: 7.75, y: 1.75, w: 4.75, h: 4.3 });
    H.msg(s, "Clock gating hurts on FPGA: zero slack, +18 % power, and it fails the methodology check.");
  }

  {
    const s = H.slide("Example · Three Ways to Gate a Clock on FPGA");
    H.bars(s, [{ name: "power", labels: ["NoCG", "CG latch", "CG BUFGCE", "CG CE"], values: [5.58, 6.60, 9.36, 5.19] }],
      { x: 0.85, y: 1.35, w: 6.3, h: 4.2, title: "Dynamic power, SAIF, 30 % activity (mW)", min: 0, max: 11, colors: [K.bar] });
    H.table(s, [["", "Fmax (MHz)"], ["NoCG", "227.3"], ["CG latch", "128.2"], ["CG BUFGCE", "196.1"], ["CG CE", "232.6"]],
      { x: 7.55, y: 1.35, w: 4.95, colW: [2.5, 2.45], boldFirstCol: true });
    H.bullets(s, ["BUFGCE: +3.6 mW, all in the clock network (96 %).", "All 139 flops use only 0.115 mW."], { x: 7.55, y: 3.65, w: 4.95, h: 1.9 });
    H.msg(s, "On FPGA no gating style beats NoCG, even at 10 % activity; on ASIC CG saves 12 %.");
  }

  {
    const s = H.slide("Example · Agent Rounds: CG r1 Accepted");
    H.table(s, [["Round", "What the agent did", "Measured", "Result"],
      ["NoCG r1", "Vivado options in build.tcl", "almost no change", "rejected"],
      ["NoCG r2", "FPGA-only ALU file", "LUT+FF −7; WNS 0.588 → 0.456 ns", "rejected"],
      ["CG r1", "Clock gating → clock enable (CE variant, gated_clock_conversion)", "critical 2 → 0; WNS 0 → 0.524 ns; power −21.3 %; LUT+FF +1.4 %", "accepted"],
      ["CG r2", "build.tcl", "WNS up, power +12 %", "rejected"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.35, 4.4, 4.55, 1.35], boldFirstCol: true });
    H.evidence(s, "the agent's summary, CG r1 (before any build)", ["\"WNS is exactly 0.000 ns only because the latch borrows time.\""], 0.85, 4.7, 11.65);
    H.caption(s, "The same change was rejected under the old strict rule on 21/11 (+3 LUT+FF); the rule was changed before this run.", 0.85, 5.5, 11.65);
    H.msg(s, "The agent fixed the real FPGA problem of clock gating: sign-off clean and power −21 %.");
  }


  {
    const s = H.slide("Takeaways and Limits");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["Same RTL, real sign-off checks: methodology, gate-level sim, SAIF power.",
      "The agent reads real Vivado reports; CG r1 fixed clock gating on FPGA."], { x: 0.85, y: 1.7, w: 5.6, h: 4.2 });
    H.head(s, "Limits", 6.85, 1.3, 5.65);
    H.bullets(s, ["Gate-level sim without SDF (zero delay).", "No board yet; > 100 MHz needs an MMCM.", "Fmax has ≈ 0.1-0.2 ns noise."],
      { x: 6.85, y: 1.7, w: 5.65, h: 4.2 });
    H.msg(s, "Next, Part 3: the same RTL goes to a sky130 GDS, where clock gating pays off.");
  }
  {
    const s = H.slide("Second Design · ASCON, Out-of-Context");
    H.table(s, [["ASCON baseline", "Artix-7 100T, 100 MHz"], ["LUT / FF", "1 592 / 473"], ["WNS / WHS (ns)", "1.292 / 0.217 (≈ 114.8 MHz)"],
      ["Methodology critical", "0"], ["SAIF power (66 % nets)", "23.4 mW"], ["Gate-level sim", "PASS (testbench of Sim)"]],
      { x: 0.85, y: 1.35, w: 6.6, colW: [2.9, 3.7], boldFirstCol: true });
    H.head(s, "Out-of-context", 7.85, 1.35, 4.65);
    H.bullets(s, ["No board wrapper: Vivado builds the core alone, no bitstream.", "The flow writes build.tcl itself from project.py.",
      "I/O delays not set yet: 120 TIMING-18 warnings, no critical.", "Agent fpga-r1 (build.tcl): WNS 1.29 → 0.71 ns, power 23.4 → 30.5 mW, rejected."], { x: 7.85, y: 1.75, w: 4.65, h: 3.1 });
    H.evidence(s, "methodology.rpt, ASCON baseline (Vivado 2025.1, fully routed)", ["| TIMING-18 | Warning  | Missing input or output delay | 120    |"], 0.85, 4.95, 11.65);
    H.msg(s, "A new design reaches FPGA sign-off without a hand-written wrapper or build script.");
  }
  await H.save("2_FPGA.pptx");
}

// ====================================================================================
// 3. ASIC
// ====================================================================================
async function asicDeck() {
  const H = deck("Part 3 · ASIC", "Part 3: ASIC with Yosys and OpenROAD");
  H.cover("PART 3 · ASIC", "Step 3 of the Flow:", "RTL to GDS with Yosys + OpenROAD",
    "OpenROAD-flow-scripts on sky130hd, sign-off checks in parallel, then the agent tunes config.mk");

  {
    const s = H.slide("Where ASIC Fits");
    H.flow(s, "ASIC", 1.35);
    H.head(s, "Six ORFS stages, one CHIA task each", 0.85, 2.55, 11.65);
    [["synth", "Yosys"], ["floorplan", "die, I/O"], ["place", "cells"], ["cts", "clock tree"], ["route", "wires"], ["finish", "GDS"]].forEach(([a, b], i) => {
      const x = 0.85 + i * 1.98;
      H.box(s, a, b, x, 3.0, 1.65, 0.9, i === 5);
      if (i < 5) H.arrow(s, x + 1.65, 3.45, x + 1.98, 3.45);
    });
    H.bullets(s, ["After finish, four checks run in parallel: KLayout DRC ‖ LVS ‖ STA with SPEF (tt, tt + OCV ±10 %) ‖ gate-level sim.",
      "Gate: GDS, slack ≥ 0, DRC and antenna = 0, LVS match, GLS PASS."],
      { x: 0.85, y: 4.3, w: 11.65, h: 1.6 });
    H.msg(s, "One task per stage: the timeline shows each stage, and a failure stops where it happened.");
  }

  {
    const s = H.slide("Running the ASIC Flow on CHIA");
    H.head(s, "How it runs", 0.85, 1.3, 5.3);
    H.bullets(s, ["Each stage is one ORFS make call on the asic worker.", "All results stay in the run folder, not in ORFS.",
      "Reads slack, cell and die area, power, DRC, LVS."], { x: 0.85, y: 1.7, w: 5.3, h: 3.4 });
    H.head(s, "Baseline config.mk", 6.55, 1.3, 5.95);
    H.table(s, [["Variable", "NoCG", "CG"], ["DESIGN_NAME", "NoCG_alu", "CG_alu"], ["Extra file", "-", "sky130 ICG cell"],
      ["CORE_UTILIZATION", "40", "40"], ["PLACE_DENSITY", "0.65", "0.65"]],
      { x: 6.55, y: 1.7, w: 5.95, colW: [2.55, 1.6, 1.8], monoCols: [0] });
    H.cmd(s, "python flow/run.py projects/demo-alu/NoCG baseline", 0.85, 4.0, 7.0);
    H.msg(s, "config.mk says how to build; the RTL, PDK and clock stay fixed.");
  }

  {
    const s = H.slide("Agent: Tune config.mk");
    H.loop(s, 1.4, "config.mk only", "1 % rule");
    H.head(s, "Why config.mk only", 0.85, 2.85, 5.6);
    H.bullets(s, ["Design, sources and clock are what is measured, so they are locked.", "Locked variables are checked before the six stages run."],
      { x: 0.85, y: 3.25, w: 5.6, h: 2.1 });
    H.head(s, "Gate and rule", 6.85, 2.85, 5.65);
    H.table(s, [["", "ASIC"], ["Gate", "slack ≥ 0, DRC, antenna, LVS, GLS"], ["Accept", "area, die, power ≤ 1 % worse; one > 1 % better"], ["Rounds", "up to 2"]],
      { x: 6.85, y: 3.25, w: 5.65, colW: [1.3, 4.35], boldFirstCol: true });
    H.cmd(s, "python flow/run.py projects/demo-alu/NoCG agent --rounds 2", 0.85, 5.45, 8.4);
    H.msg(s, "The agent only changes how ORFS builds the same design.");
  }

  {
    const s = H.slide("Example · Baseline: Clock Gating Pays Off on ASIC");
    H.table(s, [["Baseline, 10 ns", "NoCG", "CG"], ["Cell area (µm²)", "3 078", "2 975"], ["Die area (µm²)", "7 534", "6 926"],
      ["Cells (ICG, clock buffers)", "356 (0, 9)", "306 (2, 18)"], ["Setup slack tt / OCV (ns)", "6.82 / 6.48", "6.96 / 6.79"], ["Power (mW)", "0.368", "0.324"],
      ["DRC · antenna · LVS · GLS", "0 · 0 · ok · PASS", "0 · 0 · ok · PASS"]],
      { x: 0.85, y: 1.35, w: 7.6, colW: [3.2, 2.2, 2.2], boldFirstCol: true });
    H.head(s, "Why CG is better here", 8.8, 1.35, 3.7);
    H.bullets(s, ["The ICG replaces the feedback mux: 50 fewer cells.", "Cost: twice as many clock buffers.",
      "CTS handles the ICG cells."], { x: 8.8, y: 1.75, w: 3.7, h: 4.2 });
    H.caption(s, "Power: OpenSTA with default activity, not from simulation.", 0.85, 4.5, 7.6);
    H.evidence(s, "the LVS log and the gate-level sim log, NoCG", ["INFO : Congratulations! Netlists match.", "RESULT: PASS dut=NoCG_alu seed=1 ... checked=6272 errors=0"], 0.85, 4.95, 11.65);
    H.msg(s, "Clock gating pays off on ASIC: 50 fewer cells, power −12 %. The opposite of FPGA.");
  }

  {
    const s = H.slide("Example · What the Agent Changed");
    H.legend(s, 0.85, 1.3);
    H.bars(s, [{ name: "NoCG", labels: ["Die area", "Cell area", "Power"], values: [-27.5, -1.1, -1.4] },
      { name: "CG", labels: ["Die area", "Cell area", "Power"], values: [-20.5, -3.7, -20.4] }],
      { x: 0.85, y: 1.75, w: 6.3, h: 4.3, title: "Accepted (r1) vs baseline (%)", fmt: '0.0"%"', min: -32, max: 0 });
    H.head(s, "Rejected rounds", 7.55, 1.35, 4.95);
    H.bullets(s, ["NoCG r2: die −15 %, power +1.5 %.", "CG r2: die −16 %, power +2.7 %.",
      "Every round passed all sign-off gates; rejects were only \"worse\"."], { x: 7.55, y: 1.75, w: 4.95, h: 4.3 });
    H.msg(s, "Same RTL, tighter packing: die −28 % (NoCG), −21 % (CG); CG power −20 %.");
  }

  {
    const s = H.slide("Example · Layouts, Same Scale");
    const S = 2.9;
    [["NoCG_baseline", 0.85, 1, "NoCG baseline"], ["CG_baseline", 4.85, Math.sqrt(6925.57 / 7534.24), "CG baseline"],
     ["CG_agent", 8.85, Math.sqrt(5504.90 / 7534.24), "CG agent"]].forEach(([f, x, k, t]) => {
      const d = S * k, y = 1.35;
      s.addImage({ path: IMG + f + "_layout.png", x: x + (S - d) / 2, y: y + S - d, w: d, h: d });
      s.addText(t, { x: x - 0.1, y: y + S + 0.12, w: S + 0.2, h: 0.35, fontFace: F, fontSize: 15, bold: true, color: K.ink, align: "center", margin: 0, isTextBox: true });
    });
    [["7 534 µm²", 0.85], ["6 926 µm²", 4.85], ["5 505 µm²", 8.85]].forEach(([t, x]) =>
      s.addText(t, { x: x - 0.1, y: 4.75, w: S + 0.2, h: 0.32, fontFace: F, fontSize: 14, color: K.ink, align: "center", margin: 0, isTextBox: true }));
    H.caption(s, "Picture side ∝ √(die area), relative to the NoCG baseline.", 0.85, 5.35, 11.65);
    H.msg(s, "The agent shrank the die by packing the same cells tighter, not by changing the design.");
  }


  {
    const s = H.slide("Takeaways and Limits");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["RTL → GDS for both ALU variants with basic sign-off: DRC, antenna, LVS, STA with SPEF, GLS.",
      "Tuning only config.mk, the agent cut die 20-28 %, CG power 20 %; all gates still pass."], { x: 0.85, y: 1.7, w: 5.6, h: 4.2 });
    H.head(s, "Limits", 6.85, 1.3, 5.65);
    H.bullets(s, ["Not tape-out sign-off: STA one corner (tt + OCV), GLS without SDF, no LEC.", "ASIC power without real activity; one clock.",
      "Small ALU (≈ 300 cells); agent not deterministic."],
      { x: 6.85, y: 1.7, w: 5.65, h: 4.2 });
    H.msg(s, "Full RTL → GDS flow with basic sign-off, not yet tape-out sign-off.");
  }
  {
    const s = H.slide("Second Design · ASCON from RTL to GDS");
    H.table(s, [["ASCON, sky130hd, 10 ns", "Baseline"], ["Cells · flops", "5 464 · 479"], ["Cell / die area (µm²)", "48 859 / 111 934"],
      ["Setup / hold, tt (ns)", "2.626 / 0.474"], ["tt + OCV ±10 % (ns)", "2.088 / 0.430"], ["Power (mW)", "9.96"], ["DRC · antenna · LVS · GLS", "0 · 0 · match · PASS"]],
      { x: 0.85, y: 1.35, w: 6.6, colW: [3.3, 3.3], boldFirstCol: true });
    H.evidence(s, "the LVS log, ASCON baseline (KLayout, 12.4 s)", ["INFO : Congratulations! Netlists match."], 0.85, 4.95, 6.6);
    H.head(s, "Timing of the run", 7.85, 1.35, 4.65);
    H.bullets(s, ["Route ≈ 10 min; whole run 13.3 min.", "Sign-off checks in parallel: 41.7 s (72.4 s in series).",
      "Agent asic-r1: die −15 %, cell +4.8 %, rejected.", "GLS ran the agent's new testbench: PASS."], { x: 7.85, y: 1.75, w: 4.65, h: 4.3 });
    H.msg(s, "A 5 000-cell design passes the same sign-off gates as the ALU, with no change to the flow.");
  }
  await H.save("3_ASIC.pptx");
}

(async () => {
  require("fs").mkdirSync(OUT, { recursive: true });
  // ONLY=setup,sim,fpga,asic để build từng deck
  const only = (process.env.ONLY || 'setup,sim,fpga,asic').split(',');
  if (only.includes('setup')) await setupDeck();
  if (only.includes('sim')) await simDeck();
  if (only.includes('fpga')) await fpgaDeck();
  if (only.includes('asic')) await asicDeck();
  console.log("written to", OUT);
})();
