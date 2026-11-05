# Bàn giao: rocket-fmax

File này dành cho session Claude Code tiếp quản project. Đọc cùng `CLAUDE.md` (gốc repo), `docs/workflow-phases.md`
và `README.md` (thư mục này).

## Trạng thái (05/11/2026)

- Mới nhất: **140,01 MHz** Yosys `v3` ở `--period-ns 6` (`asic-7`, `~/rocket-fmax-v2/asic-7/patches/best.patch`),
  cycle +0,325 %. Chưa kiểm chức năng ngoài gate, chưa place-and-route (mục 11, 12). Các dòng dưới là lịch sử.
- Tốt nhất ở flow `v3`: **93,88 MHz** (`asic-4`, patch `~/rocket-fmax-v2/asic-4/patches/best.patch` = `sim-1` vòng 5 +
  `sim-2` vòng 4 (`LW` đi đường bypass chậm) + `asic-3` vòng 1 (mux bypass one-hot, thêm thanh ghi trước đầu vào
  `divSqrt`) + vòng 2 (`negated_remainder` cây OR) + `asic-4` vòng 1 (cộng cuối FMA carry-save + Kogge-Stone,
  `ex_reg_inst`/`ex_reg_ctrl` FPU thành `RegNext`) + vòng 3 (early-out bộ chia qua thanh ghi `eoPos`); sha256
  `b16798766e65…`, 547 dòng, 5 file: `ALU`, `AMOALU`, `Multiplier`, `RocketCore`, `FPU`), cycle **+2,685 %** (còn
  0,315 % ngân sách), worst slack −0,652 ns, TNS −16,0 ns, điểm họ −2,989.
  Đường găng: `core.div.resHi → remainder[63]` (chọn nửa kết quả rồi phủ định); họ ALU/bypass `core.ex_reg_rs_oh →
  mem_reg_wdata` ở khoảng −0,74.
  Trước đó: 93,17 (`asic-3`), 91,81 (`asic-2`), 87,84 MHz (`asic-1`).
- Tốt nhất: **92,36 MHz** Yosys `v2` (patch `~/rocket-fmax-v2/fmax-r4/patches/best.patch` trên máy người dùng),
  cycle +0,405 %, worst slack −0,827 ns. Các họ đường găng sát nhau: `core.div.remainder` (−0,83), FMA
  `mulAddResult_r`/`ppl`/`pph` (−0,80 … −0,70), `fpuOpt.regfile_ext.Memory` (−0,66), `divSqrt` `rem_Z`/`sigX_Z`
  (−0,64/−0,58), `core.mem_reg_wdata` (−0,48).
- `fmax-y1`, `y3`, `y4` (12 vòng cộng lại) không nhận vòng nào. Nguyên nhân rút ra:
  1. Sửa cấu trúc (prefix adder, CSA, Booth) không thắng `+`/`*` của Yosys/ABC.
  2. Flow `v2` (flatten) dao động 0,2–0,6 ns khi sửa một khối khác, lớn hơn ngưỡng 1 %.
  3. Fmax chỉ tăng khi rút ngắn hết các họ sát nhau, mà mỗi vòng agent chỉ xử lý một họ.
  4. Agent đo lại cùng một sửa (`mulUnroll ≤ 4` đo 4 lần).
- 02/11/2026, người dùng duyệt khung pha (trên `setup`) và hai bước cho project, **đã code, mới kiểm bằng mock**:
  - Bước 1, pha sim (`--phase sim`, `prompts/explore_pipeline.md`): agent thêm tầng pipeline cho một họ, chỉ kiểm
    compile; loop chạy gate, đo cycle; qua gate thì lưu ứng viên (`candidates.json`). `--try-from` ở pha asic thử
    từng ứng viên trên bản tốt nhất (`N.apply_candidate`, `git apply -3`).
  - Bước 2, cách đo: flow `v3` phân cấp (mặc định), nhận thêm theo điểm họ (`--min-family-gain`, `--worst-tol`),
    `synth_ab` nhớ Verilog đã đo (`AB.child_key`, `load_memo`), ngưỡng nhiễu `synth_ab` theo flow.

## Việc tiếp theo

1. Xong (03/11/2026): `v3-start` cho tên đúng dạng, điểm xuất phát 85,01 MHz ở `v3` (−1,763 ns, họ FMA đứng đầu).
   Số `v3` thấp hơn `v2` vì không tối ưu qua cổng module (hằng số không lan vào module con); khi báo kết quả cuối,
   synth lại bản tốt nhất bằng `v2` (và sau này ORFS) để so với 92,36 MHz.
