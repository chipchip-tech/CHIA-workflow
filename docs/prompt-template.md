# Template prompt cho session mới

Dùng khi mở một session Claude Code mới để làm một project bằng CHIA. Bối cảnh chung
(máy, cách cài, các lỗi đã gặp) đã có trong `CLAUDE.md`, nên prompt chỉ cần tập trung
vào **project**: mục tiêu, phạm vi, tài nguyên, thế nào là "xong", và cách làm việc.

Nhớ mở session trên nhánh `claude/zen-pasteur-gygy0m`, hoặc bảo session checkout nhánh
đó trước.

## Template

```
Đọc CLAUDE.md và docs/usage-local.md trước (nhánh claude/zen-pasteur-gygy0m).

## Mục tiêu
<Project làm gì, đầu vào và đầu ra.>

## Phạm vi
- Thiết kế/config: <ví dụ RocketConfig, BOOM, accelerator riêng...>
- Workload/test: <chương trình C, benchmark, riscv-tests...>
- Agent: <OpenCode big-pickle / Claude Code / không dùng agent>
- Số vòng lặp, chạy song song bao nhiêu: <...>

## Tài nguyên
- Cluster: configs/cluster-local.yaml (1 máy, 23 GB RAM, 1 slot chipyard)
  hoặc cluster-lan.yaml nếu có máy remote.
- Cần thêm worker/image nào thì đề xuất trước.

## Tiêu chí hoàn thành
- <Ví dụ: loop chạy hết N vòng, in bảng kết quả, lưu CSV; profile html.>

## Cách làm việc
- Liệt kê file sẽ tạo/sửa để tôi duyệt trước khi viết.
- Bạn không chạy được lệnh trên máy tôi: đưa lệnh, tôi chạy rồi gửi log.
- Làm từng bước nhỏ, chạy được rồi mới mở rộng (bypass/cache để thử orchestration rẻ).
- Cập nhật CLAUDE.md khi xong mỗi mốc.
```

## Ví dụ 1: so sánh hiệu năng các cấu hình core

```
Đọc CLAUDE.md và docs/usage-local.md trước.

## Mục tiêu
Viết loop CHIA so sánh hiệu năng RocketConfig và TinyRocketConfig trên vài benchmark
nhỏ: build simulator cho từng config, chạy từng benchmark, đo số cycle, xuất bảng.

## Phạm vi
- Config: RocketConfig, TinyRocketConfig.
- Benchmark: 3 chương trình C tự viết (matmul 16x16, quicksort 1000 phần tử, dhrystone-lite),
  đọc cycle bằng rdcycle trong chương trình và in ra.
- Không dùng agent.

## Tài nguyên
- configs/cluster-local.yaml. Chỉ có 1 slot chipyard nên các build chạy tuần tự;
  tăng verilator_run lên 3 slot để chạy sim song song (đề xuất sửa yaml trước).

## Tiêu chí hoàn thành
- `chia job submit ... python bench_loop.py` in bảng config × benchmark × cycles,
  ghi results.csv, bật profiler và xuất timeline html.
- Lần chạy thứ hai dùng cache cho build (_chia_tag theo tên config).

## Cách làm việc
- Liệt kê file để tôi duyệt trước. Đưa lệnh cho tôi chạy, tôi gửi log.
```

## Ví dụ 2: agent thiết kế một accelerator

```
Đọc CLAUDE.md, docs/usage-local.md và examples/rtl_agent_loop.py trước.

## Mục tiêu
Dựa trên rtl_agent_loop.py, làm một loop để agent thêm một RoCC accelerator
"vector add" (cộng 2 mảng int32 trong bộ nhớ) vào Rocket trong Chipyard,
viết test C gọi lệnh custom, build và mô phỏng, phản hồi lỗi cho tới khi test pass.

## Phạm vi
- Agent: OpenCode big-pickle (sau này thử ClaudeCodeLLM), timeout 1800 s.
- Agent không tự build; loop build và chạy test, đưa log lỗi vào vòng sau.
- Tối đa 5 vòng. Mỗi vòng lưu git diff của generators/ thành patch trên host.

## Tiêu chí hoàn thành
- Test in "VADD PASS" và kết quả đúng với bản tính phần mềm.
- Patch cuối cùng lưu trong thư mục project, kèm README ngắn cách áp lại.

## Cách làm việc
- Bước 1 chỉ dựng khung loop và test với bypass cho phần LLM; tôi duyệt rồi mới
  bật agent thật. Liệt kê file trước khi tạo.
```

## Mẹo

- **Chia mốc nhỏ.** Ví dụ: (1) dựng khung loop và chạy bằng bypass; (2) thêm build và sim
  thật; (3) bật agent; (4) chạy song song hoặc trên nhiều máy. Mỗi mốc bảo session dừng
  lại chờ log của bạn.
- **Nêu rõ giới hạn máy.** Máy bạn có 23 GB RAM và 1 slot chipyard. Nói trước để session
  không thiết kế loop build nhiều cấu hình song song trên một máy.
- **Chỉ định đúng tài liệu** liên quan: `usage-remote.md` khi dùng nhiều máy, case study
  trong `~/work/chia/examples/<tên>` nếu project gần giống một case study có sẵn (ví dụ
  `timing_opt`, `riscv_extensions`, `gem5_align`).
- **Gửi log đầy đủ** khi có lỗi: lệnh đã chạy, output của `chia status`, và đoạn log từ
  chỗ bắt đầu lỗi.
- **Repo hoặc nhánh riêng:** nếu project lớn, bảo session tạo thư mục `projects/<tên>/`
  trong repo này, hoặc dùng một nhánh mới. Như vậy code project không lẫn với tài liệu setup.
- **Kết thúc mỗi session** bằng câu: "Cập nhật CLAUDE.md: đã làm gì, đã kiểm chứng gì,
  việc tiếp theo". Session sau sẽ đọc file này để tiếp tục.
