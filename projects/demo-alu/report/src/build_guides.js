// build_guides.js - 4 deck hướng dẫn từng bước (tiếng Anh; 0_Setup chung, không gắn demo), cùng style với ProgressReport_22-11.pptx:
//   guides/0_Setup.pptx, 1_Sim.pptx, 2_FPGA.pptx, 3_ASIC.pptx
// Chạy: NODE_PATH=<thư mục có pptxgenjs>/node_modules node build_guides.js [thư mục ra]
// Số liệu lấy từ REPORT.md và docs/demo-task.md (chạy thật 20–21/11/2026).
const pptxgen = require("pptxgenjs");
const path = require("path");
const OUT = process.argv[2] || path.join(__dirname, "..", "guides");
const IMG = path.join(__dirname, "..", "img") + "/";
const F = "Times New Roman", M = "Courier New";
const K = { black: "000000", ink: "262626", mute: "5A5A5A", cap: "9A9A9A", line: "BFBFBF", head: "D9D9D9", soft: "F2F2F2", grid: "A6A6A6" };

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
    // khối lệnh: lines là mảng chuỗi; trả về y đáy
    code(s, lines, x, y, w, fs) {
      fs = fs || 10.5;
      const h = lines.length * fs * 1.22 / 72 + 0.22;
      s.addText(lines.map((l, i) => ({ text: l || " ", options: { breakLine: i < lines.length - 1 } })),
        { x, y, w, h, fontFace: M, fontSize: fs, color: K.black, fill: { color: K.soft }, line: { color: K.line, width: 0.75 },
          valign: "top", margin: [5, 8, 5, 8], isTextBox: true, paraSpaceAfter: 0 });
      return y + h;
    },
    expect(s, t, x, y, w, h) {
      s.addText([{ text: "Expected  ", options: { bold: true, color: K.black } }, { text: t, options: { color: K.ink } }],
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
      s.addText("Guide series: 0 Setup · 1 Sim · 2 FPGA · 3 ASIC", { x: 0.5, y: 6.6, w: 8, h: 0.3, fontFace: F, fontSize: 12, color: K.cap, margin: 0, isTextBox: true });
      return s;
    },
    save(file) { return pres.writeFile({ fileName: path.join(OUT, file) }); },
  };
  return H;
}

// bố cục chuẩn của một slide bước: trái giải thích, phải lệnh + kết quả mong đợi
function step(H, t, why, cmds, expect, notes, o) {
  o = o || {};
  const s = H.slide(t, notes);
  H.head(s, o.leftHead || "What and why", 0.85, 1.3, 5.3);
  H.bullets(s, why, { x: 0.85, y: 1.75, w: 5.3, h: 5.0, fs: o.fs || 14 });
  H.head(s, o.rightHead || "Run", 6.55, 1.3, 5.95);
  let y = 1.75;
  cmds.forEach((c) => {
    if (typeof c === "string") { H.caption(s, c, 6.55, y, 5.95, 0.28); y += 0.32; }
    else { y = H.code(s, c, 6.55, y, 5.95, o.cfs) + 0.15; }
  });
  if (expect) H.expect(s, expect, 6.55, y + 0.05, 5.95, o.eh || 0.75);
  return s;
}