2. `sim-1` xong (code trước 03/11/2026): 5 ứng viên qua gate, cả 5 ở `fpuOpt.dfma.fma`, cùng tiêu tầng `Pipe` rảnh ở
   đầu ra FMA (xem README, mục kiểm chứng). Với code mới chúng thành một nhóm biến thể, `asic-1` giữ tối đa một. Đã thêm
   03/11/2026 (mới kiểm bằng mock): khối đã đổi + biến thể, bảng "Blocks already covered" trong prompt sim, sửa tóm tắt
   mất dòng `TARGET`, synth sơ bộ cả tile ở pha sim, `--try-from` đo hết rồi thử theo nhóm biến thể, bỏ gate khi ứng
   viên nằm trên gốc của nó. Ba họ FMA đứng đầu cần nhiều hơn một tầng: đợt sim sau có thể cho agent thử tăng
   `dfmaLatency` và đo cycle.
3. Xong (03/11/2026): `asic-1 --try-from sim-1` nhận vòng 5 (85,01 → 87,84 MHz), bốn biến thể kia BỎ QUA; dừng tay ở
   vòng agent 1. Tiếp: `sim-2` từ `cand_iter5.patch` bằng code mới (lần đầu chạy thật bảng chống lặp và synth sơ bộ),
   rồi `--try-from sim-2 --iters 0`. Họ cần nhắm: `core.mem_reg_wdata` (sim-1 vòng 4 thử tắt `fastLoadWord` thì tốn
   +3,11 % cycle), `divSqrt`, các họ FMA còn lại.
4. Xong (03/11/2026): `sim-2` (6 ứng viên, 5 khối khác nhau, synth sơ bộ chạy đúng) và `asic-2 --try-from sim-2
   --iters 0` nhận vòng 4 (87,84 → 91,81 MHz, cycle +2,655 %). Sửa sau đó (chưa chạy thật): history của `--try-from`
   ghi patch riêng của ứng viên thay vì cả cây; đọc `TARGET:` ở giữa dòng. Vì ngân sách cycle gần hết, thêm tầng
   pipeline gần như không còn chỗ: bước tiếp hợp lý là pha asic có agent (sửa logic, không tốn cycle) nhắm
   `core.mem_reg_wdata` (ALU/bypass tầng ex), từ `asic-2/patches/best.patch`. Ý tưởng chưa làm: cho agent pha sim thấy đường
   xấu nhất của từng họ (hiện `timing_paths.rpt` chỉ có 10 đường, đều một họ; vòng 5 của `sim-2` phải đoán đường
   `divSqrt`); nhóm biến thể xét thêm file nguồn.
5. Xong (04/11/2026): `asic-3` (5 vòng agent từ `asic-2/patches/best.patch`) nhận vòng 1, 2: 91,81 → 93,17 MHz, cycle
   không đổi. Vòng 3–5 nhắm FMA `mulAddResult_*`: carry-save/carry-select xấu hơn; module riêng cho cộng cuối
   (`FmaFinalAdd2`) lời +0,198 ns ở họ đó nhưng `core.div.remainder` lên thay (+0,24 %, LOẠI). Sửa sau đó (chưa chạy
   thật): "Patch đã bị loại" ghi phần sửa của vòng trên bản tốt nhất; diff `synth_ab` cắt giữa thay vì lấy đuôi. Hướng
   tiếp: các họ đứng đầu sát nhau (~−0,7 ns), cần sửa FMA và `core.div.remainder` trong cùng một vòng, hoặc chấp nhận
   từng bước theo điểm họ (vòng 5 được +0,124, ngưỡng `--min-family-gain` là 0,15).
6. Xong (04/11/2026): `asic-4` (`--min-family-gain 0.1`, chạy bằng `rocket_fmax.py` cũ) nhận vòng 1, 3: 93,17 →
   93,88 MHz, cycle +2,685 %. Vòng 4, 5 nhắm `resHi → remainder` (tách fanout, `s_neg_output` hai bước) đều xấu hơn.
   Sửa sau đó: history nhãn cũ bỏ khối cả cây ở vòng có mục `synth_ab`. Hướng tiếp: `core.div` (`resHi` → mux → phủ định)
   và bypass/ALU tầng ex; ngân sách cycle còn 0,315 %.
7. Xong (04/11/2026): `asic-5` (code mới, 5 vòng agent từ `asic-4/patches/best.patch`): 0 vòng nhận. Sửa đáng xem
   lại: hai bộ phủ định song song sau mux `resHi` (`negOf`) đẩy đường găng sang FMA `mulAddResult_pph` (−0,661, worst
   chỉ −0,0087), nhưng history không có điểm họ. Sửa sau đó (mới kiểm bằng dữ liệu giả): history ghi điểm họ mỗi lần
   `synth_ab` và đánh dấu lần đo trùng. Cần hỏi người dùng điểm họ của lần đo `negOf` (`grep` trong
   `*ab_experiments.jsonl`) trước khi chọn hướng tiếp.
