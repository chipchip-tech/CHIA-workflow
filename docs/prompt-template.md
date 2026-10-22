# Template prompt cho session mới

Dùng khi mở một session Claude Code mới để làm một project bằng CHIA. Bối cảnh chung
(máy, cách cài, các lỗi đã gặp) đã có trong `CLAUDE.md`, nên prompt chỉ cần tập trung
vào **project**: mục tiêu, phạm vi, tài nguyên, thế nào là "xong", và cách làm việc.

Nhớ mở session trên nhánh `claude/temp`, hoặc bảo session checkout nhánh
đó trước.

## Bước 0: Phỏng vấn để dựng prompt

Khi chưa rõ project cần nêu những gì (resource, worker, tiêu chí "xong", giới hạn máy),
hãy mở một session riêng **chỉ để phỏng vấn và soạn prompt**. Session này chưa viết
code. Kết quả là một prompt hoàn chỉnh theo Template ở dưới, để dán vào session làm
project.

### Prompt phỏng vấn

```
Đọc CLAUDE.md, docs/usage-local.md và docs/prompt-template.md.
Tôi muốn làm một project bằng CHIA nhưng chưa rõ cần nêu những gì.
Hãy phỏng vấn tôi, MỖI LẦN TỐI ĐA 3 CÂU, theo bộ câu hỏi trong prompt-template.md.
Dựa vào câu trả lời, đề xuất phương án (node, resource, worker, image) khi tôi chưa biết.
Khi đủ thông tin, xuất ra MỘT prompt hoàn chỉnh theo Template, kèm danh sách
giả định bạn tự đặt. Chưa viết code hay sửa file nào.
```

### Bộ câu hỏi

| Nhóm | Câu hỏi | Ảnh hưởng tới phần nào của loop |
|---|---|---|
| Mục tiêu | Muốn trả lời câu hỏi gì? Kết quả cuối là gì (bảng số, RTL đã sửa, patch, báo cáo)? | Tiêu chí hoàn thành, định dạng đầu ra |
| Thiết kế | Core/config nào? Có sửa RTL không? Ai sửa: bạn, agent hay không ai? | `ChiselBuildNode` config, có cần `BashTool` và ghim worker không |
| Workload | Chạy chương trình gì? Đã có sẵn hay phải viết? Đo gì (cycle, pass/fail, area/timing)? | Node biên dịch, `VerilatorRunNode`, gem5/Hammer |
| Agent | Có dùng LLM không? Model nào? Có API key/credentials chưa? Bao nhiêu vòng? | `OpenCodeLLM`/`ClaudeCodeLLM`, worker `*_creds`, timeout |
| Quy mô | Bao nhiêu cấu hình × benchmark × vòng? Có cần song song không? | Số slot resource, `num_workers`, fan-out |
| Tài nguyên | Chỉ máy local hay có remote? Thời gian chấp nhận cho mỗi lần chạy? | `cluster-local.yaml` hay `cluster-lan.yaml`, giới hạn RAM |
| Lặp lại / tái lập | Có cần chạy lại rẻ (cache), thử orchestration không tốn tiền (bypass), đo thời gian (profiling)? | `_chia_tag`, cache/bypass, profiler |
| Lưu kết quả | Lưu ở đâu (CSV, SQLite, patch)? Có cần giữ RTL sau khi `chia down`? | Mount volume, lưu patch, `SQLiteNode` |
| Mốc | Mốc nhỏ đầu tiên muốn thấy chạy là gì? | Mốc 1 trong prompt |

### Ví dụ một lượt phỏng vấn

> **Claude:** (1) Bạn muốn trả lời câu hỏi gì, kết quả cuối là gì? (2) Có sửa RTL không,
> ai sửa? (3) Chạy chương trình gì để đo?
>
> **Bạn:** Muốn xem cache L1D lớn hơn giúp matmul nhanh bao nhiêu. Kết quả là bảng cycle.
> Không sửa RTL tay, chỉ đổi config. Chạy matmul 32x32 tự viết.
>
> **Claude:** (4) Mấy kích thước cache? (5) Chỉ máy local? (6) Có cần chạy lại rẻ không?
>
> **Bạn:** 3 kích thước. Chỉ local. Có, muốn đổi benchmark mà không build lại.

Prompt được sinh ra:

```
Đọc CLAUDE.md và docs/usage-local.md trước.

## Mục tiêu
Đo ảnh hưởng của kích thước L1 D-cache tới số cycle của matmul 32x32 trên Rocket.

## Phạm vi
- Config: 3 config Chipyard kế thừa RocketConfig với L1D 16/32/64 KiB
  (đề xuất cách khai báo config mới trong generators/chipyard trước).
- Workload: matmul 32x32 int32, đo bằng rdcycle, in "CYCLES=<n>".
- Không dùng agent.

## Tài nguyên
- configs/cluster-local.yaml: 1 slot chipyard, nên 3 build chạy tuần tự.

## Tiêu chí hoàn thành
- In bảng kích thước L1D × cycles, ghi results.csv.
- Build được cache theo _chia_tag = tên config; đổi benchmark không phải build lại.

## Cách làm việc
- Liệt kê file để tôi duyệt trước. Đưa lệnh, tôi chạy rồi gửi log.

Giả định: config mới thêm vào Chipyard bên trong container và mất khi chia down,
nên loop phải tự ghi file config trước khi build.
```

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

- **Chưa rõ project cần gì** → bắt đầu bằng Bước 0 (phỏng vấn).
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