// ====================================================================================
// 0. SETUP (chung, không gắn với demo): cluster một máy + worker remote/cloud
// ====================================================================================
async function setupDeck() {
  const H = deck("Guide 0 · Setting up CHIA", "Guide 0: Setting up the CHIA Framework");
  H.cover("GUIDE 0 · FRAMEWORK SETUP", "Setting up the CHIA Framework:", "Local and Remote Workers",
    "Ray cluster of Docker workers for hardware tools (Chipyard, Verilator, Vivado, OpenROAD) and LLM agents");

  // 2. Mô hình CHIA
  {
    const s = H.slide("How CHIA Runs Work",
      "CHIA (UC Berkeley, pip package chialoops) is a hardware/software co-design framework built on Ray. Docs: docs.chialoops.ai; paper arXiv:2606.27350.\n\nThe head runs Ray and your driver script. Each worker is a Docker container with one set of tools, and it advertises resources (names you choose, such as chipyard or vivado). A function decorated with @ChiaFunction(resources=...) runs on any worker that has those resources, on whichever machine it lives.\n\nchia job submit --working-dir . copies the current folder to every worker, so the modules that define your tasks can be imported there.\n\nCHIA also gives LLM wrappers, tool servers (BashTool), a result cache and a profiler with a timeline view (chia viz-profile).");
    H.box(s, "Head", "Ray head · your driver script · chia job submit", 3.9, 1.35, 5.5, 0.8, true);
    const bl = [["worker A", "e.g. Chipyard build", "chipyard"], ["worker B", "e.g. run simulator", "verilator_run"], ["worker C", "e.g. Vivado", "vivado"],
                ["worker D", "e.g. OpenROAD", "asic"], ["worker E", "e.g. LLM agent", "claude_creds"]];
    bl.forEach(([a, b, r], i) => {
      const x = 0.85 + i * 2.37;
      H.box(s, a, b + "\nresource: " + r, x, 2.7, 2.15, 1.0, false, 12.5);
      H.arrow(s, 6.65, 2.15, x + 1.075, 2.7);
    });
    H.caption(s, "Each worker is a Docker container; workers can sit on the head machine or on remote / cloud machines.", 0.85, 3.8, 11.6);
    H.head(s, "A task and a job", 0.85, 4.3, 6.5);
    H.code(s, ["from chia.base.ChiaFunction import ChiaFunction, get", "", '@ChiaFunction(resources={"verilator_run": 1})',
      "def run_sim(path):            # runs on a worker with that resource", '    return subprocess.run(["./sim"], cwd=path).returncode', "",
      "rc = get(run_sim.chia_remote(path))   # the driver waits here"], 0.85, 4.7, 6.6, 10.5);
    H.head(s, "Submit from the head", 7.8, 4.3, 4.7);
    H.code(s, ["conda activate chia_env", "chia job submit --working-dir . \\", "  -- python my_loop.py", "chia job logs -f <id>", "chia job stop <id>"], 7.8, 4.7, 4.7, 10.5);
  }

  // 3. Local vs remote
  {
    const s = H.slide("Two Ways to Run the Cluster",
      "Part A (local) has been run end to end on the user's machine (Bazzite, 20 CPU, 23 GB RAM): full reinstall on 14/11/2026, all five tool blocks with KẾT QUẢ: OK.\n\nPart B (remote) is written from the CHIA documentation and source (cluster_config_reference.rst, chia/cluster/*.py). It has NOT been run yet because there is no second machine. Treat its commands as a plan to check, not as verified steps.");
    H.table(s, [["", "A. Local (one machine)", "B. Remote / cloud workers"],
      ["Machines", "Head and all workers on one PC", "Head on your PC; workers also on other PCs or cloud VMs"],
      ["When", "Tools fit in one machine's CPU, RAM and disk", "More parallel builds or simulations, more RAM, tools only on another machine"],
      ["Network", "None beyond SSH to yourself", "SSH head → remote; Ray ports open both ways; Tailscale across networks"],
      ["Remote needs", "-", "sshd, Docker without sudo, rsync. No conda, no CHIA"],
      ["Config", "scripts/chia-up-blocks.py (generates the cluster file)", "configs/cluster-lan.yaml (HEAD_IP, REMOTE_IP, REMOTE_USER)"],
      ["Status", "verified 14/11/2026", "not tested yet"]],
      { x: 0.85, y: 1.35, w: 11.65, colW: [1.75, 4.6, 5.3], fs: 13, boldFirstCol: true });
    H.caption(s, "Do Part A first: a remote cluster is the local one plus extra workers.", 0.85, 5.6, 11.6);
  }

  // ---------------- Part A ----------------
  step(H, "A1 · Host Prerequisites", [
    "CHIA always calls the docker command, so Docker must work without sudo.",
    "chia up opens an SSH session to the head IP even with one machine: you need sshd and a key that logs into your own machine without a password.",
    "loginctl enable-linger keeps the Ray head alive after that SSH session closes. Without it the head dies right after chia up (worker log: Failed to connect to GCS).",
    "Miniforge (conda) provides the Python environment in A2.",
  ], [
    "Docker without sudo (Bazzite-DX: ujust dx-group, then log in again)",
    ["docker run --rm hello-world"],
    "SSH to yourself without a password",
    ["sudo systemctl enable --now sshd", "ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519",
     "cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys", "chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys",
     "ssh $USER@<your-ip> 'bash --login -c \"docker ps\"'"],
    "Keep user processes alive after logout",
    ["loginctl enable-linger $USER"],
  ], "hello-world prints its greeting; the ssh line lists containers without asking for a password.",
  "Machine used: Bazzite-DX (ghcr.io/ublue-os/bazzite-dx-nvidia:stable). On Bazzite without DX there is only Podman; CHIA does not support Podman officially (see docs/setup-bazzite.md).\n\nIf ssh asks for a passphrase, load the key into ssh-agent: eval \"$(ssh-agent -s)\"; ssh-add ~/.ssh/id_ed25519.\n\nVerified on the user's machine.", { eh: 0.6 });

  step(H, "A2 · Python Environment, CHIA and the Workflow Repo", [
    "CHIA is the pip package chialoops; it brings Ray 2.54.0.",
    "Ray checks that every worker has the same Python as the head, down to the patch version, so pin 3.10.19 (the worker images use it).",
    "PYTHONNOUSERSITE=1 stops pip from picking packages out of ~/.local (an old anyio/httpx clash).",
    "CHIA-workflow holds the cluster templates, setup scripts and notes; check-bazzite-env.sh checks docker, ssh, linger and disk.",
  ], [
    ["conda create -n chia_env python=3.10.19 -y", "conda env config vars set PYTHONNOUSERSITE=1 -n chia_env",
     "conda activate chia_env", "pip install chialoops==2.0.1", "python -V; ray --version; chia --help | head -3"],
    ["git clone -b setup \\", "  https://github.com/chipchip-tech/CHIA-workflow.git \\", "  ~/CHIA-workflow",
     "cd ~/CHIA-workflow && scripts/check-bazzite-env.sh"],
  ], "Python 3.10.19, ray 2.54.0, chia --help runs; the check script passes (a RAM warning under 32 GB is normal).",
  "Verified 14/11/2026 (docs/reinstall.md steps 3-4).", { eh: 0.75 });

  step(H, "A3 · Chipyard (chipyard and verilator workers)", [
    "Tools live on the host under ~/tools and are mounted into the workers at the same real path, so you can edit them with a normal editor.",
    "setup-chipyard.sh clones ucb-bar/chipyard, checks out tag 1.15.0 as branch local-1.15.0 and runs all 11 steps of build-setup.sh in a temporary container.",
    "This takes hours. Leave the machine on, run one copy at a time, use tmux if you are on SSH.",
    "If it stops, the script prints the step number and the exact command to resume. Steps 8-9 (FireMarshal) need --privileged.",
  ], [
    ["cd ~/CHIA-workflow", "scripts/setup-chipyard.sh --verify", "tail -f ~/tools/chipyard/setup.log"],
    "Only if step 9 fails at 'sudo mount -o loop'",
    ["scripts/setup-chipyard.sh --privileged ~/tools/chipyard \\", "  -- -s 1 -s 2 -s 3 -s 4 -s 5 -s 6 -s 7 -s 10 -s 11"],
    "Host layout after A3 and A4",
    ["~/tools/chipyard/               Chipyard 1.15.0", "~/tools/Xilinx/2025.1/          Vivado (read-only)", "~/tools/OpenROAD-flow-scripts/  ORFS"],
  ], "Setup complete!, then VERIFY_OK (RocketConfig Verilator build and a test program with EXIT=0).",
  "Errors seen on the real run (14/11/2026, 8 attempts) and their fixes are in docs/reinstall.md step 5.\n\nBazzite: /home is a symlink to /var/home, so every mount uses realpath; Chipyard writes absolute paths into env.sh.\n\nVerified 14/11/2026.", { eh: 0.6 });

  step(H, "A4 · Vivado, OpenROAD and the Agent Login", [
    "Vivado: install under ~/tools/Xilinx; the worker mounts it read-only at the same real path, since settings64.sh hard-codes it. Vivado 2025.1 crashes after routing without --net=host; CHIA containers always have it.",
    "OpenROAD-flow-scripts: build the chia-orfs image (Ray added to openroad/orfs, own Python 3.10.19), then copy ORFS out of it to ~/tools.",
    "LLM agent (Claude Code): log in once on the host; the claude worker reuses ~/.claude. CHIA also ships an OpenCode image (chia-opencode).",
    "All three are optional: enable only the workers your project needs.",
  ], [
    "Vivado board files (once, for Digilent boards)",
    ["B=~/tools/Xilinx/2025.1/Vivado/data/boards/board_files", "git clone --depth 1 https://github.com/Digilent/vivado-boards /tmp/vb", "mkdir -p $B && cp -r /tmp/vb/new/board_files/arty-a7-100 $B/"],
    "OpenROAD-flow-scripts",
    ["docker build -t chia-orfs:latest -f docker/orfs-ray.Dockerfile docker", "scripts/setup-orfs.sh --verify"],
    "Claude Code login",
    ["claude && cp ~/.claude.json ~/.claude/.claude.json"],
  ], "setup-orfs.sh prints VERIFY_OK (nangate45/gcd end to end).",
  "Other Xilinx path or version: export XILINX_DIR=... VIVADO_VERSION=.... RAM limit of the asic worker: ASIC_MEM=20g (default 12g).\n\nopenroad -version prints 'unknown' inside the worker (image built without a git SHA); not an error.\n\nVerified 08-14/11/2026.", { eh: 0.55, cfs: 9.5 });

  step(H, "A5 · Bring Up the Local Cluster and Smoke Test", [
    "chia-up-blocks.py writes cluster.gen.yaml from the template with only the workers you ask for, then runs chia up. It checks prerequisites first (Chipyard env.sh, Vivado settings64.sh, chia-orfs image, Claude login).",
    "Default: chipyard, verilator, claude. --fpga adds vivado, --asic adds asic, --no-claude drops the agent.",
    "A shared work folder (WORK_DIR, default ~/chia_demo) is mounted read-write into every worker, so one task can read what another wrote.",
    "Always shut down with --down: plain chia down without THIS_MACHINE removes nothing.",
  ], [
    ["conda activate chia_env && cd ~/CHIA-workflow", "python scripts/chia-up-blocks.py --fpga --asic", "ray status"],
    ["cd examples && chia job submit --working-dir . -- \\", "  python blocks_smoke.py"],
    "When you are done",
    ["python ~/CHIA-workflow/scripts/chia-up-blocks.py --down"],
  ], "ray status lists every resource; the smoke test ends with SUCCESS=True, vivado v2025.1 and KẾT QUẢ: OK.",
  "--dry-run shows the generated config without starting anything. --verilator-slots N lets N simulations run at once. WORK_DIR=/path changes the shared folder.\n\nchia up reuses a container that is still running, so after changing mounts run --down first.\n\nDashboard: http://127.0.0.1:8265.\n\nVerified 14/11/2026 (all five workers) and 15/11/2026 (--down removes every container).", { eh: 0.75 });

  // ---------------- Part B ----------------
  {
    const s = H.slide("B1 · Remote Workers: Architecture and Network",
      "Ray has no login: anyone who can reach the Ray ports can run code on the cluster. Allow only the cluster machines' IPs, never the Internet.\n\nThe remote machine does not need conda or CHIA. chia up SSHes into it (ssh -A ... bash --login), pulls the images, starts containers with --net=host --init and runs ray start --address=<HEAD_IP>:6379 inside them.\n\nThe driver still runs on the head; Ray sends tasks to the remote by resource and moves results between machines.\n\nNot tested yet (no second machine).");
    H.box(s, "HEAD (your PC)", "Ray head :6379 · driver · local workers", 0.85, 1.4, 4.2, 1.0, true);
    H.box(s, "REMOTE (PC or cloud VM)", "sshd · Docker · worker containers", 8.3, 1.4, 4.2, 1.0);
    H.arrow(s, 5.05, 1.7, 8.3, 1.7);
    H.arrow(s, 8.3, 2.1, 5.05, 2.1);
    H.para(s, "SSH, docker, ray start", { x: 5.2, y: 1.35, w: 3.0, h: 0.3, fs: 11.5, align: "center" });
    H.para(s, "GCS 6379 + random Ray ports, both ways", { x: 5.1, y: 2.15, w: 3.1, h: 0.3, fs: 11.5, align: "center" });
    H.head(s, "Ports that must be open between the machines", 0.85, 2.85, 5.7);
    H.bullets(s, ["Remote → head: GCS 6379, and random raylet / object manager / worker ports (10002-19999).",
      "Head → remote: random ports on the remote as well.",
      "Tool servers (BashTool, MCP) listen on 8000+ on the machine that runs them."], { x: 0.85, y: 3.25, w: 5.7, h: 2.2, fs: 13.5 });
    H.head(s, "Simplest safe rule: each machine trusts the other's IP", 6.85, 2.85, 5.65);
    H.code(s, ["# head (firewalld)", "sudo firewall-cmd --permanent --zone=trusted \\", "  --add-source=<REMOTE_IP>", "sudo firewall-cmd --reload", "",
      "# remote (ufw)", "sudo ufw allow from <HEAD_IP>"], 6.85, 3.25, 5.65, 10.5);
    H.head(s, "Suggested remote hardware", 0.85, 5.55, 5.7);
    H.para(s, "x86_64, ≥ 16 GB RAM per Chipyard worker, ≥ 120 GB free disk.", { x: 0.85, y: 5.95, w: 5.7, h: 0.5, fs: 13.5 });
  }

  step(H, "B2 · Prepare and Check the Remote Machine", [
    "prepare-remote-worker.sh runs on the remote and asks before each step: enable sshd, install Docker and add the user to the docker group, install rsync, open the firewall for the head, pre-pull the large images.",
    "It supports Ubuntu/Debian, Fedora and Fedora Atomic (Bazzite). Log out and in after joining the docker group.",
    "CHIA uses ssh -A, so the key must be in ssh-agent on the head.",
    "check-remote-worker.sh runs on the head and only reads: SSH, CPU architecture, Docker without sudo, rsync, RAM and disk, and (when the cluster is up) whether the remote reaches HEAD:6379.",
  ], [
    "On the head: copy the script over",
    ["scp ~/CHIA-workflow/scripts/prepare-remote-worker.sh \\", "  <user>@<REMOTE_IP>:"],
    "On the remote",
    ["bash prepare-remote-worker.sh <HEAD_IP>"],
    "On the head: key, SSH test, readiness check",
    ["ssh-copy-id <user>@<REMOTE_IP>", "eval \"$(ssh-agent -s)\"; ssh-add", "ssh <user>@<REMOTE_IP> 'bash --login -c \"docker ps\"'",
     "bash scripts/check-remote-worker.sh <user>@<REMOTE_IP> <HEAD_IP>"],
  ], "No password prompt, docker ps works over SSH, and the check prints no [FAIL].",
  "Fix every [FAIL] before chia up, and run the check again after the cluster is up to test the remote → head direction.\n\nIf the remote user differs from the head user, set REMOTE_USER; the template puts it in auth.overrides.\n\nNot tested yet: written from the CHIA docs and source.", { eh: 0.55, cfs: 10 });

  {
    const s = H.slide("B3 · Describe the Cluster and Bring It Up",
      "Run only one head per machine: shut the local cluster down first (chia-up-blocks.py --down), because both use port 6379.\n\nThe template's remote workers use the tools built into the CHIA images (/home/ray/chipyard). The local blocks mount tools from ~/tools instead; to do the same on a remote, the same folders must exist there at the same path.\n\nchia up --add creates only the missing workers and leaves running ones alone. To add a third machine, copy the *_remote node types, change compatible_ips, and add an auth.overrides entry if the user differs. Several identical machines can share one node type with balance_level: worker.\n\nNot tested yet.");
    H.head(s, "configs/cluster-lan.yaml (excerpt)", 0.85, 1.3, 6.3);
    H.code(s, ["provider:   { head_ip: ${HEAD_IP} }", "auth:", "  ssh_user: ${USER}", "  overrides: { \"${REMOTE_IP}\": { ssh_user: ${REMOTE_USER} } }",
      "available_node_types:", "  chipyard_remote:", '    resources: {"chipyard": 1, "cy_remote": 1}', "    num_workers: 1",
      '    compatible_ips: ["${REMOTE_IP}"]', '    docker: { image: "ghcr.io/ucb-bar/chia-chisel-build:latest" }', "  verilator_remote:",
      '    resources: {"verilator_run": 1}', "    num_workers: 2", '    compatible_ips: ["${REMOTE_IP}"]'], 0.85, 1.7, 6.5, 10);
    H.head(s, "Bring up, check, grow, shut down", 7.65, 1.3, 4.85);
    let y = H.code(s, ["conda activate chia_env", "CL=~/CHIA-workflow/configs/cluster-lan.yaml", "export HEAD_IP=<head ip>", "export REMOTE_IP=<remote ip>",
      "export REMOTE_USER=<remote user>", "chia up $CL -y", "chia status", "", "# after editing the yaml", "chia up --add $CL -y", "", "chia down $CL -y"], 7.65, 1.7, 4.85, 10);
    H.expect(s, "chia status shows 7 active nodes; resources chipyard: 2, cy_head: 1, cy_remote: 1, verilator_run: 3.", 7.65, y + 0.15, 4.85, 0.75);
  }

  {
    const s = H.slide("B4 · Other Networks, Cloud VMs and Writing Jobs",
      "Cloud VM: any provider works if the VM has a public or Tailscale-reachable SSH, Docker and enough disk for the images. Keep the provider's security group closed to the Ray ports and join the VM to the tailnet instead.\n\ntailnet: mode (no root): CHIA routes all Ray traffic over userspace Tailscale through a relay. On Bazzite, do not add --node-ip-address (CHIA adds it), keep systemd-run, and set ssh_proxy_command to ncat --proxy 127.0.0.1:1055 --proxy-type socks5 %h %p because Fedora's nc is Ncat. All workers must be on the tailnet or on the head machine.\n\nNot tested yet.");
    H.head(s, "Different networks or a cloud VM: Tailscale", 0.85, 1.3, 5.7);
    H.code(s, ["# on each machine (needs sudo)", "sudo systemctl enable --now tailscaled", "sudo tailscale up", "tailscale ip -4            # 100.x.y.z",
      "sudo firewall-cmd --permanent --zone=trusted \\", "  --add-interface=tailscale0 && sudo firewall-cmd --reload", "", "# then as on a LAN, with Tailscale IPs",
      "export HEAD_IP=$(tailscale ip -4) REMOTE_IP=100.x.y.z"], 0.85, 1.7, 5.7, 10);
    H.bullets(s, ["No root on the remote: CHIA's own tailnet: mode (see notes).", "Links over the Internet are slower than a LAN: keep heavy data on one machine."],
      { x: 0.85, y: 4.15, w: 5.7, h: 1.4, fs: 13 });
    H.head(s, "Jobs across machines", 6.85, 1.3, 5.65);
    H.bullets(s, ["The loop code does not change when machines are added: Ray places tasks by resource.",
      "Pin a tool and the build that uses its edits to the same container with a per-machine resource (cy_head, cy_remote).",
      "Spread independent tasks (simulations, builds) to use every machine; SPREAD forces it."], { x: 6.85, y: 1.7, w: 5.65, h: 2.2, fs: 13 });
    H.code(s, ['pin = {"chipyard": 1, "cy_remote": 0.01}', 'bash = BashTool("cy_bash", "/home/ray/chipyard",', '                task_options={"resources": pin})',
      "...agent edits through bash...; bash.stop()", "art = get(cb.build.options(resources=pin).chia_remote(cb))", "",
      "refs = [sim.run.chia_remote(...) for t in tests]  # all machines", 'r = f.options(scheduling_strategy="SPREAD").chia_remote(x)'], 6.85, 4.0, 5.65, 9.5);
  }

  // Lỗi
  {
    const s = H.slide("Common Errors and Fixes",
      "Local rows come from real runs on the user's machine. Remote rows are the expected failure modes listed in docs/usage-remote.md, not yet observed. Full lists: CLAUDE.md, docs/cluster-blocks.md, docs/usage-remote.md.");
    H.table(s, [
      ["", "Symptom", "Fix"],
      ["local", "Ray head dies right after chia up; worker: Failed to connect to GCS", "loginctl enable-linger $USER"],
      ["local", "chia down prints an empty Head: and removes nothing", "python scripts/chia-up-blocks.py --down"],
      ["local", "Version mismatch ... Python 3.10.22", "Same Python patch version everywhere (3.10.19)"],
      ["local", "Vivado crashes after Routing Is Done (manual container)", "Add --net=host"],
      ["local", "Ctrl+C does not stop a job", "chia job stop <id>"],
      ["remote", "chia up hangs at Waiting for SSH to worker", "ssh-copy-id, ssh-add, REMOTE_USER, sshd on the remote"],
      ["remote", "permission denied ... docker.sock", "Add the user to the docker group, log in again"],
      ["remote", "Remote worker: Failed to connect to GCS at <HEAD_IP>:6379", "Open the head firewall for the remote; HEAD_IP must be reachable from it"],
      ["remote", "Remote node comes and goes; tasks hang on results", "Open the remote firewall for the head too"],
      ["remote", "Agent edits code but the build has no change", "Tool and build ran in different containers: pin both"],
    ], { x: 0.85, y: 1.35, w: 11.65, colW: [1.0, 5.6, 5.05], fs: 12, boldFirstCol: true });
  }
  await H.save("0_Setup.pptx");
}