8. Xong (04/11/2026): `asic-6` (như `asic-5`, agent `--model opus --effort medium`) nhận vòng 2–5: 93,88 →
   **109,17 MHz**, worst slack +0,840 ns, cycle −0,006 %, hiệu năng +19,4 %. Mục tiêu > 100 MHz đạt ở mức synth. Vòng 4
   kéo cycle từ +2,685 % về −0,006 % (bypass, BHT): cần xem `asic-6/patches/best.patch` để hiểu vì sao. Agent Opus làm
   được nhiều sửa lớn trong một vòng (vòng 2 sửa cả `MulDiv` lẫn FMA), đúng chỗ Sonnet bị kẹt ở `asic-3`..`asic-5`.
9. Xong (04/11/2026): `viv-asic6`, Vivado post-route out-of-context của `asic-6/patches/best.patch` (sha
   `7dcfc16657bb`, sửa 11 file): **63,27 MHz** (WNS −5,805 ns), LUT 36 626, LUTRAM 130, FF 14 439, BRAM 48, DSP 0.
   Gần như không đổi so với `fmax-r4` (63,61 MHz): các sửa cho sky130 không giúp FPGA. Đường găng FPGA: FMA →
   `fpuOpt/regfile_ext/Memory_reg`, CE của `frontend/fq`. Người dùng bỏ worker `vivado` (`vivado_check.py`,
   `docker/VivadoDockerfile` xoá; bản cuối ở commit `2dd99af`).
10. Đang làm (04/11/2026): bitstream cả SoC bằng flow `fpga/` của Chipyard, không có board, chỉ gen file và đo
   (`soc_bitstream.py`, node `vivado_nodes.soc_bitstream` trên worker chipyard; Vivado và board files Digilent mount
   từ `~/tools/Xilinx`). Lệnh trong README, mục Chạy. Lần chạy đầu (`soc-asic6`) dừng ở compile Scala: harness `fpga/` của
   `chia_artifact` thiếu `wf_active`; node nay tự vá lúc chạy. Chạy lại thành công ở 50 MHz: cả hai điểm có bitstream,
   WNS baseline 0,129 / `asic-6` 0,372 ns (Fmax chỉ là cận dưới), `asic-6` tile LUT +23,8 %, DSP 25 → 0. Xong
   (05/11/2026): `soc-asic6-f70` (70 MHz, WNS âm, Fmax thật): baseline 55,50, `asic-6` 59,91 MHz (+7,9 %); đường găng
   `asic-6` là `frontend/icache/s2_dout` → CE của `frontend/fq`. fpga-shells vẫn ghi bitstream khi không đạt timing
   (summary nay ghi rõ). Chi tiết ở README, mục kiểm chứng. Nhánh FPGA dừng ở đây trừ khi người dùng muốn tối ưu
   riêng cho FPGA (`frontend/fq` CE, lấy lại DSP cho bộ nhân).
11. Xong (05/11/2026): `asic-7` (từ `asic-6/patches/best.patch`, `--period-ns 6 --model opus --effort high
   --llm-timeout 2700`) nhận vòng 1–4: 113,53 (điểm xuất phát synth lại ở 6 ns) → **140,01 MHz**, worst slack −1,143 ns
   ở 6 ns, cycle +0,325 %, diện tích +3,9 %. Vòng 5 LOẠI (agent hết giờ). Đường găng cuối
   `dcache.s1_tlb_req_passthrough → s2_tlb_xcpt_ae_st`. Patch: `~/rocket-fmax-v2/asic-7/patches/best.patch`. Chi tiết
   từng vòng ở README, mục kiểm chứng. Chưa tới 150 MHz; các sửa đụng frontend queue (`ShiftQueue` vòng pop trễ),
   `IBuf`/`RVCExpander`, `BTB`, ALU (đếm số 0, mux cuối), recode FP: phải kiểm chức năng (mục 12a) trước khi chạy đợt
   agent tiếp.
