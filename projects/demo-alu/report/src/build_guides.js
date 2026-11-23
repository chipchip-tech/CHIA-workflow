// build_guides.js - 4 deck báo cáo cho thầy hướng dẫn (tiếng Anh), cùng style với ProgressReport_22-11.pptx:
//   guides/0_Setup.pptx  cách đã setup CHIA (máy thật) + kế hoạch worker remote/cloud
//   guides/1_Sim.pptx, 2_FPGA.pptx, 3_ASIC.pptx  từng bước của demo ALU, ví dụ NoCG/CG
// Quy ước: trên slide là sơ đồ, bảng, số liệu; lệnh chạy nằm trong speaker notes.
// Chạy: NODE_PATH=<thư mục có pptxgenjs>/node_modules node build_guides.js [thư mục ra]
// Số liệu lấy từ REPORT.md và docs/demo-task.md (chạy thật 20–21/11/2026).
const pptxgen = require("pptxgenjs");
const path = require("path");
const OUT = process.argv[2] || path.join(__dirname, "..", "guides");
const IMG = path.join(__dirname, "..", "img") + "/";
const F = "Times New Roman", M = "Courier New";
const K = { black: "000000", ink: "262626", mute: "5A5A5A", cap: "9A9A9A", line: "BFBFBF", head: "D9D9D9", soft: "F2F2F2", grid: "A6A6A6" };

// ghép notes với danh sách lệnh
function withCmds(text, cmds) {
  return cmds && cmds.length ? text + "\n\nCommands:\n" + cmds.join("\n") : text;
}

function deck(name, titleText) {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE";
  pres.title = titleText;
  pres.theme = { headFontFace: F, bodyFontFace: F };
  let n = 0;
  const H = {
    pres,
    slide(t, notes) {
      const s = pres.addSlide(); n += 1;
      if (t) s.addText(t, { x: 0.85, y: 0.45, w: 11.65, h: 0.7, fontFace: F, fontSize: 28, bold: true, color: K.black, margin: 0, isTextBox: true });
      s.addText(name, { x: 0.85, y: 7.0, w: 6, h: 0.3, fontFace: F, fontSize: 10, color: K.cap, margin: 0, isTextBox: true });
      s.addText(String(n), { x: 12.2, y: 7.0, w: 0.5, h: 0.3, fontFace: F, fontSize: 10, color: K.cap, align: "right", margin: 0, isTextBox: true });
      if (notes) s.addNotes(notes);
      return s;
    },
    head(s, t, x, y, w) {
      s.addText(t, { x, y, w, h: 0.38, fontFace: F, fontSize: 16, bold: true, color: K.black, margin: 0, isTextBox: true });
    },
    bullets(s, items, o) {
      const runs = [];
      items.forEach((it, i) => {
        const last = i === items.length - 1;
        runs.push({ text: it, options: { bullet: { indent: 16 }, paraSpaceAfter: 5, color: K.ink, breakLine: !last } });
      });
      s.addText(runs, { fontFace: F, fontSize: o.fs || 14, valign: "top", margin: 0, isTextBox: true, x: o.x, y: o.y, w: o.w, h: o.h });
    },
    para(s, t, o) {
      s.addText(t, { fontFace: F, fontSize: o.fs || 14, color: o.color || K.ink, valign: "top", margin: 0, isTextBox: true, x: o.x, y: o.y, w: o.w, h: o.h, italic: !!o.italic, bold: !!o.bold, align: o.align });
    },
    expect(s, t, x, y, w, h, label) {
      s.addText([{ text: (label || "Result") + "  ", options: { bold: true, color: K.black } }, { text: t, options: { color: K.ink } }],
        { x, y, w, h: h || 0.5, fontFace: F, fontSize: 12.5, valign: "top", margin: [4, 8, 4, 8], line: { color: K.ink, width: 1, dashType: "dash" }, isTextBox: true });
    },
    caption(s, t, x, y, w, h) {
      s.addText(t, { x, y, w, h: h || 0.3, fontFace: F, fontSize: 12, italic: true, color: K.mute, margin: 0, isTextBox: true });
    },
    box(s, a, b, x, y, w, h, dark, fs) {
      const parts = [{ text: a, options: { bold: true, fontSize: fs || 12.5, breakLine: !!b } }];
      if (b) parts.push({ text: b, options: { fontSize: (fs || 12.5) - 1.5 } });
      s.addText(parts, { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.08, x, y, w, h, fontFace: F, align: "center", valign: "middle",
        color: dark ? "FFFFFF" : K.black, fill: { color: dark ? K.ink : "FFFFFF" }, line: { color: K.ink, width: 1 }, margin: 3 });
    },
    frame(s, x, y, w, h) {
      s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: K.soft }, line: { color: K.line, width: 0.75, dashType: "dash" } });
    },
    tag(s, t) {
      s.addText(t, { shape: pres.shapes.ROUNDED_RECTANGLE, rectRadius: 0.06, x: 10.1, y: 0.55, w: 2.4, h: 0.42, fontFace: F, fontSize: 12, bold: true,
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
        bold: i === 0 || (o.boldFirstCol && j === 0), fill: { color: i === 0 ? K.head : "FFFFFF" },
        fontFace: (o.monoCols || []).includes(j) && i > 0 ? M : F } })));
      s.addTable(data, { fontFace: F, fontSize: o.fs || 12.5, color: K.black, border: { type: "solid", pt: 0.75, color: K.grid },
        margin: [3, 6, 3, 6], valign: "middle", autoPage: false, x: o.x, y: o.y, w: o.w, colW: o.colW, rowH: o.rowH });
    },
    // chuỗi hộp ngang có mũi tên; items = [[a, b], ...]; dark = chỉ số hộp tô đậm
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
    // chuỗi hộp dọc có mũi tên
    vflow(s, items, x, y, w, h, g, o) {
      o = o || {};
      items.forEach(([a, b], i) => {
        const yy = y + i * (h + g);
        H.box(s, a, b, x, yy, w, h, (o.dark || []).includes(i), o.fs);
        if (i < items.length - 1) H.arrow(s, x + w / 2, yy + h, x + w / 2, yy + h + g);
      });
      return y + items.length * (h + g) - g;
    },
    // dải bước Sim → FPGA → ASIC, tô đậm bước hiện tại
    flow(s, cur, y) {
      const st = [["Sim", "Verilator"], ["FPGA", "Vivado 2025.1"], ["ASIC", "Yosys + OpenROAD"]];
      st.forEach(([a, b], i) => {
        const x = 0.85 + i * 3.3;
        H.box(s, a, b, x, y, 2.4, 0.75, a === cur);
        if (i < 2) H.arrow(s, x + 2.4, y + 0.375, x + 3.3, y + 0.375);
      });
      H.caption(s, "next step runs only after the gate of the previous one passes", 0.85, y + 0.82, 9.0);
    },
    // vòng agent: chung cho 3 deck
    loop(s, y, edit, rule) {
      const g = 0.3, w = (11.65 - 5 * g) / 6;
      const st = [["1. Copy", "best tree → round dir"], ["2. Claude edits", edit], ["3. CHIA reverts", "edits outside scope"],
                  ["4. Re-measure", "on the real tool"], ["5. Gate", "pass / fail"], ["6. Compare", rule]];
      st.forEach(([a, b], i) => {
        const x = 0.85 + i * (w + g);
        H.box(s, a, b, x, y, w, 0.95, i === 1, 12);
        if (i < st.length - 1) H.arrow(s, x + w, y + 0.475, x + w + g, y + 0.475);
      });
    },
    cover(kicker, t1, t2, sub) {
      const s = pres.addSlide(); n += 1;
      s.addText(kicker, { x: 0.4, y: 1.45, w: 9, h: 0.35, fontFace: F, fontSize: 13, color: K.mute, charSpacing: 3, margin: 0, isTextBox: true });
      s.addText([{ text: t1, options: { breakLine: true } }, { text: t2 }],
        { x: 0.4, y: 2.0, w: 12, h: 1.5, fontFace: F, fontSize: 34, bold: true, color: K.black, valign: "middle", margin: 0, isTextBox: true });
      s.addText(sub, { x: 0.4, y: 3.55, w: 12, h: 0.4, fontFace: F, fontSize: 16, color: K.mute, margin: 0, isTextBox: true });
      s.addShape(pres.shapes.LINE, { x: 0.5, y: 4.3, w: 4.2, h: 0, line: { color: K.line, width: 1 } });
      s.addText([{ text: "Advisor: MSc. Phạm Thế Hùng", options: { breakLine: true } }, { text: "Student: Phan Thành Nhân" }],
        { x: 0.5, y: 4.5, w: 6, h: 0.8, fontFace: F, fontSize: 16, color: K.black, margin: 0, isTextBox: true });
      s.addText("Report series: 0 Setup · 1 Sim · 2 FPGA · 3 ASIC", { x: 0.5, y: 6.6, w: 8, h: 0.3, fontFace: F, fontSize: 12, color: K.cap, margin: 0, isTextBox: true });
      return s;
    },
    save(file) { return pres.writeFile({ fileName: path.join(OUT, file) }); },
  };
  return H;
}

// slide hai cột: trái tiêu đề + bullet; phải tiêu đề (nội dung phải vẽ sau)
function twoCol(H, t, notes, lh, items, rh, o) {
  o = o || {};
  const lw = o.lw || 5.3, rx = 0.85 + lw + 0.4, rw = 12.5 - rx;
  const s = H.slide(t, notes);
  H.head(s, lh, 0.85, 1.3, lw);
  H.bullets(s, items, { x: 0.85, y: 1.75, w: lw, h: o.lh || 5.0, fs: o.fs || 14 });
  if (rh) H.head(s, rh, rx, 1.3, rw);
  s.rx = rx; s.rw = rw;
  return s;
}