// ====================================================================================
// 1. SIM
// ====================================================================================
async function simDeck() {
  const H = deck("Guide 1 · Simulation", "Guide 1: Simulation with Verilator");
  H.cover("GUIDE 1 · SIMULATION", "Step 1 of the Flow:", "Simulation with Verilator",
    "Build on the chipyard block, run on the verilator block, then let Claude extend the testbench");

  {
    const s = H.slide("Where Sim Fits",
      "Sim is the first gate of the flow. FPGA and ASIC only run after the testbench prints RESULT: PASS.\n\nThe build and the run are two separate CHIA tasks on two blocks: the chipyard block has Verilator 5.022 (from Chipyard's conda env), the verilator block only has the runtime to execute the binary. Both see the same run directory through WORK_DIR.");
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
      "run_sim.sh works anywhere Verilator is installed, so you can also run it by hand outside CHIA. SIM=iverilog switches to Icarus Verilog; NO_VCD=1 skips the waveform.\n\nThe testbench drives random operations with en active en_pct % of the time, keeps a queue of expected results from its reference model, and compares every output when valid rises. checked counts those comparisons.");
    H.table(s, [
      ["File", "Role", "Who edits it"],
      ["rtl/<DUT>.v", "8-bit ALU, 8 ops (ADD SUB AND OR XOR SHL SHR SLT), 2-stage pipeline, flags zero / carry / valid", "nobody (locked)"],
      ["sim/tb_<DUT>.v", "Random stimulus, reference model, compares every result, prints the RESULT line", "agent, in phase 2"],
      ["sim/run_sim.sh", "Builds with Verilator and runs; BUILD_ONLY=1 or SKIP_BUILD=1 splits the two halves", "nobody"],
      ["baseline.py", "Defines the CHIA tasks sim_build and sim_run and calls them", "nobody"],
    ], { x: 0.85, y: 1.35, w: 11.65, colW: [2.1, 7.25, 2.3], fs: 12.5, monoCols: [0] });
    H.head(s, "Verilator command inside run_sim.sh", 0.85, 4.15, 11.6);
    let y = H.code(s, ["verilator --binary --timing --trace -Wall -Wno-fatal --top-module tb_NoCG_alu \\",
      "  -Mdir build/obj -o Vtb tb_NoCG_alu.v ../rtl/NoCG_alu.v"], 0.85, 4.55, 11.65, 11);
    H.head(s, "Result line the gate looks for", 0.85, y + 0.2, 11.6);
    H.code(s, ["RESULT: PASS dut=NoCG_alu seed=1 cycles=20000 en_pct=30 sent=<n> checked=6272 errors=0"], 0.85, y + 0.6, 11.65, 11);
  }

  step(H, "Step 1 · Run the Baseline Sim", [
    "Start the cluster first (Guide 0, step 7). Sim needs the chipyard and verilator blocks.",
    "--stages sim runs only this step. Without it baseline.py continues into FPGA and ASIC.",
    "Each run gets a new folder WORK_DIR/demo-alu/<demo>/<run_id>/ with a copy of rtl/, sim/, fpga/, asic/. The project folder in the repo is never written to.",
    "run_demo.py also opens the live status page; the plain chia job submit line does the same work without it.",
  ], [
    "With the status page",
    ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG baseline --stages sim"],
    "Or directly",
    ["cd ~/CHIA-workflow/projects/demo-alu/NoCG", "chia job submit --working-dir . -- \\", "  python baseline.py --stages sim --seed 1 --cycles 20000"],
  ], "[baseline NoCG] sim_build OK ... @ chia-chipyard-..., sim_run OK ... @ chia-verilator-..., then RESULT: PASS and KẾT QUẢ: OK.",
  "--working-dir . matters: CHIA ships the current folder to every worker, and the cache actor unpickles results by importing these modules.\n\nOther options: --seed, --cycles, --en-pct, --run-id, --work-dir.\n\nVerified: the Sim baseline ran for real on 20/11/2026 for both demos; the full three-step baseline on 21/11/2026. The --stages sim form of run_demo.py was not run separately on the user's machine.", { eh: 0.8 });

  {
    const s = H.slide("Step 2 · What CHIA Does Behind the Command",
      "Each @ChiaFunction declares the resource it needs; .chia_remote() returns a Ray ObjectRef and get() waits for it. The driver calls get() before starting the next task, because with the CHIA profiler on, an ObjectRef passed straight into another task arrives wrapped (_ProfiledResult).\n\nThe profiler (start_collector) records every task; at the end baseline.py turns the log into profile/ChiaProfileCollector_profile.html with chia viz-profile.");
    H.head(s, "Two tasks on two blocks (simplified from baseline.py)", 0.85, 1.3, 7.0);
    H.code(s, [
      '@ChiaFunction(resources={"chipyard": 1})',
      "def sim_build(run_dir):",
      '    _sh("./run_sim.sh", sim, {"BUILD_ONLY": "1"})   # Verilator 5.022',
      "",
      '@ChiaFunction(resources={"verilator_run": 1})',
      "def sim_run(run_dir, plusargs):",
      '    _sh("./run_sim.sh " + plusargs, sim, {"SKIP_BUILD": "1"})',
      '    ok = "RESULT: PASS" in log                       # the gate',
      "",
      "ok = get(sim_build.chia_remote(run_dir))[\"ok\"]",
      "if ok:",
      "    ok = get(sim_run.chia_remote(run_dir, plus))[\"ok\"]",
      "if ok:  # only now FPGA starts",
      "    get(fpga_build.chia_remote(run_dir))"], 0.85, 1.75, 7.2, 11);
    H.head(s, "Why split build and run", 8.4, 1.3, 4.1);
    H.bullets(s, ["Verilator lives in Chipyard's conda env, which only the chipyard block mounts.",
      "The run needs no compiler, so a lighter block runs it, and --verilator-slots N runs several simulations at once.",
      "Both tasks read and write the same run folder through WORK_DIR, so nothing is copied between blocks."], { x: 8.4, y: 1.75, w: 4.1, h: 4.6, fs: 13.5 });
  }

  step(H, "Step 3 · Read the Result", [
    "The RESULT line shows how many comparisons were made (checked) and how many failed (errors).",
    "results/baseline.json keeps every number of the run; phase 2 uses it as the reference.",
    "status.html shows the three steps, the task that is running and on which block.",
    "The waveform can be opened in any VCD viewer, for example GTKWave.",
  ], [
    ["R=~/chia_demo/demo-alu/NoCG/<run_id>", "grep RESULT $R/sim/build/sim.log", "ls -lh $R/sim/build/wave.vcd", "python -m json.tool $R/results/baseline.json | less"],
    "Timeline of the CHIA tasks",
    ["xdg-open $R/profile/ChiaProfileCollector_profile.html"],
  ], "Baseline, both demos: RESULT: PASS, checked=6272, errors=0.",
  "Folder layout of a run: rtl/ sim/ fpga/ asic/ (copies), results/, profile/, status.html.\n\nOn Bazzite use /usr/bin/xdg-open (the one on PATH is a distrobox shim), or python run_demo.py show <run_dir>.", { eh: 0.55 });

  {
    const s = H.slide("Step 4 · Let the Agent Improve the Testbench",
      "agent.py takes the newest baseline with KẾT QUẢ: OK as the starting point (or --baseline <run_dir>). --steps sim restricts phase 2 to this step.\n\nThe prompt tells the agent: keep the plusargs, the RESULT format and the counters; the RTL is the reference, so a new check that fails rejects the round.\n\nIf the agent changes nothing (it judges there is nothing worth adding), the step ends early.\n\nVerified: full agent runs (all steps, --rounds 2) on 21/11/2026 for both demos. --steps sim alone was not run separately.");
    H.loop(s, 1.4, "sim/tb_<DUT>.v only", "checked must rise");
    H.head(s, "Run", 0.85, 2.75, 5.6);
    H.code(s, ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG agent --steps sim --rounds 2"], 0.85, 3.15, 5.7, 10.5);
    H.head(s, "Why only the testbench", 0.85, 4.1, 5.6);
    H.bullets(s, ["The RTL is the thing being tested. If the agent could edit it, a passing test would prove nothing.",
      "Any other edited file is restored before CHIA measures."], { x: 0.85, y: 4.5, w: 5.6, h: 2.2, fs: 14 });
    H.head(s, "Gate and rule", 6.85, 2.75, 5.6);
    H.table(s, [["", "Sim"], ["Gate", "Verilator build + run with the baseline plusargs prints RESULT: PASS"],
      ["Accept", "checked strictly greater than the current best"], ["Runs on", "chipyard + verilator blocks (agent on claude)"]],
      { x: 6.85, y: 3.15, w: 5.65, colW: [1.3, 4.35], fs: 12.5, boldFirstCol: true });
  }

  step(H, "Step 5 · Inspect a Round", [
    "Each round has its own folder <step>-r<k>/ holding a full copy of the tree, so a rejected round never touches the best version.",
    "agent/TASK.md is the exact prompt; agent/context/ holds the reports the agent read.",
    "agent/summary.md is the agent's own short explanation (in Vietnamese, for the slides).",
    "changes.diff shows what was kept after CHIA reverted out-of-scope edits.",
    "results/compare.md is the baseline vs agent table, written at the end (phase 3).",
  ], [
    ["A=~/chia_demo/demo-alu/NoCG/<agent_run_id>", "ls $A/sim-r1/agent", "#  TASK.md  context/  summary.md  changes.diff  stream.log",
     "cat $A/sim-r1/agent/summary.md", "less $A/sim-r1/agent/changes.diff", "cat $A/results/compare.md"],
  ], "Console during the run: [agent NoCG] sim-r1: NHẬN (checked 6272 → ...), or LOẠI with the reason.",
  "NHẬN = accepted, LOẠI = rejected (the console messages of the scripts are in Vietnamese).\n\nRejected rounds are slimmed: binaries, VCD, bitstream, checkpoints and GDS are deleted to save disk; the sources, logs and agent folder stay.", { eh: 0.6 });

  // Ví dụ 1
  {
    const s = H.slide("Example · NoCG vs CG: Same Test, Different Clocking",
      "Both demos use the same port list and the same testbench structure, so the baseline gives identical counts. The difference is inside: CG drives the register clocks through module icg (rtl/icg_sim.v), a latch that is transparent while clk = 0 and an AND gate, so the gated clock has no glitches.");
    H.head(s, "NoCG: clock enable", 0.85, 1.3, 5.6);
    H.code(s, ["always @(posedge clk or negedge rst_n)", "  if (!rst_n) a_q <= 0;", "  else if (en) a_q <= a;   // feedback mux"], 0.85, 1.7, 5.6, 11);
    H.head(s, "CG: gated clock", 6.85, 1.3, 5.6);
    H.code(s, ["icg u_icg_in (.clk(clk), .en(en), .test_en(1'b0),", "             .gclk(gclk_in));", "always @(posedge gclk_in or negedge rst_n)", "  ...  a_q <= a;              // no mux"], 6.85, 1.7, 5.65, 11);
    H.head(s, "Baseline Sim result", 0.85, 3.35, 11.6);
    H.table(s, [["", "NoCG", "CG"], ["Result", "PASS", "PASS"], ["checked / errors", "6 272 / 0", "6 272 / 0"],
      ["Simulator", "Verilator 5.022 (chipyard block)", "Verilator 5.022 (chipyard block)"], ["ICG model in sim", "-", "rtl/icg_sim.v: latch + AND"]],
      { x: 0.85, y: 3.75, w: 11.65, colW: [3.0, 4.325, 4.325], fs: 13, boldFirstCol: true });
    H.caption(s, "Sim cannot see the cost of clock gating; that only shows up on FPGA and ASIC (Guides 2 and 3).", 0.85, 6.3, 11.6);
  }

  // Ví dụ 2
  {
    const s = H.slide("Example · What the Agent Added",
      "Run IDs: NoCG agent-20261121-141430, CG agent-20261121-143619. All four Sim rounds were accepted and still pass on the real Verilator.\n\nThe cost: longer simulation and a default VCD of about 90 MB. Use NO_VCD=1 in run_sim.sh if you do not need the waveform.");
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
    H.para(s, "Guide 2: the same ALU goes through Vivado to a bitstream, inside a self-checking FPGA wrapper.", { x: 0.85, y: 5.8, w: 11.6, h: 0.5, fs: 14.5 });
  }
  await H.save("1_Sim.pptx");
}