12. Tiếp theo (kế hoạch, chưa làm, chờ người dùng duyệt file): (a) kiểm chức năng bản tốt nhất: rv64si/rv64mi (trap, ngắt, VM), chương trình
   chạy trên `pk`, so trace commit (PC, rd, dữ liệu ghi) lệnh-theo-lệnh với baseline trên mọi benchmark, test ngẫu
   nhiên nếu image có; (b) ASIC post-route bằng OpenROAD đã có trong image `eda` + platform sky130hd của ORFS (cùng
   commit `cca3a717`), SRAM là macro giả (timing giả định, giống nhau cho mọi điểm), đo baseline / `asic-6` / `asic-7`
   cùng chu kỳ, ghi WNS sau route (có RC), diện tích, mật độ.

   Đọc `asic-7/patches/best.patch` (05/11/2026, sha `b93d772b45d8`, 2345 dòng, 12 file; Claude chỉ đọc, không sửa).
   Điểm rủi ro mà gate hiện tại gần như không phủ, theo thứ tự ưu tiên kiểm:
   - **Nghi lỗi thật:** `csr.io.decode(0).inst := id_raw_inst(0)`. C.EBREAK (lệnh RVC duy nhất có `id_system_insn`)
     đi qua `system_illegal` của CSRFile với bit thô: bit 31..20 là nửa lệnh kế tiếp, nên ở U/S-mode có thể trap
     "illegal instruction" thay vì breakpoint (gdb dưới Linux dùng `c.ebreak`). Cần test nhắm đúng chỗ này.
   - Giải mã stall song song cho RVC (`sd_*`, bảng tay; mình đối chiếu từng opcode, chưa thấy sai) và `ShiftQueue`
     vòng pop trễ trong `frontend.fq`: gate không có `rv64uc`.
   - Zbb: CPOP/CPOPW tách hai tầng (đếm theo byte ở EX, cộng ở MEM, `ex_reg_cpop` chặn bypass EX), bộ đếm số 0 mới,
     mux cuối ALU theo byte: gate không có test Zbb.
   - Load: mọi load (cả LB/LH) bypass qua `data_load_bypass` = `RegNext(LoadGen(s1_req, dữ liệu s1))`, chỉ còn SC
     đi đường chậm; load uncached (MMIO) đổi `s2_req` nên giá trị bypass sai, phải dựa vào replay để không bị dùng.
   - FPU: `sfmaLatency` 3 → 6, `dfmaLatency` 4 → 6, `ifpuLatency` 2 → 3; FMA nhân tách hai tầng (Dadda), `postMul`
     tách tầng, `FastDivSqrt` (bản sửa của `DivSqrtRecFN_small`) + thanh ghi đầu ra, `recodeFast` cho load FP,
     `RecFNToINPre`, `rawFloatFromIN` viết lại: rv64uf/ud chỉ có vài vector mỗi phép.
   - Bộ nhân (`daddaReduce`), early-out bộ chia tính trước một chu kỳ (`eoPos`), `PMP.computeMask` bằng smear,
     `ex_*` nạp mỗi chu kỳ không chờ `ctrl_killd`, tval của lệnh illegal lấy từ `wb_reg_raw_inst`, đích nhánh tính ở EX.
   Kế hoạch kiểm cụ thể (chưa code): (1) thêm `rv64uc`, `rv64si`, `rv64mi`, bản `-v-`, `rv64uzb*` nếu image có;
   (2) chương trình C ngẫu nhiên có hướng (FP mọi phép, mọi rm, subnormal/NaN/inf, fflags; mul/div biên; Zbb kèm dùng
   ngay kết quả; load byte/half dùng ngay; RVC), in hash, so với Spike và baseline; (3) test tay C.EBREAK ở U-mode,
   load MMIO byte dùng ngay; (4) so trace commit với baseline trên 8 benchmark nếu `+verbose` có.

## Quy ước làm việc

Xem đầy đủ trong `CLAUDE.md`.

- Trả lời bằng tiếng Việt.
- Claude không chạy lệnh trên máy người dùng: đưa lệnh cho người dùng chạy, rồi đọc log họ gửi lại.
- **Claude không tự viết patch Chisel.** Mọi chỉnh sửa Chisel do loop (agent) làm.
- `~/CHIA-workflow` trên máy người dùng không phải git clone: người dùng chép file bằng tay. Sau mỗi lần đưa code,
  đưa `sha256sum <file> | cut -c1-12` để họ đối chiếu.
- Làm việc trên nhánh `project/rocket-fmax`. Không tạo PR, không rebase hay force-push, không ghi tên model vào commit.
- Liệt kê file cho người dùng duyệt trước khi tạo hoặc xoá file tài liệu.
- Cập nhật `README.md` và `CLAUDE.md` sau mỗi đợt chạy. Ghi rõ phần nào đã kiểm chứng, phần nào chưa.
- Mã nguồn CHIA (chialoops 2.0.1) để đối chiếu: repo `ucb-bar/chia`, `examples/timing_opt`.