// ====================================================================================
// 0. SETUP: cách đã setup CHIA trên máy thật + kế hoạch worker remote/cloud
// ====================================================================================
async function setupDeck() {
  const H = deck("Part 0 · Setup", "Part 0: How the CHIA Framework Was Set Up");
  H.cover("PART 0 · FRAMEWORK SETUP", "Setting up CHIA:", "How the Framework Was Installed",
    "A single-machine Ray cluster of tool containers, used for the ALU demo, and a plan for remote / cloud workers");

  // 2. CHIA chạy thế nào
  {
    const s = H.slide("How CHIA Runs Work",
      withCmds("CHIA (UC Berkeley, pip package chialoops) is a hardware/software co-design framework built on Ray. Docs: docs.chialoops.ai; paper arXiv:2606.27350.\n\nThe head runs Ray and the driver script. Each worker is a Docker container with one set of tools and advertises a resource name. A function decorated with @ChiaFunction(resources=...) runs on any worker that has that resource, on whichever machine it lives.\n\nchia job submit --working-dir . copies the project folder to every worker, so the modules that define the tasks can be imported there.\n\nMinimal task:\n@ChiaFunction(resources={\"verilator_run\": 1})\ndef run_sim(path): ...\nrc = get(run_sim.chia_remote(path))",
      ["conda activate chia_env", "chia job submit --working-dir . -- python my_loop.py", "chia job logs -f <id>", "chia job stop <id>"]));
    H.box(s, "Head (user's PC)", "Ray head · driver script · chia job submit", 3.9, 1.35, 5.5, 0.8, true);
    const bl = [["chipyard", "Chisel / Verilator build"], ["verilator", "run simulations"], ["vivado", "FPGA build"],
                ["asic", "Yosys + OpenROAD"], ["claude", "LLM agent"]];
    bl.forEach(([a, b], i) => {
      const x = 0.85 + i * 2.37;
      H.box(s, a + " worker", b, x, 2.7, 2.15, 0.85, false, 12.5);
      H.arrow(s, 6.65, 2.15, x + 1.075, 2.7);
    });
    H.caption(s, "Each worker is a Docker container; here all of them run on the head machine.", 0.85, 3.65, 11.6);
    H.table(s, [["Term", "Meaning in CHIA"],
      ["Worker (block)", "Docker container with one set of tools; advertises a resource, e.g. chipyard, vivado, asic"],
      ["Task", "Python function marked @ChiaFunction(resources=...); Ray runs it on a worker that has the resource"],
      ["Job", "The driver script, sent with chia job submit; it calls tasks and waits for their results with get()"],
      ["Gate", "A check in the driver on a task's result; the next step only starts if it passes"]],
      { x: 0.85, y: 4.15, w: 11.65, colW: [2.2, 9.45], fs: 13, boldFirstCol: true });
  }

  // 3. Máy và phiên bản
  {
    const s = H.slide("Machine and Software Used",
      "Every version is pinned. Ray checks that workers run the same Python as the head down to the patch version, so the conda env uses 3.10.19 like the worker images.\n\nChipyard is the upstream ucb-bar/chipyard at tag 1.15.0, checked out as local branch local-1.15.0. CHIA's own Chipyard image is based on 1.14.0, so the host copy is mounted instead (slide 6).\n\nThe GPU is not used by any tool in the flow.");
    H.head(s, "Host (head node)", 0.85, 1.3, 5.4);
    H.table(s, [["", "Value"], ["OS", "Bazzite-DX (Fedora Atomic, NVIDIA image)"], ["CPU / RAM", "20 cores / 23 GB (32 GB recommended)"],
      ["Disk", "≈ 259 GB free on /var"], ["Containers", "Docker, usable without sudo"], ["Other machines", "none yet (remote = plan, slides 10-11)"]],
      { x: 0.85, y: 1.7, w: 5.4, colW: [1.7, 3.7], fs: 13, boldFirstCol: true });
    H.head(s, "Software", 6.65, 1.3, 5.85);
    H.table(s, [["Component", "Version", "Source"], ["Python", "3.10.19", "conda env chia_env"], ["Ray", "2.54.0", "with CHIA"],
      ["CHIA", "chialoops 2.0.1", "PyPI"], ["Chipyard", "1.15.0", "ucb-bar, tag pinned"], ["Verilator", "5.022", "Chipyard conda env"],
      ["Vivado", "2025.1", "Xilinx installer"], ["Yosys / OpenROAD", "0.68 / ORFS", "image chia-orfs"], ["Agent", "Claude Code", "image chia-claude-code"]],
      { x: 6.65, y: 1.7, w: 5.85, colW: [1.9, 1.75, 2.2], fs: 13, boldFirstCol: true });
  }

  // 4. Quy trình setup
  {
    const s = H.slide("Setup Process",
      "The whole setup is written down as docs/reinstall.md in the CHIA-workflow repo. To check that the document is complete, CHIA was removed from the machine and reinstalled from scratch on 14/11/2026 by following it step by step; every step ended with its check passing.\n\nThe first install (08/11/2026) used an older Chipyard fork (1.10.0); the reinstall moved to upstream Chipyard 1.15.0.");
    H.hflow(s, [["1. Host", "Docker, SSH, linger"], ["2. Python + CHIA", "conda env chia_env"], ["3. Chipyard", "1.15.0 in ~/tools"],
      ["4. FPGA, ASIC, agent", "Vivado · ORFS · Claude"], ["5. Cluster", "bring up + smoke test"]], 1.45, 0.95, { dark: [4] });
    H.head(s, "Check at the end of each step", 0.85, 2.75, 11.6);
    H.table(s, [["Step", "Check that passed"],
      ["1. Host", "docker runs without sudo; ssh to own IP without password; scripts/check-bazzite-env.sh all pass (RAM warning only)"],
      ["2. Python + CHIA", "python 3.10.19, ray 2.54.0, chia --help"],
      ["3. Chipyard", "VERIFY_OK: RocketConfig builds in Verilator, a RISC-V test program exits with 0"],
      ["4. FPGA, ASIC, agent", "vivado v2025.1 inside the worker; ORFS VERIFY_OK (nangate45/gcd to GDS); Claude logged in"],
      ["5. Cluster", "ray status lists all resources; blocks_smoke.py prints KẾT QUẢ: OK"]],
      { x: 0.85, y: 3.15, w: 11.65, colW: [2.4, 9.25], fs: 13, boldFirstCol: true });
    H.caption(s, "08/11 first install · 14/11 full reinstall from the written steps · 15/11 clean shutdown checked · 20-21/11 used for the ALU demo", 0.85, 6.2, 11.65);
  }

  // 5. Host + Python
  {
    const s = H.slide("Steps 1-2 · Host and Python Environment",
      withCmds("Machine: Bazzite-DX (ghcr.io/ublue-os/bazzite-dx-nvidia:stable). Plain Bazzite only has Podman, which CHIA does not support; the DX image ships Docker.\n\nThe Ray head is started under systemd-run --user --scope in the cluster template, together with linger, so it is not tied to the SSH session that chia up opens.\n\nVerified on the user's machine on 14/11/2026 (docs/reinstall.md steps 0-4).",
      ["ujust dx-group                     # then log in again", "docker run --rm hello-world", "sudo systemctl enable --now sshd",
       "ssh-keygen -t ed25519; cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys", "loginctl enable-linger $USER",
       "conda create -n chia_env python=3.10.19 -y", "conda env config vars set PYTHONNOUSERSITE=1 -n chia_env",
       "conda activate chia_env && pip install chialoops==2.0.1",
       "git clone -b setup https://github.com/chipchip-tech/CHIA-workflow.git ~/CHIA-workflow", "~/CHIA-workflow/scripts/check-bazzite-env.sh"]));
    H.table(s, [["Requirement", "Why CHIA needs it", "How it was set up"],
      ["Docker without sudo", "CHIA always calls the docker command to start workers", "Bazzite-DX docker group (ujust dx-group)"],
      ["SSH to own IP, no password", "chia up opens an SSH session to the head IP, even on one machine", "sshd enabled, ed25519 key in authorized_keys"],
      ["Linger", "Keeps the Ray head alive after that SSH session closes", "loginctl enable-linger; head started under systemd-run"],
      ["Python 3.10.19", "Must match the Python inside the worker images exactly", "Miniforge, conda env chia_env"],
      ["CHIA + Ray", "Framework, cluster commands, job submission", "pip install chialoops==2.0.1 (brings Ray 2.54.0)"],
      ["Isolated packages", "Avoid packages leaking in from ~/.local", "PYTHONNOUSERSITE=1 in chia_env"],
      ["Environment check", "Catch missing pieces before chia up", "scripts/check-bazzite-env.sh"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [2.6, 5.0, 4.05], fs: 13, boldFirstCol: true });
    H.caption(s, "Commands for every row are in the speaker notes and in docs/reinstall.md.", 0.85, 5.75, 11.6);
  }

  // 6. Công cụ trên host, mount vào worker
  {
    const s = H.slide("Tools Live on the Host, Workers Mount Them",
      "Design choice of this setup (docs/cluster-blocks.md): the large tools are installed once under ~/tools on the host and mounted into the worker containers, instead of living only inside images.\n\nThey are mounted at the same real path as on the host (/var/home/..., because /home is a symlink on Bazzite): Chipyard's env.sh and Vivado's settings64.sh store absolute paths.\n\nThe claude worker sees Chipyard read-only, so the agent can read sources but only writes inside WORK_DIR.");
    H.frame(s, 0.85, 1.35, 4.6, 3.55);
    H.para(s, "Host  ~/tools/", { x: 1.0, y: 1.42, w: 4.3, h: 0.35, fs: 14, bold: true });
    [["chipyard/", "Chipyard 1.15.0 + RISC-V tools"], ["Xilinx/2025.1/", "Vivado (mounted read-only)"], ["OpenROAD-flow-scripts/", "ORFS (copied out of chia-orfs)"]]
      .forEach(([a, b], i) => H.box(s, a, b, 1.05, 1.85 + i * 0.72, 4.2, 0.62, false, 12.5));
    H.box(s, "~/chia_demo/  (WORK_DIR)", "shared run folders, read-write", 1.05, 4.1, 4.2, 0.62, true, 12.5);
    H.arrow(s, 5.45, 3.1, 6.75, 3.1);
    H.para(s, "mounted at the same real path", { x: 5.4, y: 2.35, w: 1.45, h: 0.5, fs: 11, align: "center" });
    const wk = [["chipyard", "chia-chisel-build · chipyard/"], ["verilator", "chia-verilator-run"], ["vivado", "chia-chisel-build · chipyard/, Xilinx/"],
                ["asic", "chia-orfs · OpenROAD-flow-scripts/"], ["claude", "chia-claude-code · chipyard/ (read-only)"]];
    wk.forEach(([a, b], i) => H.box(s, a + " worker", "image " + b, 6.75, 1.35 + i * 0.73, 5.75, 0.63, false, 12.5));
    H.caption(s, "every worker also mounts WORK_DIR", 6.75, 5.0, 5.75);
    H.head(s, "Why", 0.85, 5.2, 11.6);
    H.bullets(s, ["Tools are edited with a normal editor on the host and survive when containers are recreated.",
      "Builds still run inside the containers, so the host only needs Docker; one tool copy serves every worker that needs it."],
      { x: 0.85, y: 5.6, w: 11.65, h: 1.2, fs: 14 });
  }

  // 7. Cài công cụ
  {
    const s = H.slide("Steps 3-4 · Installing the Tools",
      withCmds("Chipyard: setup-chipyard.sh clones ucb-bar/chipyard, checks out tag 1.15.0 as branch local-1.15.0, and runs all 11 steps of build-setup.sh in a temporary chia-chisel-build container (--rm). It takes hours. If a step stops, the script prints the step number and the command to resume. Steps 8-9 (FireMarshal) need --privileged.\n\nVivado: installed with the Xilinx installer into ~/tools/Xilinx; Digilent board files added for the Arty A7-100T. The flow was checked through a CHIA job (examples/fpga_smoke.py) that built a Chipyard Rocket SoC bitstream for the Arty board.\n\nORFS: docker/orfs-ray.Dockerfile adds Ray (own Python 3.10.19) to the openroad/orfs image; setup-orfs.sh copies ORFS out of it to ~/tools.\n\nClaude Code: logged in once on the host; the claude worker reuses ~/.claude.",
      ["cd ~/CHIA-workflow && scripts/setup-chipyard.sh --verify", "git clone --depth 1 https://github.com/Digilent/vivado-boards /tmp/vb",
       "cp -r /tmp/vb/new/board_files/arty-a7-100 ~/tools/Xilinx/2025.1/Vivado/data/boards/board_files/",
       "docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker", "scripts/setup-orfs.sh --verify",
       "claude && cp ~/.claude.json ~/.claude/.claude.json"]));
    H.table(s, [["Tool", "Version", "How it was installed", "Check that passed", "Date"],
      ["Chipyard", "1.15.0", "scripts/setup-chipyard.sh: clone, pin tag, run build-setup.sh (11 steps) in a temporary container", "VERIFY_OK: RocketConfig Verilator build, test EXIT=0", "14/11"],
      ["Vivado", "2025.1", "Xilinx installer into ~/tools/Xilinx, Digilent board files added", "vivado v2025.1 in the worker; Arty A7-100T bitstream through a CHIA job", "14/11"],
      ["ORFS", "Yosys 0.68 + OpenROAD", "Image chia-orfs (openroad/orfs + Ray), then scripts/setup-orfs.sh copies ORFS to ~/tools", "VERIFY_OK: nangate45/gcd to GDS", "08/11, 14/11"],
      ["Claude Code", "-", "Logged in once on the host; the claude worker reuses ~/.claude", "Agent sessions of the ALU demo", "21/11"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.5, 1.6, 4.25, 3.3, 1.0], fs: 12.5, boldFirstCol: true });
    H.caption(s, "Vivado, ORFS and the agent are optional: a project enables only the workers it needs (next slide).", 0.85, 5.3, 11.6);
  }

  // 8. Cluster theo khối
  {
    const s = H.slide("Step 5 · The Block Cluster",
      withCmds("chia-up-blocks.py reads configs/cluster-blocks.template.yaml, keeps only the requested blocks, fills in paths and the machine IP, writes cluster.gen.yaml and runs chia up. Before that it checks the prerequisites: Chipyard env.sh, Vivado settings64.sh, the chia-orfs image, the Claude login.\n\nWORK_DIR (default ~/chia_demo) is created if missing and mounted read-write into every block, so one task can read what another wrote (for example the Verilator binary built on chipyard and run on verilator).\n\nOther options: --dry-run (show the config only), --verilator-slots N (N simulations at once), ASIC_MEM=20g (RAM limit of the asic container, default 12g).",
      ["conda activate chia_env && cd ~/CHIA-workflow", "python scripts/chia-up-blocks.py --fpga --asic", "python scripts/chia-up-blocks.py --down"]));
    H.table(s, [["Block", "Resource", "Image", "Host folders mounted", "Enabled by", "Used in the demo for"],
      ["chipyard", "chipyard", "chia-chisel-build", "chipyard/", "default", "sim_build (Verilator)"],
      ["verilator", "verilator_run", "chia-verilator-run", "-", "default", "sim_run"],
      ["vivado", "vivado", "chia-chisel-build", "chipyard/, Xilinx/ (ro)", "--fpga", "fpga_build, fpga_sim"],
      ["asic", "asic", "chia-orfs", "OpenROAD-flow-scripts/", "--asic", "asic_stage × 6"],
      ["claude", "claude_creds", "chia-claude-code", "~/.claude, chipyard/ (ro)", "default", "agent_session"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.35, 1.65, 2.05, 2.75, 1.25, 2.6], fs: 12.5, boldFirstCol: true });
    H.head(s, "One script brings the cluster up and down", 0.85, 4.1, 11.6);
    H.hflow(s, [["chia-up-blocks.py", "--fpga --asic"], ["checks", "tools present?"], ["cluster.gen.yaml", "only chosen blocks"], ["chia up", "head + containers"], ["ray status", "all resources listed"]],
      4.55, 0.85, { dark: [0] });
    H.caption(s, "Shutdown also goes through the script (--down), which removes the head and every container.", 0.85, 5.6, 11.6);
  }

  // 9. Bật cluster và smoke test
  {
    const s = H.slide("Bring-up and Smoke Test",
      withCmds("blocks_smoke.py submits one small task per enabled block and reports what each container sees: the Chipyard environment and a RocketConfig build on chipyard, the runtime on verilator, the Vivado version on vivado, the Yosys/OpenROAD binaries and a writable flow folder on asic.\n\n--clean rebuilds RocketConfig from scratch.\n\nopenroad -version prints 'unknown' inside the worker because the image was built without a git SHA; this is not an error.",
      ["python scripts/chia-up-blocks.py --fpga --asic", "ray status", "cd ~/CHIA-workflow/examples && chia job submit --working-dir . -- python blocks_smoke.py --clean",
       "python ~/CHIA-workflow/scripts/chia-up-blocks.py --down"]));
    H.head(s, "What the smoke test checks", 0.85, 1.3, 11.6);
    H.table(s, [["Block", "Check", "Result", "Date"],
      ["chipyard", "Chipyard env loaded, RISCV set; RocketConfig built through CHIA", "SUCCESS=True", "14/11/2026"],
      ["verilator", "Simulator runtime present", "OK", "14/11/2026"],
      ["vivado", "Vivado found on PATH inside the worker", "vivado v2025.1", "14/11/2026"],
      ["asic", "Yosys and OpenROAD present, ORFS flow folder writable", "Yosys 0.68, flow_writable True", "14/11/2026"],
      ["all five", "Whole smoke job", "KẾT QUẢ: OK", "14/11/2026"],
      ["shutdown", "--down removes the Ray head and all containers", "docker ps empty", "15/11/2026"]],
      { x: 0.85, y: 1.7, w: 11.65, colW: [1.5, 5.6, 2.85, 1.7], fs: 13, boldFirstCol: true });
    H.expect(s, "The five-block cluster then ran the whole ALU demo (Parts 1-3): baseline on 20-21/11/2026 and agent rounds on 21/11/2026.", 0.85, 5.15, 11.65, 0.55);
  }

  // 10. Remote: kiến trúc
  {
    const s = H.slide("Remote Workers: Architecture",
      "Plan only. Written from the CHIA documentation and source (cluster_config_reference.rst, chia/cluster/*.py); not run yet because there is no second machine. The files are ready in the repo: configs/cluster-lan.yaml, scripts/prepare-remote-worker.sh, scripts/check-remote-worker.sh, docs/usage-remote.md.\n\nThe remote does not need conda or CHIA: chia up SSHes into it (ssh -A ... bash --login), pulls the images, starts containers with --net=host --init and runs ray start --address=<HEAD_IP>:6379 inside them.\n\nRay has no login: anyone who can reach the Ray ports can run code on the cluster. The firewall must allow only the cluster machines' IPs, never the Internet.");
    H.tag(s, "PLAN · NOT TESTED");
    H.box(s, "HEAD (user's PC)", "Ray head :6379 · driver · local workers", 0.85, 1.45, 4.2, 1.0, true);
    H.box(s, "REMOTE (lab PC or cloud VM)", "sshd · Docker · worker containers", 8.3, 1.45, 4.2, 1.0);
    H.arrow(s, 5.05, 1.75, 8.3, 1.75);
    H.arrow(s, 8.3, 2.15, 5.05, 2.15);
    H.para(s, "SSH: start containers, ray start", { x: 5.1, y: 1.38, w: 3.1, h: 0.3, fs: 11.5, align: "center" });
    H.para(s, "Ray: 6379 + 10002-19999, both ways", { x: 5.1, y: 2.2, w: 3.1, h: 0.3, fs: 11.5, align: "center" });
    H.head(s, "What the remote machine needs", 0.85, 2.95, 5.6);
    H.table(s, [["Item", "Value"], ["Software", "sshd, Docker without sudo, rsync"], ["Not needed", "conda, CHIA"], ["CPU", "x86_64 (same images)"],
      ["RAM / disk", "≥ 16 GB per Chipyard worker, ≥ 120 GB free"]], { x: 0.85, y: 3.35, w: 5.6, colW: [1.6, 4.0], fs: 13, boldFirstCol: true });
    H.head(s, "What changes and what does not", 6.85, 2.95, 5.65);
    H.bullets(s, ["The driver still runs on the head; Ray sends tasks to any machine that has the resource.",
      "Job code does not change. Tasks that must share files are pinned to one machine with a per-machine resource (cy_head, cy_remote).",
      "Firewall: each machine trusts only the other's IP.",
      "Different networks or a cloud VM: connect the machines with Tailscale and use the Tailscale IPs."],
      { x: 6.85, y: 3.35, w: 5.65, h: 3.4, fs: 13.5 });
  }

  // 11. Remote: các bước dự kiến
  {
    const s = H.slide("Remote Workers: Planned Steps",
      withCmds("Plan only, not run.\n\nprepare-remote-worker.sh runs on the remote and asks before each step: enable sshd, install Docker and add the user to the docker group, install rsync, open the firewall for the head, pre-pull the large images. It supports Ubuntu/Debian, Fedora and Fedora Atomic.\n\ncheck-remote-worker.sh runs on the head and only reads: SSH, CPU architecture, Docker without sudo, rsync, RAM and disk, and (when the cluster is up) whether the remote reaches HEAD:6379.\n\nOnly one head per machine: shut the local cluster down first (both use port 6379). The template's remote workers use the tools built into the CHIA images; using ~/tools on the remote would need the same folders at the same path there.\n\nCloud VM: any provider with SSH, Docker and enough disk; keep the provider's security group closed to the Ray ports and join the VM to the tailnet. Without root on a machine, CHIA has a tailnet: mode (userspace Tailscale).",
      ["scp ~/CHIA-workflow/scripts/prepare-remote-worker.sh <user>@<REMOTE_IP>:", "# on the remote:", "bash prepare-remote-worker.sh <HEAD_IP>",
       "# on the head:", "ssh-copy-id <user>@<REMOTE_IP>; eval \"$(ssh-agent -s)\"; ssh-add", "bash scripts/check-remote-worker.sh <user>@<REMOTE_IP> <HEAD_IP>",
       "export HEAD_IP=... REMOTE_IP=... REMOTE_USER=...", "chia up ~/CHIA-workflow/configs/cluster-lan.yaml -y", "chia status",
       "chia down ~/CHIA-workflow/configs/cluster-lan.yaml -y", "# Tailscale (each machine): sudo tailscale up; tailscale ip -4"]));
    H.tag(s, "PLAN · NOT TESTED");
    H.hflow(s, [["1. Prepare remote", "prepare-remote-worker.sh: sshd, Docker, rsync, firewall"], ["2. Check from head", "check-remote-worker.sh: no [FAIL]"],
      ["3. Describe cluster", "cluster-lan.yaml: HEAD_IP, REMOTE_IP, REMOTE_USER"], ["4. Bring up", "chia up, chia status"]], 1.45, 1.1, { dark: [3], fs: 13 });
    H.expect(s, "chia status shows 7 nodes; resources chipyard: 2, verilator_run: 3, cy_head: 1, cy_remote: 1.", 0.85, 2.75, 11.65, 0.5, "Expected");
    H.head(s, "Lab PC vs cloud VM", 0.85, 3.6, 11.6);
    H.table(s, [["", "Lab PC on the same LAN", "Cloud VM / other network"],
      ["Connection", "Direct LAN IPs", "Tailscale on both machines, Tailscale IPs"],
      ["Firewall", "Trust the other machine's IP", "Trust the tailscale0 interface; Ray ports closed to the Internet"],
      ["Speed", "Fast enough to share run folders", "Slower: keep heavy files on one machine"],
      ["To verify", "Needs a second PC", "Needs a VM with SSH, Docker, ≥ 120 GB disk"]],
      { x: 0.85, y: 4.0, w: 11.65, colW: [1.9, 4.6, 5.15], fs: 13, boldFirstCol: true });
  }

  // 12. Trạng thái
  {
    const s = H.slide("Setup Status",
      "Verified = run on the user's machine with the check passing. The dates match the notes in CLAUDE.md of the repo.\n\nThe remote part becomes testable as soon as a second machine (lab PC or cloud VM) is available; the scripts check the machine before anything is started.");
    H.table(s, [["Item", "Status"],
      ["Single-machine cluster with five blocks (chipyard, verilator, vivado, asic, claude)", "verified 14/11/2026"],
      ["Reinstall from scratch by following docs/reinstall.md", "verified 14/11/2026"],
      ["Clean shutdown (--down)", "verified 15/11/2026"],
      ["Shared WORK_DIR between blocks", "verified 20/11/2026"],
      ["ALU demo: Sim, FPGA, ASIC baseline and agent rounds on this cluster", "verified 20-21/11/2026 (Parts 1-3)"],
      ["Remote worker on a LAN (cluster-lan.yaml, prepare / check scripts)", "written, not tested"],
      ["Cloud VM / Tailscale", "written, not tested"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [8.0, 3.65], fs: 13.5 });
    H.head(s, "Next", 0.85, 5.0, 11.6);
    H.bullets(s, ["Get a second machine and run the remote plan (slides 10-11).", "Then spread independent tasks (simulations, ASIC runs) over both machines."],
      { x: 0.85, y: 5.4, w: 11.65, h: 1.2, fs: 14 });
  }
  await H.save("0_Setup.pptx");
}

// ====================================================================================
// 1. SIM
// ====================================================================================
async function simDeck() {
  const H = deck("Part 1 · Simulation", "Part 1: Simulation with Verilator");
  H.cover("PART 1 · SIMULATION", "Step 1 of the Flow:", "Simulation with Verilator",
    "Build on the chipyard block, run on the verilator block, then let Claude extend the testbench");

  {
    const s = H.slide("Where Sim Fits",
      "Sim is the first gate of the flow. FPGA and ASIC only run after the testbench prints RESULT: PASS.\n\nThe build and the run are two separate CHIA tasks on two blocks (Part 0, slide 8): the chipyard block has Verilator 5.022 (from Chipyard's conda env), the verilator block only has the runtime to execute the binary. Both see the same run directory through WORK_DIR.");
    H.flow(s, "Sim", 1.35);
    H.head(s, "Input", 0.85, 2.75, 5.5);
    H.bullets(s, ["rtl/<DUT>.v: the ALU, given and never edited (DUT = NoCG_alu or CG_alu; CG also has rtl/icg_sim.v).",
      "sim/tb_<DUT>.v: self-checking testbench with its own reference model.",
      "Plusargs: +seed=1 +cycles=20000 +en_pct=30 (baseline defaults)."], { x: 0.85, y: 3.15, w: 5.5, h: 2.4, fs: 14 });
    H.head(s, "Output", 6.85, 2.75, 5.6);
    H.bullets(s, ["RESULT line with the number of checks (checked) and errors.",
      "sim/build/sim.log, waveform sim/build/wave.vcd.",
      "results/baseline.json, status.html, CHIA timeline in profile/."], { x: 6.85, y: 3.15, w: 5.6, h: 2.4, fs: 14 });
    H.head(s, "Gate", 0.85, 5.6, 11.6);
    H.para(s, "The run passes when the log contains RESULT: PASS. Otherwise the flow stops here.", { x: 0.85, y: 6.0, w: 11.6, h: 0.5, fs: 14 });
  }

  {
    const s = H.slide("The Files Involved",
      withCmds("run_sim.sh works anywhere Verilator is installed, so it can also be run by hand outside CHIA. SIM=iverilog switches to Icarus Verilog; NO_VCD=1 skips the waveform.\n\nThe testbench drives random operations with en active en_pct % of the time, keeps a queue of expected results from its reference model, and compares every output when valid rises. checked counts those comparisons.\n\nResult line the gate looks for:\nRESULT: PASS dut=NoCG_alu seed=1 cycles=20000 en_pct=30 sent=<n> checked=6272 errors=0",
      ["verilator --binary --timing --trace -Wall -Wno-fatal --top-module tb_NoCG_alu -Mdir build/obj -o Vtb tb_NoCG_alu.v ../rtl/NoCG_alu.v"]));
    H.table(s, [
      ["File", "Role", "Who edits it"],
      ["rtl/<DUT>.v", "8-bit ALU, 8 ops (ADD SUB AND OR XOR SHL SHR SLT), 2-stage pipeline, flags zero / carry / valid", "nobody (locked)"],
      ["sim/tb_<DUT>.v", "Random stimulus, reference model, compares every result, prints the RESULT line", "agent, in phase 2"],
      ["sim/run_sim.sh", "Builds with Verilator and runs; BUILD_ONLY=1 or SKIP_BUILD=1 splits the two halves", "nobody"],
      ["baseline.py", "Defines the CHIA tasks sim_build and sim_run and calls them", "nobody"],
    ], { x: 0.85, y: 1.35, w: 11.65, colW: [2.1, 7.25, 2.3], fs: 12.5, monoCols: [0] });
    H.head(s, "What run_sim.sh does", 0.85, 4.25, 11.6);
    H.hflow(s, [["RTL + testbench", "rtl/<DUT>.v, tb_<DUT>.v"], ["Verilator build", "BUILD_ONLY=1 · chipyard block"], ["Binary Vtb", "in the run folder"],
      ["Run", "SKIP_BUILD=1 · verilator block"], ["sim.log + wave.vcd", "RESULT: PASS / FAIL"]], 4.7, 0.95, { dark: [4] });
    H.caption(s, "The binary is built on one block and run on another; both read the same run folder in WORK_DIR.", 0.85, 5.8, 11.6);
  }

  {
    const s = twoCol(H, "Step 1 · Run the Baseline Sim",
      withCmds("--working-dir . matters: CHIA ships the current folder to every worker, and the cache actor unpickles results by importing these modules.\n\nrun_demo.py submits the same job and also serves the live status page.\n\nVerified: the Sim baseline ran for real on 20/11/2026 for both demos; the full three-step baseline on 21/11/2026. The --stages sim form of run_demo.py was not run separately on the user's machine.",
        ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG baseline --stages sim", "# or directly:", "cd NoCG && chia job submit --working-dir . -- python baseline.py --stages sim --seed 1 --cycles 20000"]),
      "What and why", [
        "The cluster from Part 0 must be up; Sim needs the chipyard and verilator blocks.",
        "Each run gets a new folder WORK_DIR/demo-alu/<demo>/<run_id>/ with a copy of rtl/, sim/, fpga/, asic/. The project folder in the repo is never written to.",
        "baseline.py is the driver: it runs on the head and only calls CHIA tasks.",
        "Sim alone, or Sim followed by FPGA and ASIC, is chosen with --stages.",
      ], "Options of baseline.py");
    H.table(s, [["Option", "Meaning", "Default"], ["--stages", "steps to run", "sim,fpga,asic"], ["--seed", "testbench random seed", "1"],
      ["--cycles", "simulated cycles", "20000"], ["--en-pct", "% of cycles with en = 1", "30"], ["--run-id", "name of the run folder", "date-time"], ["--work-dir", "WORK_DIR root", "~/chia_demo"]],
      { x: s.rx, y: 1.75, w: s.rw, colW: [1.5, 2.85, 1.6], fs: 12.5, monoCols: [0] });
    H.expect(s, "sim_build OK on the chipyard block, sim_run OK on the verilator block, RESULT: PASS, KẾT QUẢ: OK.", s.rx, 5.0, s.rw, 0.8);
  }

  {
    const s = H.slide("Step 2 · What CHIA Does Behind the Command",
      "Each @ChiaFunction declares the resource it needs; .chia_remote() returns a Ray ObjectRef and get() waits for it. The driver calls get() before starting the next task, because with the CHIA profiler on, an ObjectRef passed straight into another task arrives wrapped (_ProfiledResult).\n\nSimplified from baseline.py:\n@ChiaFunction(resources={\"chipyard\": 1})\ndef sim_build(run_dir): _sh(\"./run_sim.sh\", sim, {\"BUILD_ONLY\": \"1\"})\n@ChiaFunction(resources={\"verilator_run\": 1})\ndef sim_run(run_dir, plusargs): ... ok = \"RESULT: PASS\" in log\nok = get(sim_build.chia_remote(run_dir))[\"ok\"]\nif ok: ok = get(sim_run.chia_remote(run_dir, plus))[\"ok\"]\nif ok: get(fpga_build.chia_remote(run_dir))\n\nThe profiler (start_collector) records every task; at the end baseline.py turns the log into profile/ChiaProfileCollector_profile.html with chia viz-profile.");
    H.box(s, "Driver baseline.py", "runs on the head", 0.85, 1.5, 2.6, 0.9, true);
    H.box(s, "sim_build", "chipyard block · Verilator 5.022", 4.3, 1.5, 3.0, 0.9);
    H.box(s, "sim_run", "verilator block · runs Vtb", 4.3, 3.0, 3.0, 0.9);
    H.box(s, "Run folder (WORK_DIR)", "sim/build/Vtb, sim.log, wave.vcd", 8.2, 2.25, 4.3, 0.9);
    H.box(s, "Gate", "RESULT: PASS ?", 4.3, 4.5, 3.0, 0.8);
    H.box(s, "FPGA step", "Part 2", 8.2, 4.5, 4.3, 0.8);
    H.arrow(s, 3.45, 1.95, 4.3, 1.95);
    H.arrow(s, 5.8, 2.4, 5.8, 3.0);
    H.arrow(s, 5.8, 3.9, 5.8, 4.5);
    H.arrow(s, 7.3, 1.95, 8.2, 2.5);
    H.arrow(s, 8.2, 2.9, 7.3, 3.45);
    H.arrow(s, 7.3, 4.9, 8.2, 4.9);
    H.para(s, "writes binary", { x: 7.1, y: 1.75, w: 1.3, h: 0.3, fs: 11, align: "center" });
    H.para(s, "reads binary", { x: 7.1, y: 3.4, w: 1.3, h: 0.3, fs: 11, align: "center" });
    H.para(s, "pass", { x: 7.3, y: 4.55, w: 0.9, h: 0.3, fs: 11, align: "center" });
    H.head(s, "Why split build and run", 0.85, 5.55, 11.6);
    H.bullets(s, ["Verilator lives in Chipyard's conda env, which only the chipyard block mounts; the run needs no compiler, and --verilator-slots N runs several simulations at once.",
      "Both tasks use the same run folder through WORK_DIR, so nothing is copied between blocks."], { x: 0.85, y: 5.95, w: 11.65, h: 0.95, fs: 13.5 });
  }

  {
    const s = twoCol(H, "Step 3 · Read the Result",
      withCmds("Folder layout of a run: rtl/ sim/ fpga/ asic/ (copies), results/, profile/, status.html.\n\nOn Bazzite use /usr/bin/xdg-open (the one on PATH is a distrobox shim), or python run_demo.py show <run_dir>.",
        ["R=~/chia_demo/demo-alu/NoCG/<run_id>", "grep RESULT $R/sim/build/sim.log", "ls -lh $R/sim/build/wave.vcd", "python -m json.tool $R/results/baseline.json | less",
         "/usr/bin/xdg-open $R/profile/ChiaProfileCollector_profile.html"]),
      "What and why", [
        "The RESULT line shows how many comparisons were made (checked) and how many failed (errors).",
        "results/baseline.json keeps every number of the run; phase 2 uses it as the reference.",
        "status.html shows the three steps, the task that is running and on which block.",
        "The waveform can be opened in any VCD viewer, for example GTKWave.",
      ], "Outputs of a run");
    H.table(s, [["Output", "Where (in the run folder)"], ["RESULT line", "sim/build/sim.log"], ["Waveform", "sim/build/wave.vcd"],
      ["All numbers", "results/baseline.json"], ["Live status", "status.html"], ["Task timeline", "profile/ChiaProfileCollector_profile.html"]],
      { x: s.rx, y: 1.75, w: s.rw, colW: [1.8, 4.15], fs: 12.5 });
    H.expect(s, "Baseline, both demos: RESULT: PASS, checked = 6272, errors = 0.", s.rx, 4.4, s.rw, 0.55);
  }

  {
    const s = H.slide("Step 4 · Let the Agent Improve the Testbench",
      withCmds("agent.py takes the newest baseline with KẾT QUẢ: OK as the starting point (or --baseline <run_dir>). --steps sim restricts phase 2 to this step.\n\nThe prompt tells the agent: keep the plusargs, the RESULT format and the counters; the RTL is the reference, so a new check that fails rejects the round.\n\nIf the agent changes nothing (it judges there is nothing worth adding), the step ends early.\n\nVerified: full agent runs (all steps, --rounds 2) on 21/11/2026 for both demos. --steps sim alone was not run separately.",
        ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG agent --steps sim --rounds 2"]));
    H.loop(s, 1.4, "sim/tb_<DUT>.v only", "checked must rise");
    H.head(s, "Why only the testbench", 0.85, 2.75, 5.6);
    H.bullets(s, ["The RTL is the thing being tested. If the agent could edit it, a passing test would prove nothing.",
      "Any other edited file is restored before CHIA measures.",
      "The agent runs in the claude block and only sees the copy of the tree for its round."], { x: 0.85, y: 3.15, w: 5.6, h: 3.4, fs: 14 });
    H.head(s, "Gate and rule", 6.85, 2.75, 5.6);
    H.table(s, [["", "Sim"], ["Gate", "Verilator build + run with the baseline plusargs prints RESULT: PASS"],
      ["Accept", "checked strictly greater than the current best"], ["Rounds", "up to 2 per step"], ["Runs on", "chipyard + verilator blocks (agent on claude)"]],
      { x: 6.85, y: 3.15, w: 5.65, colW: [1.3, 4.35], fs: 12.5, boldFirstCol: true });
  }

  {
    const s = twoCol(H, "Step 5 · Inspect a Round",
      withCmds("NHẬN = accepted, LOẠI = rejected (the console messages of the scripts are in Vietnamese).\n\nRejected rounds are slimmed: binaries, VCD, bitstream, checkpoints and GDS are deleted to save disk; the sources, logs and agent folder stay.",
        ["A=~/chia_demo/demo-alu/NoCG/<agent_run_id>", "ls $A/sim-r1/agent", "cat $A/sim-r1/agent/summary.md", "less $A/sim-r1/agent/changes.diff", "cat $A/results/compare.md"]),
      "What and why", [
        "Each round has its own folder <step>-r<k>/ with a full copy of the tree, so a rejected round never touches the best version.",
        "Everything the agent saw and did is kept, so a round can be explained afterwards.",
        "At the end (phase 3) a baseline vs agent table is written for the whole run.",
      ], "Files kept for each round");
    H.table(s, [["File", "Content"], ["agent/TASK.md", "exact prompt given to Claude"], ["agent/context/", "reports the agent read"],
      ["agent/summary.md", "agent's own short explanation"], ["agent/changes.diff", "edits kept after CHIA reverted out-of-scope ones"],
      ["agent/stream.log", "full session log"], ["results/compare.md", "baseline vs agent, whole run"]],
      { x: s.rx, y: 1.75, w: s.rw, colW: [2.2, 3.75], fs: 12.5, monoCols: [0] });
    H.expect(s, "Console during the run: sim-r1: NHẬN (checked 6272 → ...), or LOẠI with the reason.", s.rx, 4.85, s.rw, 0.55);
  }

  // Ví dụ 1
  {
    const s = H.slide("Example · NoCG vs CG: Same Test, Different Clocking",
      "Both demos use the same port list and the same testbench structure, so the baseline gives identical counts. The difference is inside: CG drives the register clocks through module icg (rtl/icg_sim.v), a latch that is transparent while clk = 0 and an AND gate, so the gated clock has no glitches.\n\nNoCG register: always @(posedge clk) if (en) a_q <= a;  (feedback mux)\nCG register: icg u_icg_in (.clk(clk), .en(en), .test_en(1'b0), .gclk(gclk_in)); always @(posedge gclk_in) a_q <= a;  (no mux)");
    H.head(s, "NoCG: clock enable", 0.85, 1.3, 5.6);
    H.box(s, "a, b", null, 0.85, 1.75, 1.0, 0.6);
    H.box(s, "MUX", "en ? a : a_q", 2.25, 1.75, 1.5, 0.6);
    H.box(s, "FF  a_q", "clocked by clk", 4.15, 1.75, 2.3, 1.3, true);
    H.box(s, "clk", null, 0.85, 2.45, 1.0, 0.6);
    H.arrow(s, 1.85, 2.05, 2.25, 2.05); H.arrow(s, 3.75, 2.05, 4.15, 2.05); H.arrow(s, 1.85, 2.75, 4.15, 2.75);
    H.head(s, "CG: gated clock", 6.85, 1.3, 5.6);
    H.box(s, "clk, en", null, 6.85, 1.75, 1.2, 0.6);
    H.box(s, "ICG", "latch + AND", 8.45, 1.75, 1.7, 0.6);
    H.box(s, "FF  a_q", "clocked by gclk_in, no mux", 10.55, 1.75, 1.95, 1.3, true);
    H.box(s, "a, b", null, 6.85, 2.45, 1.2, 0.6);
    H.arrow(s, 8.05, 2.05, 8.45, 2.05); H.arrow(s, 10.15, 2.05, 10.55, 2.05); H.arrow(s, 8.05, 2.75, 10.55, 2.75);
    H.caption(s, "en = 0: the mux feeds a_q back", 0.85, 3.15, 5.6);
    H.caption(s, "en = 0: the clock stops, so the flip-flop holds", 6.85, 3.15, 5.6);
    H.head(s, "Baseline Sim result", 0.85, 3.6, 11.6);
    H.table(s, [["", "NoCG", "CG"], ["Result", "PASS", "PASS"], ["checked / errors", "6 272 / 0", "6 272 / 0"],
      ["Simulator", "Verilator 5.022 (chipyard block)", "Verilator 5.022 (chipyard block)"], ["ICG model in sim", "-", "rtl/icg_sim.v: latch + AND"]],
      { x: 0.85, y: 4.0, w: 11.65, colW: [3.0, 4.325, 4.325], fs: 13, boldFirstCol: true });
    H.caption(s, "Sim cannot see the cost of clock gating; that only shows up on FPGA and ASIC (Parts 2 and 3).", 0.85, 6.3, 11.6);
  }

  // Ví dụ 2
  {
    const s = H.slide("Example · What the Agent Added",
      "Run IDs: NoCG agent-20261121-141430, CG agent-20261121-143619. All four Sim rounds were accepted and still pass on the real Verilator.\n\nThe cost: longer simulation and a default VCD of about 90 MB. NO_VCD=1 in run_sim.sh skips the waveform.");
    H.table(s, [["", "NoCG", "CG"], ["Baseline checked", "6 272", "6 272"], ["After r1 + r2", "542 046  (×86)", "1 509 090  (×241)"], ["Still PASS", "yes", "yes"]],
      { x: 0.85, y: 1.35, w: 6.2, colW: [2.2, 2.0, 2.0], fs: 13, boldFirstCol: true });
    H.head(s, "Round 1: what was not checked", 0.85, 3.25, 6.2);
    H.bullets(s, ["valid arrives exactly 2 cycles after en", "edge values around 7F/80 and FF/00", "shifts with b[7:3] ≠ 0",
      "en patterns: back-to-back, alternating, long idle with outputs held", "asynchronous reset in the middle of a run"], { x: 0.85, y: 3.65, w: 6.2, h: 2.6, fs: 13.5 });
    H.head(s, "Round 2: exhaustive", 7.35, 1.35, 5.1);
    H.bullets(s, ["ADD, SUB and SLT over all 256 × 256 operand pairs.", "Counts the hold checks that were already running but not counted."], { x: 7.35, y: 1.75, w: 5.1, h: 1.4, fs: 13.5 });
    H.head(s, "CG only: glitch check on the gated clock", 7.35, 3.25, 5.1);
    H.bullets(s, ["Counts edges of gclk_in and gclk_out every cycle: exactly 1 when en = 1, 0 when idle, also when en changes while clk = 1.",
      "This tests directly that the ICG latch never makes a spurious pulse, the main risk of clock gating."], { x: 7.35, y: 3.65, w: 5.1, h: 2.6, fs: 13.5 });
  }

  {
    const s = H.slide("Takeaways and Limits",
      "Coverage could be measured with Verilator's --coverage and a coverage report; that is not part of the demo.");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["CHIA splits one step over two blocks and gates the rest of the flow on its result.",
      "The agent found real gaps in the testbench and closed them; Verilator confirmed every new check.",
      "On the CG design the agent added a check specific to clock gating without being told to."], { x: 0.85, y: 1.7, w: 5.6, h: 3.5, fs: 14.5 });
    H.head(s, "Limits", 6.85, 1.3, 5.6);
    H.bullets(s, ["checked counts comparisons, not coverage. A larger count does not prove that more behaviour is covered.",
      "Bigger tests cost time and disk (VCD ≈ 90 MB).",
      "Two rounds per step and one run per configuration; the agent is not deterministic, so a rerun can propose different checks."], { x: 6.85, y: 1.7, w: 5.6, h: 3.5, fs: 14.5 });
    H.head(s, "Next", 0.85, 5.4, 11.6);
    H.para(s, "Part 2: the same ALU goes through Vivado to a bitstream, inside a self-checking FPGA wrapper.", { x: 0.85, y: 5.8, w: 11.6, h: 0.5, fs: 14.5 });
  }
  await H.save("1_Sim.pptx");
}

// ====================================================================================
// 2. FPGA
// ====================================================================================
async function fpgaDeck() {
  const H = deck("Part 2 · FPGA", "Part 2: FPGA with Vivado");
  H.cover("PART 2 · FPGA", "Step 2 of the Flow:", "FPGA with Vivado 2025.1",
    "Self-checking wrapper, synthesis to bitstream on the vivado block, then agent rounds under a strict rule");

  {
    const s = H.slide("Where FPGA Fits",
      "The ALU alone has about 30 I/O pins and no way to show a result on a board. fpga_top makes it self-contained: a 32-bit Galois LFSR sends an operation in about 30 % of cycles, a 32-bit MISR folds every result into a signature, and at the end of each frame the signature is compared with EXP_SIG computed by golden_sig.py, an independent Python model.\n\nOnly clk, a reset button and 4 LEDs leave the chip, so changing boards means editing only the .xdc. No board was available yet, so the flow stops at the bitstream.");
    H.flow(s, "FPGA", 1.35);
    H.head(s, "Self-checking wrapper fpga/fpga_top.v", 0.85, 2.75, 11.6);
    const y = 3.25;
    H.box(s, "LFSR", "random op, a, b, en (~30 %)", 0.85, y, 2.3, 0.9);
    H.arrow(s, 3.15, y + 0.45, 3.75, y + 0.45);
    H.box(s, "ALU (rtl/<DUT>.v)", "unchanged", 3.75, y, 2.6, 0.9, true);
    H.arrow(s, 6.35, y + 0.45, 6.95, y + 0.45);
    H.box(s, "MISR", "32-bit signature of all results", 6.95, y, 2.5, 0.9);
    H.arrow(s, 9.45, y + 0.45, 10.05, y + 0.45);
    H.box(s, "4 LEDs", "heartbeat · PASS · FAIL · frame done", 10.05, y, 2.45, 0.9);
    H.head(s, "Target", 0.85, 4.55, 5.6);
    H.bullets(s, ["Part xc7a100tcsg324-1 (Arty A7-100T), 100 MHz clock from the .xdc.", "Vivado 2025.1 in batch mode, non-project flow."], { x: 0.85, y: 4.95, w: 5.6, h: 1.4, fs: 14 });
    H.head(s, "Gate", 6.85, 4.55, 5.6);
    H.bullets(s, ["Bitstream written (out/fpga_top.bit).", "WNS ≥ 0 (timing met at 100 MHz).", "Phase 2 adds a functional check in xsim (step 3)."], { x: 6.85, y: 4.95, w: 5.6, h: 1.6, fs: 14 });
  }

  {
    const s = H.slide("The Files Involved",
      "sources.f is read by both build.tcl and the xsim check of phase 2, so the build and the functional test always use the same list of files.\n\nbuild.tcl (baseline, Vivado defaults): read_verilog $srcs; read_xdc arty_a7_100t.xdc; synth_design -top fpga_top -part xc7a100tcsg324-1; opt_design; place_design; route_design; report_timing_summary / report_utilization / report_power ...; write_bitstream out/fpga_top.bit\n\nIf N_CYCLES or SEED in fpga_top.v changes, EXP_SIG must be recomputed: python3 golden_sig.py 1048576 ACE12025 (fpga_top, EXP_SIG = EACBE87B) or python3 golden_sig.py 4096 ACE12025 (tb_fpga_top, AC066D58).");
    H.table(s, [
      ["File (in fpga/)", "Role", "Agent may edit"],
      ["fpga_top.v", "Wrapper: LFSR stimulus, ALU, MISR, LEDs", "no"],
      ["arty_a7_100t.xdc", "Pins, 100 MHz clock", "no"],
      ["build.tcl", "Vivado batch script: synth → opt → place → route → reports → bitstream", "yes (options, directives)"],
      ["sources.f", "List of Verilog files for Vivado and xsim", "yes"],
      ["<new>.v", "FPGA-only version of a module, same name and ports", "yes (new files only)"],
      ["tb_fpga_top.v", "xsim test: 4096-cycle frame, expects signature AC066D58", "no"],
      ["golden_sig.py", "Python model that computes the expected signatures", "no"],
    ], { x: 0.85, y: 1.35, w: 11.65, colW: [2.6, 6.55, 2.5], fs: 12.5, monoCols: [0] });
    H.head(s, "build.tcl flow (baseline: Vivado defaults)", 0.85, 5.0, 11.6);
    H.hflow(s, [["read", "sources.f + .xdc"], ["synth", "synth_design"], ["opt", "opt_design"], ["place", "place_design"], ["route", "route_design"],
      ["reports", "timing, util, power"], ["bitstream", "fpga_top.bit"]], 5.45, 0.85, { g: 0.25, dark: [6], fs: 12 });
  }

  {
    const s = twoCol(H, "Step 1 · Run the Baseline FPGA Build",
      withCmds("The task reads the numbers from reports/timing.rpt, util.rpt and power.rpt, and counts WARNING / CRITICAL WARNING lines (with their IDs) in out/vivado.log; the warning IDs are given to the agent in phase 2.\n\nVerified: FPGA baseline for both demos on 21/11/2026 (run twice, identical numbers). The --stages sim,fpga form was not run separately.",
        ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG baseline --stages sim,fpga", "# what fpga_build runs on the vivado block:",
         "cd <run_dir>/fpga && vivado -mode batch -nojournal -log out/vivado.log -source build.tcl"]),
      "What and why", [
        "Needs the vivado block: the cluster is brought up with --fpga (Part 0, slide 8).",
        "The Sim gate stays in front; Vivado only starts after RESULT: PASS.",
        "fpga_build runs Vivado in batch inside the run folder's copy of fpga/, then parses the reports.",
        "About 64 s per demo on the user's machine.",
      ], "Tasks in order");
    H.table(s, [["Task", "Block", "Does"], ["sim_build, sim_run", "chipyard, verilator", "Sim gate (Part 1)"],
      ["fpga_build", "vivado", "build.tcl → bitstream, reports"], ["(driver)", "head", "reads numbers, checks WNS ≥ 0"]],
      { x: s.rx, y: 1.75, w: s.rw, colW: [1.95, 1.9, 2.1], fs: 12.5, monoCols: [0] });
    H.expect(s, "fpga OK, then LUT, FF, BUFG, WNS, WHS, power, warnings and the bitstream path.", s.rx, 3.75, s.rw, 0.75);
  }

  {
    const s = twoCol(H, "Step 2 · Read the Reports",
      withCmds("Full list in reports/: post_synth_util.rpt, util.rpt, util_hier.rpt, timing.rpt, clock_util.rpt, power.rpt, methodology.rpt, drc.rpt. Checkpoint: out/post_route.dcp.",
        ["F=~/chia_demo/demo-alu/NoCG/<run_id>/fpga", "ls $F/reports", "grep -A6 'Design Timing Summary' $F/reports/timing.rpt",
         "grep -E 'Slice LUTs|Slice Registers' $F/reports/util.rpt", "grep -E 'Total On-Chip|Dynamic' $F/reports/power.rpt", "ls -l $F/out/fpga_top.bit"]),
      "What and why", [
        "WNS ≥ 0 is the gate; the other numbers are what the agent tries to improve.",
        "Power is Vivado's vector-less estimate, not from simulation activity.",
        "methodology.rpt and the warning list are where the CG design gets flagged.",
        "Numbers include the wrapper (LFSR, MISR, counters), the same for both demos.",
      ], "Report → metric");
    H.table(s, [["Report", "Metric", "NoCG baseline"], ["timing.rpt", "WNS / WHS at 100 MHz", "4.579 / 0.024 ns"], ["util.rpt", "LUT / FF", "138 / 139"],
      ["util_hier.rpt", "same, per module", "wrapper vs ALU"], ["power.rpt", "total / dynamic", "0.099 / 0.002 W"], ["vivado.log", "warnings", "1"]],
      { x: s.rx, y: 1.75, w: s.rw, colW: [1.75, 2.2, 2.0], fs: 12.5, monoCols: [0] });
  }

  {
    const s = H.slide("Step 3 · Let the Agent Try",
      withCmds("fpga_sim (vivado block): rm -rf xsim.dir && xvlog tb_fpga_top.v <sources.f> $XILINX_VIVADO/data/verilog/src/glbl.v && xelab ... && xsim tbsim -R. UNISIM lets Xilinx primitives such as BUFGCE or LUT6_2 simulate, so the agent can use them.\n\nTolerances only skip rounding: the tools are deterministic (two baseline runs gave identical numbers).\n\nVerified: full agent runs on 21/11/2026 for both demos. --steps fpga alone was not run separately; point --baseline at a baseline that includes FPGA.",
        ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG agent --steps fpga --rounds 2 --baseline <baseline_run_dir>"]));
    H.loop(s, 1.4, "build.tcl, sources.f, new fpga/*.v", "strict Pareto");
    H.head(s, "Why these files only", 0.85, 2.75, 5.6);
    H.bullets(s, ["rtl/ is shared with ASIC. An FPGA trick written into new files under fpga/ cannot leak into the ASIC branch.",
      "Part, clock, .xdc and the checker stay fixed, so before and after measure the same thing.",
      "A functional check in xsim runs before the build, so a fast but wrong design is rejected."], { x: 0.85, y: 3.15, w: 5.6, h: 3.4, fs: 13.5 });
    H.head(s, "Gate and rule", 6.85, 2.75, 5.6);
    H.table(s, [["", "FPGA"], ["Gate", "tb_fpga_top PASS in xsim (UNISIM) · bitstream · WNS ≥ 0"],
      ["Accept", "no worse in WNS (±0.02 ns), dynamic power (±0.5 mW) and LUT+FF (±0.5) and better in at least one"],
      ["Rounds", "up to 2 per step"], ["Runs on", "vivado block (agent on claude)"]], { x: 6.85, y: 3.15, w: 5.65, colW: [1.3, 4.35], fs: 12.5, boldFirstCol: true });
  }

  // Ví dụ 1
  {
    const s = H.slide("Example · Baseline: Clock Gating Hurts on FPGA",
      "Vivado builds the behavioural icg model as a latch plus a LUT that drives the register clock. The path through it has only half a cycle, so WNS drops by about 3 ns; the extra logic on the clock adds dynamic power, and Vivado reports gated-clock warnings (for example PDRC-153).\n\nThe ASIC result goes the other way (Part 3), which is why FPGA-specific fixes must stay out of rtl/.");
    H.table(s, [["Baseline (incl. wrapper)", "NoCG", "CG"], ["LUT / FF / BUFG", "138 / 139 / 1", "140 / 141 / 1"], ["WNS / WHS (ns)", "4.579 / 0.024", "1.502 / 0.052"],
      ["Total / dynamic power (W)", "0.099 / 0.002", "0.102 / 0.005"], ["Vivado warnings", "1", "4"], ["Gate", "pass", "pass"]],
      { x: 0.85, y: 1.35, w: 6.6, colW: [2.8, 1.9, 1.9], fs: 13.5, boldFirstCol: true });
    H.head(s, "Why CG is worse here", 7.8, 1.35, 4.7);
    H.bullets(s, ["The FPGA clock network expects clock enables on the flip-flops, not gated clocks built from logic.",
      "Latch + LUT on the clock path → half-cycle critical path: WNS 4.579 → 1.502 ns.",
      "Dynamic power 2 → 5 mW; 3 more warnings.",
      "Still passes the gate, so it is a valid baseline for the agent to improve."], { x: 7.8, y: 1.75, w: 4.7, h: 4.6, fs: 14 });
  }

  // Ví dụ 2
  {
    const s = H.slide("Example · Four Agent Rounds, None Accepted",
      "Before submitting, the agent checked its own logic without Vivado: a Python model compared exhaustively over 524 288 op × a × b combinations (0 errors); in CG r2 it also simulated fpga_top cycle by cycle and matched the MISR signature AC066D58. Its resource and timing predictions were still wrong in part; only the real build tells.\n\nIn NoCG r2 the agent read why r1 was rejected (dynamic power rose because the ALU toggled every cycle with the LFSR) and went back to keeping the input registers.");
    H.table(s, [["Round", "What the agent did", "Measured", "Result"],
      ["NoCG r1", "Retiming (ALU before stage 1, store 9-bit result), hand-mapped LUT6_2 / CARRY4, ExtraTimingOpt / Explore", "WNS 4.579 → 4.94; P dyn 2 → 5 mW; LUT+FF 277 → 302", "rejected"],
      ["NoCG r2", "Keep input registers, one shared adder for ADD / SUB / SLT", "LUT+FF 277 → 262; WNS 4.579 → 4.212", "rejected"],
      ["CG r1", "icg_fpga.v = AND gate for synthesis + -gated_clock_conversion auto; build.tcl stops if a latch or LUT drives a clock", "WNS 1.502 → 4.499; P dyn 5 → 2 mW; LUT+FF 281 → 284", "rejected"],
      ["CG r2", "CG_alu_fpga.v with clock enables (no latch, no LUT on the clock) + retiming", "WNS 1.502 → 4.436; LUT+FF 281 → 312", "rejected"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.15, 5.3, 3.9, 1.3], fs: 12, boldFirstCol: true });
    H.caption(s, "Every round made at least one metric worse than the baseline, so CHIA kept the baseline for both demos.", 0.85, 5.15, 11.6);
    H.head(s, "CG r1: right diagnosis, blocked by the rule", 0.85, 5.55, 11.6);
    H.para(s, "WNS ×3 and dynamic power −60 % for +3 LUT+FF (+1.1 %). The strict rule was fixed before the run and was kept. A timing-first rule (\"WNS is the main metric, resources may grow a few %\") would accept it, but changing the rule means rerunning.",
      { x: 0.85, y: 5.95, w: 11.65, h: 0.9, fs: 13.5 });
  }

  {
    const s = H.slide("Takeaways and Limits",
      "Power on FPGA is Vivado's vector-less estimate; using the VCD from Sim (SAIF) would give activity-based numbers.");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["The same RTL that passed Sim builds to a bitstream on the vivado block, with a built-in self-check.",
      "The agent reads real Vivado reports and proposes concrete changes; xsim with UNISIM checks them functionally before the build.",
      "The gate does its job: no round is accepted unless it is at least as good on every metric."], { x: 0.85, y: 1.7, w: 5.6, h: 3.6, fs: 14.5 });
    H.head(s, "Limits", 6.85, 1.3, 5.6);
    H.bullets(s, ["The strict Pareto rule rejects good trade-offs such as CG r1. Choosing the rule is a design decision made before running.",
      "Power is a vector-less estimate.", "No board yet: the flow ends at the bitstream."], { x: 6.85, y: 1.7, w: 5.6, h: 3.6, fs: 14.5 });
    H.head(s, "Next", 0.85, 5.4, 11.6);
    H.para(s, "Part 3: the same RTL goes through Yosys and OpenROAD to a GDS on sky130hd, where clock gating pays off.", { x: 0.85, y: 5.8, w: 11.6, h: 0.5, fs: 14.5 });
  }
  await H.save("2_FPGA.pptx");
}

// ====================================================================================
// 3. ASIC
// ====================================================================================
async function asicDeck() {
  const H = deck("Part 3 · ASIC", "Part 3: ASIC with Yosys and OpenROAD");
  H.cover("PART 3 · ASIC", "Step 3 of the Flow:", "RTL to GDS with Yosys + OpenROAD",
    "OpenROAD-flow-scripts on sky130hd, six CHIA tasks on the asic block, then the agent tunes config.mk");

  {
    const s = H.slide("Where ASIC Fits",
      "Each ORFS stage is its own CHIA task, so the timeline shows exactly how long synth, floorplan, place, CTS, route and finish take, and a failure stops at the stage that failed.\n\nWORK_HOME points into the run folder, so ORFS on the host is never written to and every run keeps its own logs, reports and results.");
    H.flow(s, "ASIC", 1.35);
    H.head(s, "Six stages, six tasks on the asic block", 0.85, 2.75, 11.6);
    const st = [["synth", "Yosys + ABC"], ["floorplan", "die, core, I/O"], ["place", "standard cells"], ["cts", "clock tree"], ["route", "global + detailed"], ["finish", "GDS, reports, picture"]];
    st.forEach(([a, b], i) => {
      const x = 0.85 + i * 1.98;
      H.box(s, a, b, x, 3.2, 1.65, 0.8, i === 5);
      if (i < 5) H.arrow(s, x + 1.65, 3.6, x + 1.98, 3.6);
    });
    H.head(s, "Setup", 0.85, 4.35, 5.6);
    H.bullets(s, ["Yosys 0.68 + OpenROAD (ORFS), PDK sky130hd.", "10 ns clock (same 100 MHz as FPGA), I/O delays 20 % of the period.", "About 2.5 min per demo."], { x: 0.85, y: 4.75, w: 5.6, h: 1.8, fs: 14 });
    H.head(s, "Gate", 6.85, 4.35, 5.6);
    H.bullets(s, ["GDS written (6_final.gds).", "Setup and hold slack ≥ 0.", "DRC errors = 0."], { x: 6.85, y: 4.75, w: 5.6, h: 1.8, fs: 14 });
  }

  {
    const s = H.slide("The Files Involved",
      "config.mk computes its paths from its own location (DEMO_ASIC_DIR := $(dir $(DESIGN_CONFIG))), so the whole run folder can be copied anywhere and still builds.\n\nThe CG demo reads asic/icg_sky130.v instead of rtl/icg_sim.v: the icg module becomes the sky130 cell sky130_fd_sc_hd__dlclkp_1 (latch + AND, glitch-free), and Yosys keeps it as is because it is in the liberty file.");
    H.table(s, [["File (in asic/)", "Role", "Agent may edit"],
      ["config.mk", "ORFS variables: platform, design, sources, SDC, utilization, density ...", "yes (tuning only)"],
      ["constraint.sdc", "10 ns clock, I/O delays", "no"],
      ["icg_sky130.v (CG)", "icg → sky130_fd_sc_hd__dlclkp_1", "no"],
      ["render_gds.py", "KLayout script: GDS → layout.png", "no"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [2.6, 6.55, 2.5], fs: 12.5, monoCols: [0] });
    H.head(s, "Baseline config.mk (close to ORFS's gcd example)", 0.85, 3.65, 11.6);
    H.table(s, [["Variable", "NoCG", "CG"], ["PLATFORM", "sky130hd", "sky130hd"], ["DESIGN_NAME", "NoCG_alu", "CG_alu"],
      ["VERILOG_FILES", "rtl/NoCG_alu.v", "rtl/CG_alu.v + asic/icg_sky130.v"], ["SDC_FILE", "constraint.sdc (10 ns)", "constraint.sdc (10 ns)"],
      ["CORE_UTILIZATION", "40", "40"], ["PLACE_DENSITY", "0.65", "0.65"]],
      { x: 0.85, y: 4.05, w: 11.65, colW: [3.0, 3.8, 4.85], fs: 12.5, monoCols: [0] });
  }

  {
    const s = twoCol(H, "Step 1 · Run the Baseline ASIC Flow",
      withCmds("If the ORFS checkout ignores WORK_HOME, the synth task fails on purpose with a message, so nothing is silently written into the host ORFS tree.\n\nThe asic container has a RAM limit (ASIC_MEM, default 12g); a flow over the limit is OOM-killed without affecting the machine.\n\nVerified: ASIC baseline for both demos on 21/11/2026 (run twice, identical numbers, including route time).",
        ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG baseline", "# what each asic_stage task runs (stage = synth ... finish):",
         "make -C /OpenROAD-flow-scripts/flow DESIGN_CONFIG=<run_dir>/asic/config.mk WORK_HOME=<run_dir>/asic/orfs <stage>"]),
      "What and why", [
        "Needs the asic block: the cluster is brought up with --asic (Part 0, slide 8).",
        "The full baseline runs Sim → FPGA → ASIC; --stages sim,asic skips Vivado.",
        "Each stage is one make call of ORFS on the asic block, with its log in asic/make/<stage>.log.",
        "finish also reads the metrics and draws asic/layout.png with KLayout.",
      ], "One task per stage");
    H.vflow(s, [["asic_stage synth", "netlist"], ["asic_stage floorplan", "die, core, I/O, power grid"], ["asic_stage place", "placed cells"],
      ["asic_stage cts", "clock tree"], ["asic_stage route", "wires, DRC"], ["asic_stage finish", "6_final.gds, metrics, layout.png"]],
      s.rx, 1.75, s.rw, 0.52, 0.2, { dark: [5], fs: 12 });
    H.expect(s, "Six stages OK, then area, cells, slack, power, DRC.", s.rx, 6.15, s.rw, 0.45);
  }

  {
    const s = twoCol(H, "Step 2 · Read the Results",
      withCmds("6_final.v is the final netlist; counting cell types gives flops, ICG cells and clock buffers. The GDS itself can be opened in KLayout.",
        ["O=~/chia_demo/demo-alu/NoCG/<run_id>/asic", "ls $O/orfs/results/sky130hd/NoCG_alu/base", "less $O/orfs/reports/sky130hd/NoCG_alu/base/6_finish.rpt",
         "ls $O/orfs/logs/sky130hd/NoCG_alu/base/*.json", "/usr/bin/xdg-open $O/layout.png", "klayout <...>/6_final.gds"]),
      "What and why", [
        "The gate reads setup / hold slack and DRC; area and power are what the agent tries to improve.",
        "Power is OpenSTA's estimate with default activity, not from the Sim VCD.",
        "All results of a run stay inside the run folder (WORK_HOME), not in the host ORFS tree.",
      ], "Result → source");
    H.table(s, [["Result", "Source", "NoCG baseline"], ["Setup / hold slack", "6_finish.rpt", "6.815 / 0.493 ns"], ["Power", "6_finish.rpt", "0.368 mW"],
      ["Cell / die area", "logs/*.json", "3 078 / 7 534 µm²"], ["Cells", "6_final.v", "356"], ["DRC errors", "route reports", "0"], ["Picture", "layout.png", "slide 9"]],
      { x: s.rx, y: 1.75, w: s.rw, colW: [1.95, 1.85, 2.15], fs: 12.5 });
  }

  {
    const s = H.slide("Step 3 · Let the Agent Tune config.mk",
      withCmds("The prompt lists the ORFS variables the agent may use (CORE_UTILIZATION, PLACE_DENSITY, ABC_AREA, synthesis, repair and CTS options) and warns that too high a utilization fails place or route, which rejects the round.\n\nCHIA checks the rules on the edited config.mk before measuring: PLATFORM, DESIGN_NAME, DESIGN_NICKNAME, VERILOG_FILES and SDC_FILE must be unchanged.\n\nVerified: full agent runs on 21/11/2026 for both demos. --steps asic alone was not run separately.",
        ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG agent --steps asic --rounds 2 --baseline <baseline_run_dir>"]));
    H.loop(s, 1.4, "asic/config.mk only", "1 % rule");
    H.head(s, "Why config.mk only", 0.85, 2.75, 5.6);
    H.bullets(s, ["config.mk says how to build, not what to build. The design, sources and SDC are the thing being measured.",
      "Same RTL, PDK and 10 ns clock as the baseline, so the numbers compare.",
      "Locked variables are checked before the six stages run."], { x: 0.85, y: 3.15, w: 5.6, h: 3.4, fs: 13.5 });
    H.head(s, "Gate and rule", 6.85, 2.75, 5.6);
    H.table(s, [["", "ASIC"], ["Gate", "GDS · setup / hold slack ≥ 0 · DRC = 0"],
      ["Accept", "cell area, die area and power each no more than 1 % worse, and at least one more than 1 % better"],
      ["Rounds", "up to 2 per step"], ["Runs on", "asic block (agent on claude)"]], { x: 6.85, y: 3.15, w: 5.65, colW: [1.3, 4.35], fs: 12.5, boldFirstCol: true });
  }

  // Ví dụ 1
  {
    const s = H.slide("Example · Baseline: Clock Gating Pays Off on ASIC",
      "OpenROAD's CTS handled the ICG cells correctly; this was the main risk listed before the demo.\n\nRoute took 113 s for CG against 35 s for NoCG, the same on both baseline runs. The reason was not investigated.");
    H.table(s, [["Baseline", "NoCG", "CG"], ["Cell area (µm²), util", "3 078, 44.8 %", "2 975, 46.9 %"], ["Die area (µm²)", "7 534", "6 926"],
      ["Cells (flops, ICG, clock buffers)", "356 (31, 0, 9)", "306 (31, 2, 18)"], ["Setup / hold slack (ns)", "6.815 / 0.493", "6.957 / 0.406"],
      ["Power (mW)", "0.368", "0.324"], ["DRC", "0", "0"]], { x: 0.85, y: 1.35, w: 6.9, colW: [3.1, 1.9, 1.9], fs: 13.5, boldFirstCol: true });
    H.head(s, "Why CG is better here", 8.05, 1.35, 4.45);
    H.bullets(s, ["The ICG replaces the feedback mux in front of every input and output register: 50 fewer cells.",
      "Cell area −3 %, power −12 %.", "Cost: twice as many clock buffers (9 → 18).",
      "The opposite of FPGA (Part 2). This is why FPGA fixes stay out of rtl/."], { x: 8.05, y: 1.75, w: 4.45, h: 4.6, fs: 14 });
  }

  // Ví dụ 2
  {
    const s = H.slide("Example · What the Agent Changed",
      "All four rounds passed the gate (GDS, slack ≥ 0, DRC 0) and setup slack even rose slightly.\n\nCell count went up 26-31 % although area went down: ABC in area mode uses more, smaller cells.\n\nNoCG r2 was accepted although power rose 0.4 %: that is inside the 1 % tolerance of the rule.\n\nThe agent noted its own limit: in NoCG about 70 % of the power is flip-flops and the clock tree, set by the RTL, which config.mk cannot change.");
    H.table(s, [["Round", "NoCG", "CG"],
      ["r1", "ABC_AREA=1 (lots of timing slack, so synthesize for area); CORE_UTILIZATION 40 → 55; PLACE_DENSITY 0.65 → 0.75", "ABC_AREA=1 only: the earlier route needed 6 iterations to clear DRC, so it stayed careful"],
      ["r2", "CORE_UTILIZATION 55 → 65; PLACE_DENSITY 0.75 → 0.85", "CORE_UTILIZATION 40 → 50; DONT_BUFFER_PORTS=1 (about 33 I/O buffers removed)"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [0.9, 5.375, 5.375], fs: 12.5, boldFirstCol: true });
    H.table(s, [["Baseline → agent", "NoCG", "CG"], ["Cell area (µm²)", "3 077.95 → 3 021.65 (−1.8 %)", "2 975.35 → 2 835.22 (−4.7 %)"],
      ["Die area (µm²)", "7 534.24 → 4 641.70 (−38.4 %)", "6 925.57 → 5 504.90 (−20.5 %)"], ["Util", "44.8 % → 73.9 %", "46.9 % → 58.1 %"],
      ["Power (mW)", "0.368 → 0.361 (−1.8 %)", "0.324 → 0.302 (−6.8 %)"], ["Setup / hold slack (ns)", "6.815 → 7.034 / 0.493 → 0.481", "6.957 → 7.042 / 0.406 → 0.404"],
      ["Cells, DRC", "356 → 468, 0", "306 → 386, 0"]], { x: 0.85, y: 3.55, w: 11.65, colW: [2.85, 4.4, 4.4], fs: 12.5, boldFirstCol: true });
  }

  // Ví dụ 3: layout
  {
    const s = H.slide("Example · Layouts, Same Scale",
      "render_gds.py fits each GDS to a 1600 × 1600 picture, so the raw images all have the same size whatever the die. Here each picture is scaled by the square root of its die-area ratio to the NoCG baseline's frame size, so the sizes on the slide compare.\n\nImages: report/img/*_layout.png (from asic/layout.png of the baseline and of the accepted asic-r2 round).");
    const S = 2.45;
    [["NoCG_baseline", 0.85, 1, "NoCG baseline · 7 534 µm²"], ["NoCG_agent", 3.85, Math.sqrt(4641.70 / 7534.24), "NoCG agent · 4 642 µm²"],
     ["CG_baseline", 6.85, Math.sqrt(6925.57 / 7534.24), "CG baseline · 6 926 µm²"], ["CG_agent", 9.85, Math.sqrt(5504.90 / 7534.24), "CG agent · 5 505 µm²"]].forEach(([f, x, k, t]) => {
      const d = S * k, y = 1.5;
      s.addImage({ path: IMG + f + "_layout.png", x: x + (S - d) / 2, y: y + S - d, w: d, h: d });
      s.addText(t, { x: x - 0.1, y: y + S + 0.1, w: S + 0.2, h: 0.3, fontFace: F, fontSize: 12, color: K.ink, align: "center", margin: 0, isTextBox: true });
    });
    H.caption(s, "Side ∝ √(die area); all four relative to the NoCG baseline.", 0.85, 4.5, 11.6);
    H.head(s, "Reading the pictures", 0.85, 5.0, 11.6);
    H.bullets(s, ["The agent shrank the die by packing the same cells tighter (higher utilization and placement density), not by changing the design.",
      "CG starts smaller than NoCG and shrinks less, because the agent stayed careful after the long CG route."], { x: 0.85, y: 5.4, w: 11.65, h: 1.4, fs: 14 });
  }

  {
    const s = H.slide("Takeaways and Limits",
      "Demo 2 (optional, not started): a PicoRV32 mini-SoC reusing the same blocks, tasks and agent loop.");
    H.head(s, "What this step shows", 0.85, 1.3, 5.6);
    H.bullets(s, ["CHIA takes the same RTL from Sim to a sky130 GDS, one task per ORFS stage, with a gate at the end.",
      "Tuning only config.mk, the agent cut die area by 20-38 % and CG power by 6.8 %, with timing still met and no DRC errors.",
      "Clock gating helps on ASIC and hurts on FPGA; fixed RTL keeps the two branches apart."], { x: 0.85, y: 1.7, w: 5.6, h: 3.8, fs: 14.5 });
    H.head(s, "Limits", 6.85, 1.3, 5.6);
    H.bullets(s, ["Small design (about 300 cells) and fixed RTL leave little room.",
      "Power is OpenSTA's default-activity estimate, not from the VCD.",
      "Two rounds, one run per configuration; the tools are deterministic, the agent is not."], { x: 6.85, y: 1.7, w: 5.6, h: 3.8, fs: 14.5 });
  }
  await H.save("3_ASIC.pptx");
}

(async () => {
  require("fs").mkdirSync(OUT, { recursive: true });
  await setupDeck(); await simDeck(); await fpgaDeck(); await asicDeck();
  console.log("written to", OUT);
})();