// ====================================================================================
// 2. FPGA
// ====================================================================================
async function fpgaDeck() {
  const H = deck("Guide 2 · FPGA", "Guide 2: FPGA with Vivado");
  H.cover("GUIDE 2 · FPGA", "Step 2 of the Flow:", "FPGA with Vivado 2025.1",
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
      "sources.f is read by both build.tcl and the xsim check of phase 2, so the build and the functional test always use the same list of files.\n\nIf N_CYCLES or SEED in fpga_top.v changes, EXP_SIG must be recomputed: python3 golden_sig.py 1048576 ACE12025 (fpga_top, EXP_SIG = EACBE87B) or python3 golden_sig.py 4096 ACE12025 (tb_fpga_top, AC066D58).");
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
    H.head(s, "build.tcl (baseline: Vivado defaults)", 0.85, 5.05, 11.6);
    H.code(s, ["read_verilog $srcs; read_xdc arty_a7_100t.xdc", "synth_design -top fpga_top -part xc7a100tcsg324-1; opt_design; place_design; route_design",
      "report_timing_summary / report_utilization / report_power ...; write_bitstream out/fpga_top.bit"], 0.85, 5.45, 11.65, 10.5);
  }

  step(H, "Step 1 · Run the Baseline FPGA Build", [
    "Needs the vivado block: bring the cluster up with --fpga (Guide 0, step 7).",
    "--stages sim,fpga keeps the Sim gate in front; Vivado only starts after RESULT: PASS.",
    "The fpga_build task runs Vivado in batch inside the run folder's copy of fpga/.",
    "About 64 s per demo on the user's machine.",
  ], [
    ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG baseline --stages sim,fpga"],
    "What the task runs on the vivado block",
    ["cd <run_dir>/fpga", "vivado -mode batch -nojournal \\", "  -log out/vivado.log -source build.tcl"],
  ], "[baseline NoCG] fpga OK ..., then a line with LUT, FF, BUFG, WNS, WHS, power and warnings, and the bitstream path.",
  "The task reads the numbers from reports/timing.rpt, util.rpt and power.rpt, and counts WARNING / CRITICAL WARNING lines (with their IDs) in out/vivado.log; the warning IDs are given to the agent in phase 2.\n\nVerified: FPGA baseline for both demos on 21/11/2026 (run twice, identical numbers). The --stages sim,fpga form was not run separately.", { eh: 0.8 });

  step(H, "Step 2 · Read the Reports", [
    "timing.rpt: WNS (setup) and WHS (hold) at 100 MHz. WNS ≥ 0 is the gate.",
    "util.rpt: LUT and FF count. util_hier.rpt splits them per module (wrapper vs ALU).",
    "power.rpt: total and dynamic power. Vivado's vector-less estimate, not from simulation activity.",
    "methodology.rpt and the warning list: this is where the CG design gets flagged.",
    "Numbers include the wrapper (LFSR, MISR, counters), the same for both demos.",
  ], [
    ["F=~/chia_demo/demo-alu/NoCG/<run_id>/fpga", "ls $F/reports", "grep -A6 'Design Timing Summary' $F/reports/timing.rpt",
     "grep -E 'Slice LUTs|Slice Registers' $F/reports/util.rpt", "grep -E 'Total On-Chip|Dynamic' $F/reports/power.rpt", "ls -l $F/out/fpga_top.bit"],
  ], "NoCG: 138 LUT, 139 FF, WNS 4.579 ns, dynamic power 2 mW, 1 warning.",
  "Full list in reports/: post_synth_util.rpt, util.rpt, util_hier.rpt, timing.rpt, clock_util.rpt, power.rpt, methodology.rpt, drc.rpt. Checkpoint: out/post_route.dcp.", { eh: 0.55 });

  {
    const s = H.slide("Step 3 · Let the Agent Try",
      "fpga_sim (vivado block): rm -rf xsim.dir && xvlog tb_fpga_top.v <sources.f> $XILINX_VIVADO/data/verilog/src/glbl.v && xelab ... && xsim tbsim -R. UNISIM lets Xilinx primitives such as BUFGCE or LUT6_2 simulate, so the agent can use them.\n\nTolerances only skip rounding: the tools are deterministic (two baseline runs gave identical numbers).\n\nVerified: full agent runs on 21/11/2026 for both demos. --steps fpga alone was not run separately; point --baseline at a baseline that includes FPGA.");
    H.loop(s, 1.4, "build.tcl, sources.f, new fpga/*.v", "strict Pareto");
    H.head(s, "Run", 0.85, 2.75, 5.6);
    H.code(s, ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG agent --steps fpga \\", "  --rounds 2 --baseline <baseline_run_dir>"], 0.85, 3.15, 5.7, 10.5);
    H.head(s, "Why these files only", 0.85, 4.35, 5.6);
    H.bullets(s, ["rtl/ is shared with ASIC. An FPGA trick written into new files under fpga/ cannot leak into the ASIC branch.",
      "Part, clock, .xdc and the checker stay fixed, so before and after measure the same thing."], { x: 0.85, y: 4.75, w: 5.6, h: 2.0, fs: 13.5 });
    H.head(s, "Gate and rule", 6.85, 2.75, 5.6);
    H.table(s, [["", "FPGA"], ["Gate", "tb_fpga_top PASS in xsim (UNISIM) · bitstream · WNS ≥ 0"],
      ["Accept", "no worse in WNS (±0.02 ns), dynamic power (±0.5 mW) and LUT+FF (±0.5) and better in at least one"],
      ["Runs on", "vivado block (agent on claude)"]], { x: 6.85, y: 3.15, w: 5.65, colW: [1.3, 4.35], fs: 12.5, boldFirstCol: true });
  }

  // Ví dụ 1
  {
    const s = H.slide("Example · Baseline: Clock Gating Hurts on FPGA",
      "Vivado builds the behavioural icg model as a latch plus a LUT that drives the register clock. The path through it has only half a cycle, so WNS drops by about 3 ns; the extra logic on the clock adds dynamic power, and Vivado reports gated-clock warnings (for example PDRC-153).\n\nThe ASIC result goes the other way (Guide 3), which is why FPGA-specific fixes must stay out of rtl/.");
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
    H.para(s, "Guide 3: the same RTL goes through Yosys and OpenROAD to a GDS on sky130hd, where clock gating pays off.", { x: 0.85, y: 5.8, w: 11.6, h: 0.5, fs: 14.5 });
  }
  await H.save("2_FPGA.pptx");
}

