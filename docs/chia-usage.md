# Hướng dẫn dùng CHIA

Tài liệu tóm tắt cách viết và chạy workflow bằng CHIA, đúc kết từ docs chính thức
(`~/work/chia/docs/`), mã nguồn `chia/` và những gì đã chạy được trên Bazzite. Cài đặt:
xem [setup-bazzite.md](setup-bazzite.md). Thêm máy: xem [remote-worker.md](remote-worker.md).

Mục lục:
1. [Mô hình tư duy](#1-mô-hình-tư-duy)
2. [Vòng làm việc hằng ngày (CLI)](#2-vòng-làm-việc-hằng-ngày-cli)
3. [Viết node: `@ChiaFunction`](#3-viết-node-chiafunction)
4. [Resources và thiết kế cluster](#4-resources-và-thiết-kế-cluster)
5. [Agent: LLM + ChiaTool](#5-agent-llm--chiatool)
6. [Các node có sẵn](#6-các-node-có-sẵn)
7. [Cache và bypass](#7-cache-và-bypass)
8. [Profiling](#8-profiling)
9. [Loop mẫu: agent sửa RTL có phản hồi](#9-loop-mẫu-agent-sửa-rtl-có-phản-hồi)
10. [Lỗi hay gặp](#10-lỗi-hay-gặp)

---

## 1. Mô hình tư duy

Một dự án CHIA gồm hai phần:

| Phần | Là gì | Viết ở đâu |
|---|---|---|
| **Loop** | Script Python mô tả *làm gì*: các tác vụ (node) và dữ liệu chảy giữa chúng | `my_loop.py` |
| **Cluster** | *Chạy ở đâu*: máy vật lý, logical worker (thường là container) và resource của chúng | `cluster.yaml` |

Từ vựng (mượn của Ray):

- **Driver**: tiến trình chạy loop (`chia job submit -- python my_loop.py`), nằm trên head.
- **Worker**: tiến trình thực thi task. Mỗi logical worker quảng bá *resources*, ví dụ
  `{"chipyard": 1}`.
- **Task**: một lần gọi `fn.chia_remote(...)`. Không giữ trạng thái giữa các lần gọi.
- **Actor**: worker có trạng thái. CHIA dùng actor cho tool server MCP, cache và profiler.
- **ObjectRef**: "future" mà `chia_remote` trả về ngay. Lấy giá trị bằng `get(ref)`, hoặc
  truyền thẳng ref vào node khác để tạo cạnh phụ thuộc.
- **Resources**: nhãn kèm số lượng. Task yêu cầu `{"chipyard": 1}` sẽ chờ tới khi có worker
  còn trống `chipyard` thì mới chạy. **Đây là cách duy nhất để chọn task chạy ở đâu.**

Có hai loại cạnh (edge) trong đồ thị:
- **Lập trình (programmatic):** bạn tự gọi `node.chia_remote(...)` theo thứ tự trong code.
- **Agentic:** bạn đưa cho agent các `ChiaTool` (MCP), và agent tự quyết gọi tool nào, lúc nào.

## 2. Vòng làm việc hằng ngày (CLI)

```bash
conda activate chia_env
export THIS_MACHINE=...            # hoặc HEAD_IP/REMOTE_IP cho cluster LAN

chia up configs/cluster-local.yaml -y          # bật cluster
chia up --add configs/cluster-local.yaml -y    # thêm worker mới/đã chết, giữ worker đang chạy
chia status                                    # = ray status: node, resources đang dùng
chia list nodes                                # = ray list nodes

chia job submit --working-dir . -- python my_loop.py             # chạy loop, xem log trực tiếp
chia job submit --no-wait --submission-id exp1 --working-dir . -- python my_loop.py
chia job logs -f exp1                          # xem lại log
chia job stop exp1 --kill-tracked-pids         # dừng và giết cả subprocess (make, verilator...)
chia job list

chia viz my_loop.py                            # vẽ đồ thị node tĩnh từ source (svg)
chia viz-profile /tmp/chia_profiles/*.jsonl --format html   # timeline từ profile

chia down configs/cluster-local.yaml -y        # tắt hết
```

- Dashboard: <http://127.0.0.1:8265> (Jobs, Cluster, Logs từng task).
- `--working-dir .` đóng gói thư mục hiện tại lên cluster. Driver và mọi worker đều thấy
  các file `.py` trong đó, nên có thể `import` module của bạn trong node.
  **Không** để file lớn (build, waveform) trong thư mục này.
- `Ctrl+C` chỉ ngừng xem log, **job vẫn chạy**. Muốn dừng thì dùng `chia job stop`.

## 3. Viết node: `@ChiaFunction`

```python
from chia.base.ChiaFunction import ChiaFunction, get

@ChiaFunction(resources={"chipyard": 1})          # chỉ chạy trên worker có "chipyard"
def compile_program(src: str) -> bytes:
    ...                                           # code này chạy BÊN TRONG container
    return elf_bytes
```

Bốn cách gọi:

| Cách gọi | Chạy ở đâu | Trả về |
|---|---|---|
| `compile_program(src)` | Ngay trong driver, như hàm thường (vẫn được profile) | giá trị |
| `compile_program.chia_remote(src)` | Worker phù hợp, **không chặn** | `ObjectRef` |
| `compile_program.chia_remote_blocking(src)` | Worker phù hợp, chặn | giá trị |
| `compile_program.options(num_cpus=4).chia_remote(src)` | Như trên, ghi đè tuỳ chọn cho riêng lần gọi này | `ObjectRef` |

Node là **method của class** (đa số node có sẵn) thì phải truyền `self` tường minh:

```python
cb = ChiselBuildNode("/home/ray/chipyard", "RocketConfig")
ref = cb.build.chia_remote(cb)        # chú ý: truyền cb
```

**Nối node thành đồ thị**: truyền thẳng `ObjectRef` vào node sau, không cần `get()`. Ray
tự chờ input sẵn sàng, còn các nhánh độc lập thì chạy song song:

```python
bin_ref   = compile_program.chia_remote(src)        # chạy song song...
build_ref = cb.build.chia_remote(cb)                # ...với cái này
res = get(sim.run.chia_remote(sim, build_ref, bin_ref, "t.riscv", "/home/ray"))
```

**Fan-out / fan-in** (ví dụ chạy 100 test):

```python
refs = [sim.run.chia_remote(sim, build_ref, elf, f"t{i}.riscv", "/home/ray")
        for i, elf in enumerate(elfs)]
results = get(refs)                   # get() nhận cả list
```

Các tuỳ chọn hay dùng trong `@ChiaFunction(...)` hoặc `.options(...)`:
- `resources`: nhãn mà worker phải có.
- `num_cpus`: số CPU dành cho task (mặc định 1). Đặt `0.1` cho task nhẹ.
- `max_retries`: số lần chạy lại khi worker chết (mặc định 3). Đặt `0` cho task có tác dụng phụ.
- `scheduling_strategy="SPREAD"`: rải task đều ra các node.

Công cụ nâng cao:
- `chia_wait(tracked, num_returns=1, pending_timeout=120, retry=True)` với `TrackedRef`:
  xử lý task nào xong trước thì làm trước, và tự gửi lại task bị kẹt.
- `chia_cancel(ref, force=True)`: huỷ task và giết các subprocess nó đã sinh.
- `_chia_setup=` và `_chia_cleanup=` khi gọi: chạy hook trên worker trước và sau node.

**Quy tắc vàng:** truyền **dữ liệu** (str, bytes, dataclass) giữa các node, không truyền
**đường dẫn file**. Mỗi container có filesystem riêng, nên đường dẫn ở worker này không
tồn tại ở worker kia. Các node có sẵn đều tuân theo quy tắc này, ví dụ `BuildArtifact`
chứa nguyên bytes của simulator.

## 4. Resources và thiết kế cluster

Mỗi node type trong `cluster.yaml` là một loại logical worker:

```yaml
available_node_types:
    verilator:
        resources: {"verilator_run": 4}     # mỗi container nhận tối đa 4 task verilator_run:1 cùng lúc
        num_workers: 2                      # 2 container
        compatible_ips: ["192.168.1.3", "192.168.1.20"]
        docker:
            image: "ghcr.io/ucb-bar/chia-verilator-run:latest"
            container_name: "chia-verilator-${USER}"
```

Cách nghĩ về resource:

- **Số lượng là "slot", không phải CPU thật.** Ví dụ `{"verilator_run": 4}` nghĩa là một
  container chạy được 4 simulator cùng lúc. Ray vẫn tính thêm `num_cpus` riêng.
- **Phân số để dùng chung:** các LLM node yêu cầu `opencode_creds: 0.01`, nên một worker
  `{"opencode_creds": 1}` chạy được tới 100 prompt song song. Ngược lại, `chipyard: 1`
  chiếm trọn container.
- **Resource riêng theo máy để ghim task:** đặt thêm `cy_head` hoặc `cy_remote`
  (xem `configs/cluster-lan.yaml`).
- **Tên resource của node có sẵn là cố định.** Worker phải quảng bá đúng tên mà node yêu cầu:

| Node / LLM | Resource cần |
|---|---|
| `ChiselBuildNode.build`, `BashTool` mặc định trong tutorial | `chipyard` |
| `VerilatorRunNode.run` | `verilator_run` |
| `OpenCodeLLM` / `ClaudeCodeLLM` / `CodexLLM` / `CopilotLLM` / `AntigravityLLM` | `opencode_creds` / `claude_creds` / `codex_creds` / `copilot_creds` / `antigravity_creds` |
| `OpenAILLM` / `OpenRouterLLM` / `GroqLLM` / `FireworksLLM` / `NvidiaLLM` | `openai_creds` / `openrouter_creds` / `groq_creds` / `fireworks_creds` / `nvidia_nim_creds` |
| `BedrockLLM` / `VertexGeminiLLM` / `OllamaLLM` / `VLLMLLM` | `bedrock_creds` / `vertex_creds` / `ollama_creds` / `vllm_creds` |

Muốn xem node yêu cầu resource gì, đọc dòng `@ChiaFunction(resources=...)` trong mã
nguồn (`~/work/chia/chia/...`).

### Ghim tác vụ vào cùng một worker

Agent sửa RTL qua `BashTool` trong container chipyard **A**. Nếu bước build sau đó rơi
vào container chipyard **B**, bản build sẽ không có thay đổi. Khi có nhiều worker
chipyard, phải ghim cả tool lẫn build vào cùng một worker:

```python
pin = {"chipyard": 1, "cy_remote": 0.01}
bash = BashTool("chipyard_bash", "/home/ray/chipyard", task_options={"resources": pin})
...
bash.stop()                                            # nhả slot chipyard
art_ref = cb.build.options(resources=pin).chia_remote(cb)
```

Lưu ý thêm:
- Tool đang chạy vẫn **giữ** resource. Phải `bash.stop()` trước khi build, nếu không
  build sẽ chờ mãi vì không còn slot chipyard.
- Mọi chỉnh sửa trong container mất khi `chia down`. Muốn giữ lại, cho agent
  `git diff > /tmp/x.patch` rồi đọc patch về qua một node, hoặc mount volume bằng
  `run_options: ["-v /data/cy:/home/ray/work"]`.

## 5. Agent: LLM + ChiaTool

### Gọi LLM

```python
from chia.models.opencode import OpenCodeLLM          # cần worker {"opencode_creds": ...}
llm = OpenCodeLLM(model="opencode/big-pickle", system_message="...", timeout_seconds=1800)
resp = get(llm.prompt.chia_remote(llm, "Câu hỏi...", tools=[...]))
print(resp.result, resp.success)       # QueryResult: result, returncode, stderr, stream_result, success
```

- `timeout_seconds` mặc định là **600 giây cho mỗi lần thử**, thử tối đa `retries=3`
  lần. Ở step 4 Hello World, agent tự build thử nên vượt 600 giây hai lần. Hãy tăng
  timeout, hoặc dặn agent "đừng tự build".
- Đổi sang Claude Code: `from chia.models.claude import ClaudeCodeLLM`, dùng
  `ClaudeCodeLLM(model="...")` với worker image `ghcr.io/ucb-bar/chia-claude-code` có
  resource `claude_creds` và mount `~/.claude` (xem setup-bazzite mục 6).
- Gọi LLM qua API (không qua CLI agent): `OpenAILLM`, `OpenRouterLLM`, `OllamaLLM`,...
  trong `chia/models/openai_providers.py` và `ollama.py`. Các lớp này cũng nhận `tools=`.

### Tool cho agent (MCP)

**Tạo một `ChiaTool` là đã deploy nó:** server MCP lên ngay trên worker khớp
`task_options`.

```python
from chia.base.tools.BashTool import BashTool
bash = BashTool("chipyard_bash", "/home/ray/chipyard",        # name, work_dir
                timeout_seconds=120,                          # giới hạn MỖI lệnh
                task_options={"resources": {"chipyard": 1}})
resp = get(llm.prompt.chia_remote(llm, "Dùng chipyard_bash để ...", tools=[bash]))
bash.stop()
```

Tự viết tool:

```python
from chia.base.tools.ChiaTool import ChiaTool
from chia.base.ChiaFunction import get

class SimTool(ChiaTool):
    def setup(self, build_ref, binaries):
        self.build_ref, self.binaries = build_ref, binaries
        self.mcp.add_tool(self.run_test, name=f"{self.name}_run_test")

    def run_test(self, test_name: str) -> str:
        """Chạy test `test_name` trên simulator hiện tại và trả về log.

        Args:
            test_name: một trong các tên test có sẵn.
        """
        sim = VerilatorRunNode()
        # Tool gọi lại node: dùng chia_remote_blocking để trả giá trị thật cho agent.
        r = sim.run.chia_remote_blocking(sim, self.build_ref, self.binaries[test_name],
                                         f"{test_name}.riscv", "/home/ray")
        return r.log[-4000:]
```

- **Docstring và type hint là "giao diện"** mà agent đọc, nên viết rõ ràng.
- Tool trả về `str`, `dict`, dataclass hoặc pydantic là tốt nhất.
- Với việc chạy lâu, dùng `AsyncBashTool` hoặc `AsyncJobTool` (kiểu submit + poll) để
  agent không bị timeout.

## 6. Các node có sẵn

| Module | Node | Việc |
|---|---|---|
| `chia.chipyard.chisel_build_node` | `ChiselBuildNode(chipyard_path, config, target=BuildTarget.VERILATOR, make_jobs=32, timeout_seconds=600, ...)` → `.build()` → `BuildArtifact` (`success`, `stdout`, `stderr`, bytes simulator) | Elaborate Chisel và build simulator Verilator |
| `chia.chipyard.verilator_run_node` | `VerilatorRunNode().run(artifact, elf_bytes, name, work_dir, plusargs={}, timeout_cycles=None, capture_waveform=False, ...)` → `RunResult` (`log`, `out`, `returncode`, `success`) | Chạy ELF trên simulator. `out` chứa trace lệnh và `printf` của RTL |
| `chia.chipyard.riscv_build_node` | `RiscvBuildNode` | Biên dịch chương trình RISC-V |
| `chia.chipyard.spike_build_node`, `cosim_node`, `torture_run_node`, `riscv_dv_gen_node`, `riscv_objdump_node`, `firemarshal_node`, `macrocompiler`, `chipyard_hammer`, `circt` | ... | Spike golden model, co-sim, riscv-torture/riscv-dv, objdump, FireMarshal, SRAM macro, Hammer, CIRCT |
| `chia.simulators.gem5`, `chia.simulators.champsim` | `Gem5Node`, ChampSim | Mô phỏng kiến trúc |
| `chia.vlsi.hammer`, `chia.vlsi.sram_cacti` | `HammerNode`, CACTI | Luồng VLSI, đặc tả SRAM |
| `chia.firesim` | `BitstreamBuildNode`, `FireSimManagerNode` | FPGA trên AWS (lệnh `chia firesim-*`) |
| `chia.github` | `GithubIssuesNode`, `GithubPullsNode` | Đọc issue/PR (cần `GITHUB_TOKEN`) |
| `chia.database` | `SQLiteNode`, `PostgresNode` | Lưu kết quả thí nghiệm |

Muốn biết chữ ký đầy đủ, đọc docstring trong `~/work/chia/chia/<module>.py` hoặc
`docs/api/*.rst`. Các case study thật nằm trong `~/work/chia/examples/`: `timing_opt`,
`riscv_extensions`, `gem5_align`, `circt_issue_solver`, `memcpy`, `bypass_cache`,...

## 7. Cache và bypass

Hai tính năng này giúp chạy lại thí nghiệm mà không tốn lại build hay LLM:

- `_chia_tag="iter3_opt1"` khi gọi: đặt tên cho lần gọi đó.
- **Cache** (ghi): trong YAML, đặt `cache: {run_verilator_test: true}`. Kết quả thật
  được lưu xuống đĩa theo tag.
- **Bypass** (đọc/thay thế): đặt `bypass: {fn: {bypass: true, tags: ["iter0_.*"]}}` và
  đăng ký provider. Node vẫn được lập lịch qua Ray nhưng trả về dữ liệu đã ghi sẵn. Dùng
  để thử logic orchestration mà không tốn tiền LLM hay thời gian build.

```python
from chia.base.cache import start_cache, get_active_cache
from chia.base.bypass import Bypass

start_cache(size=4, units="GB", cache_dir_path="/data/chia_cache", yaml_path="exp.yaml")
bypass = Bypass(yaml_path="exp.yaml")
def from_cache(tag, data_path, *a, **k):
    hit, v = get(get_active_cache().read.chia_remote(tag))
    if not hit: raise KeyError(tag)
    return v
bypass.set_provider("run_verilator_test", from_cache)
bypass.set_cond("run_verilator_test",
                lambda tag, *a, **k: get(get_active_cache().has.chia_remote(tag)))
ref = run_verilator_test.chia_remote(design, _chia_tag=f"iter{i}")
```

Ví dụ chạy được: `~/work/chia/examples/bypass_cache/`. Chạy hai lần, lần đầu cache còn
trống (cold), lần sau đọc từ cache (warm).

## 8. Profiling

```python
import ray
from chia.trace.profiler import start_collector, get_profiler
ray.init(address="auto", ignore_reinit_error=True)
start_collector(log_dir="/tmp/chia_profiles")          # ghi JSONL trên head

@ChiaFunction(resources={"verilator_run": 1})
def run_test(...):
    ...
    get_profiler().add_info({"cycles": cycles})        # gắn số liệu riêng vào sự kiện
```

```bash
chia viz-profile /tmp/chia_profiles/<file>.jsonl --format html     # timeline tương tác
chia viz-profile /tmp/chia_profiles/<file>.jsonl --format table    # CSV thời gian theo hàm
```

## 9. Loop mẫu: agent sửa RTL có phản hồi

`examples/rtl_agent_loop.py` trong repo này mở rộng step 4 của Hello World thành một
vòng lặp thật:

1. Biên dịch song song các test C trên worker `chipyard`.
2. Mỗi vòng: OpenCode sửa Chisel qua `BashTool` (đã ghim), rồi `stop()` tool.
3. `ChiselBuildNode` build trên **cùng** container. Nếu `success=False`, log lỗi được
   đưa vào prompt của vòng sau.
4. `VerilatorRunNode` chạy mọi test song song trên tất cả worker `verilator_run` (cả
   trên máy remote nếu có).
5. Kiểm tra chuỗi mong đợi trong `RunResult.out`. Nếu chưa có, đưa log vào prompt và lặp lại.

```bash
cp ~/CHIA-workflow/examples/rtl_agent_loop.py ~/chia-hello-world/
cd ~/chia-hello-world
chia up ~/CHIA-workflow/configs/cluster-local.yaml -y
chia job submit --working-dir . -- python rtl_agent_loop.py --iters 3
# cluster 2 máy: ghim vào chipyard của remote
chia job submit --working-dir . -- python rtl_agent_loop.py --pin cy_remote
```

Loop mẫu này dùng đúng API trong mã nguồn CHIA 2.0.1, nhưng **chưa được chạy thử**. Nếu
gặp lỗi, gửi log để chỉnh lại. Để viết loop của riêng bạn, thay `TASK`, `EXPECT`,
`TESTS` và config Chipyard (`"RocketConfig"`).

Một số mẫu khác dễ làm từ đây:
- **Tìm kiếm song song:** chạy N agent cùng lúc, mỗi agent ghim vào một container chipyard
  riêng, rồi chọn bản tốt nhất theo số chu kỳ (`RunResult.out`).
- **Agent làm orchestrator:** đưa cho agent một `SimTool` (mục 5), để agent tự quyết
  chạy test nào.
- **Quét cấu hình (sweep):** dùng nhiều `ChiselBuildNode` với các `config` khác nhau,
  cùng fan-out qua nhiều worker.

## 10. Lỗi hay gặp

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `No available node types can fulfill resource request {'xxx': ...}` | Không worker nào quảng bá resource `xxx`. Thêm node type vào yaml rồi `chia up --add` |
| Task treo ở PENDING, `ray status` có resource nhưng đang bị dùng hết | Một `ChiaTool` chưa `stop()` vẫn giữ slot, hoặc một job cũ vẫn chạy (`chia job list`) |
| LLM sửa code nhưng build không thấy thay đổi | Tool và build chạy ở hai container khác nhau, xem mục 4 |
| `Timeout on attempt 1/3` ở LLM | Tăng `timeout_seconds` của LLM, hoặc bảo agent không tự build |
| Lệnh agent bị cắt ngang sau 120 giây | `BashTool(timeout_seconds=...)`, hoặc dùng `AsyncBashTool` |
| `FileNotFoundError` cho file vừa tạo ở node trước | Hai node chạy ở hai container. Truyền bytes/str thay vì đường dẫn |
| Job vẫn chạy sau `Ctrl+C` | Đúng như thiết kế. Dùng `chia job stop <id> --kill-tracked-pids` |
| Head chết sau `chia up` (Bazzite) | Thiếu `systemd-run --user --scope` hoặc `loginctl enable-linger`, xem setup-bazzite mục 5 |