// ====================================================================================
// 3. ASIC
// ====================================================================================
async function asicDeck() {
  const H = deck("Guide 3 · ASIC", "Guide 3: ASIC with Yosys and OpenROAD");
  H.cover("GUIDE 3 · ASIC", "Step 3 of the Flow:", "RTL to GDS with Yosys + OpenROAD",
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
    H.head(s, "Baseline config.mk (close to ORFS's gcd example)", 0.85, 3.65, 7.0);
    H.code(s, ["export PLATFORM         = sky130hd", "export DESIGN_NAME      = NoCG_alu", "export VERILOG_FILES    = $(DEMO_ASIC_DIR)../rtl/NoCG_alu.v",
      "export SDC_FILE         = $(DEMO_ASIC_DIR)constraint.sdc", "export CORE_UTILIZATION = 40", "export PLACE_DENSITY    = 0.65"], 0.85, 4.05, 7.2, 11);
    H.head(s, "CG adds one line", 8.4, 3.65, 4.1);
    H.code(s, ["export VERILOG_FILES = \\", "  $(DEMO_ASIC_DIR)../rtl/CG_alu.v \\", "  $(DEMO_ASIC_DIR)icg_sky130.v"], 8.4, 4.05, 4.1, 10.5);
  }

  step(H, "Step 1 · Run the Baseline ASIC Flow", [
    "Needs the asic block: bring the cluster up with --asic (Guide 0, step 7).",
    "The full baseline runs Sim → FPGA → ASIC; --stages sim,asic skips Vivado.",
    "Each stage is one make call on the asic block. The log of each stage goes to asic/make/<stage>.log.",
    "finish also reads the metrics and draws asic/layout.png with KLayout.",
  ], [
    ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG baseline"],
    "What each asic_stage task runs (stage = synth ... finish)",
    ["make -C /OpenROAD-flow-scripts/flow \\", "  DESIGN_CONFIG=<run_dir>/asic/config.mk \\", "  WORK_HOME=<run_dir>/asic/orfs  <stage>"],
  ], "Six lines asic_synth ... asic_finish OK, then area, cells, slack, power, DRC and the paths of the GDS and layout.png.",
  "If the ORFS checkout ignores WORK_HOME, the synth task fails on purpose with a message, so nothing is silently written into the host ORFS tree.\n\nThe asic container has a RAM limit (ASIC_MEM, default 12g); a flow over the limit is OOM-killed without affecting the machine.\n\nVerified: ASIC baseline for both demos on 21/11/2026 (run twice, identical numbers, including route time).", { eh: 0.8 });

  step(H, "Step 2 · Read the Results", [
    "6_finish.rpt: final timing (setup and hold slack) and power.",
    "logs/*.json: ORFS metrics, where cell area, die area and utilization come from.",
    "6_final.v: final netlist. Counting cell types gives flops, ICG cells and clock buffers.",
    "layout.png: picture of the GDS for the slides.",
    "Power is OpenSTA's estimate with default activity, not from the Sim VCD.",
  ], [
    ["O=~/chia_demo/demo-alu/NoCG/<run_id>/asic", "ls $O/orfs/results/sky130hd/NoCG_alu/base", "#  ... 6_final.gds  6_final.v ...",
     "less $O/orfs/reports/sky130hd/NoCG_alu/base/6_finish.rpt", "ls $O/orfs/logs/sky130hd/NoCG_alu/base/*.json", "xdg-open $O/layout.png"],
  ], "NoCG: cell area 3 078 µm², die 7 534 µm², 356 cells, setup slack 6.815 ns, 0.368 mW, DRC 0.",
  "Open the GDS itself in KLayout if needed: klayout <...>/6_final.gds (inside the asic block or on a host with KLayout).", { eh: 0.6 });

  {
    const s = H.slide("Step 3 · Let the Agent Tune config.mk",
      "The prompt lists the ORFS variables the agent may use (CORE_UTILIZATION, PLACE_DENSITY, ABC_AREA, synthesis, repair and CTS options) and warns that too high a utilization fails place or route, which rejects the round.\n\nCHIA checks the rules on the edited config.mk before measuring: PLATFORM, DESIGN_NAME, DESIGN_NICKNAME, VERILOG_FILES and SDC_FILE must be unchanged.\n\nVerified: full agent runs on 21/11/2026 for both demos. --steps asic alone was not run separately.");
    H.loop(s, 1.4, "asic/config.mk only", "1 % rule");
    H.head(s, "Run", 0.85, 2.75, 5.6);
    H.code(s, ["cd ~/CHIA-workflow/projects/demo-alu", "python run_demo.py NoCG agent --steps asic \\", "  --rounds 2 --baseline <baseline_run_dir>"], 0.85, 3.15, 5.7, 10.5);
    H.head(s, "Why config.mk only", 0.85, 4.35, 5.6);
    H.bullets(s, ["config.mk says how to build, not what to build. The design, sources and SDC are the thing being measured.",
      "Same RTL, PDK and 10 ns clock as the baseline, so the numbers compare."], { x: 0.85, y: 4.75, w: 5.6, h: 2.0, fs: 13.5 });
    H.head(s, "Gate and rule", 6.85, 2.75, 5.6);
    H.table(s, [["", "ASIC"], ["Gate", "GDS · setup / hold slack ≥ 0 · DRC = 0"],
      ["Accept", "cell area, die area and power each no more than 1 % worse, and at least one more than 1 % better"],
      ["Runs on", "asic block (agent on claude)"]], { x: 6.85, y: 3.15, w: 5.65, colW: [1.3, 4.35], fs: 12.5, boldFirstCol: true });
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
      "The opposite of FPGA (Guide 2). This is why FPGA fixes stay out of rtl/."], { x: 8.05, y: 1.75, w: 4.45, h: 4.6, fs: 14 });
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
