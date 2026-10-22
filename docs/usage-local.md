# Dùng CHIA trên một máy (local)

Hướng dẫn dùng CHIA khi mọi thứ chạy trên **một máy Bazzite**: Ray head, driver và tất
cả worker container. Nếu chưa cài đặt, làm theo [setup-bazzite.md](setup-bazzite.md)
trước. Khi muốn thêm máy khác làm worker, xem [usage-remote.md](usage-remote.md).

Nội dung tổng hợp từ docs chính thức (`~/work/chia/docs/`), mã nguồn `chia/` (bản 2.0.1)
và những gì đã chạy thật trên Bazzite. Cluster `configs/cluster-local.yaml` đã chạy
hết 4 bước Hello World. Loop mẫu `examples/rtl_agent_loop.py` **chưa được chạy thử**.

Mục lục:
1. [Kiến trúc một máy](#1-kiến-trúc-một-máy)
2. [Bật, tắt và theo dõi cluster](#2-bật-tắt-và-theo-dõi-cluster)
3. [Chạy job](#3-chạy-job)
4. [Viết node: `@ChiaFunction`](#4-viết-node-chiafunction)
5. [Resources và `cluster-local.yaml`](#5-resources-và-cluster-localyaml)
6. [Agent: LLM + ChiaTool](#6-agent-llm--chiatool)
7. [Các node có sẵn](#7-các-node-có-sẵn)
8. [Cache và bypass](#8-cache-và-bypass)
9. [Profiling](#9-profiling)
10. [Loop mẫu: agent sửa RTL có phản hồi](#10-loop-mẫu-agent-sửa-rtl-có-phản-hồi)
11. [Lỗi hay gặp](#11-lỗi-hay-gặp)

---

## 1. Kiến trúc một máy

```
Máy Bazzite (THIS_MACHINE, ví dụ 192.168.1.3)
│
├─ ~/chia-hello-world/ (hoặc thư mục dự án của bạn)  ← loop .py, file C, ...
├─ conda chia_env: chia CLI + Ray 2.54 (Python 3.10.19)
├─ Ray HEAD (GCS :6379, dashboard :8265)            ← chạy trong systemd-run --user --scope
├─ DRIVER: python my_loop.py                         ← chạy trên host, trong chia_env
│
├─ container chia-chipyard-<user>-0   resources {"chipyard": 1}
│     /home/ray/chipyard: Chipyard + RISC-V gcc + Verilator + sbt/Java
├─ container chia-verilator-<user>-0  resources {"verilator_run": 1}
└─ container chia-opencode-<user>-0   resources {"opencode_creds": 1}
```

Một dự án CHIA gồm hai phần:

| Phần | Là gì | Ở đâu |
|---|---|---|
| **Loop** | Script Python mô tả *làm gì*: các tác vụ (node) và dữ liệu chảy giữa chúng | `my_loop.py`, chạy làm driver |
| **Cluster** | *Chạy ở đâu*: các logical worker (container) cùng resource của chúng | `configs/cluster-local.yaml` |

Từ vựng (mượn của Ray):

- **Driver**: tiến trình chạy loop, nằm trên host.
- **Worker**: tiến trình thực thi task, nằm trong container. Mỗi worker quảng bá *resources*.
- **Task**: một lần gọi `fn.chia_remote(...)`. Không giữ trạng thái giữa các lần gọi.
- **Actor**: worker có trạng thái. CHIA dùng actor cho tool server MCP, cache và profiler.
- **ObjectRef**: "future" mà `chia_remote` trả về ngay. Lấy giá trị bằng `get(ref)`, hoặc
  truyền thẳng ref vào node khác.
- **Resources**: nhãn kèm số lượng. Task yêu cầu `{"chipyard": 1}` chỉ chạy ở container có
  `chipyard` còn trống. **Đây là cách chọn task chạy ở container nào.**

Có hai loại cạnh (edge) trong đồ thị:
- **Lập trình (programmatic):** bạn tự gọi `node.chia_remote(...)` theo thứ tự trong code.
- **Agentic:** bạn đưa cho agent các `ChiaTool` (MCP), và agent tự quyết gọi tool nào, lúc nào.

## 2. Bật, tắt và theo dõi cluster

```bash
conda activate chia_env
export THIS_MACHINE=$(ip route get 1.1.1.1 | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}')
CL=~/CHIA-workflow/configs/cluster-local.yaml

chia up $CL -y              # bật head + 3 worker
chia status                 # = ray status: phải thấy 4 node, resource chipyard/verilator_run/opencode_creds
chia list nodes             # = ray list nodes
chia up --add $CL -y        # sau khi sửa yaml: chỉ tạo worker mới hoặc đã chết, giữ worker đang chạy
chia down $CL -y            # tắt worker rồi tắt head (giải phóng ~20 GB RAM)
```

- Dashboard: <http://127.0.0.1:8265>. Tab Jobs có log, tab Cluster cho biết task chạy ở
  container nào.
- Muốn vào xem bên trong một container: `docker exec -it chia-chipyard-$USER-0 bash`.
- **Mọi thay đổi bên trong container sẽ mất khi `chia down`**, kể cả RTL do agent sửa.
  Muốn trả Chipyard về bản gốc mà không tắt cả cluster:
  `docker rm -f chia-chipyard-$USER-0 && chia up --add $CL -y`.
- Máy có 23 GB RAM thì chỉ nên để **1** worker chipyard: mỗi lần build RocketConfig tốn
  12–16 GB.

## 3. Chạy job

```bash
cd ~/my-project                                          # chứa my_loop.py
chia job submit --working-dir . -- python my_loop.py     # chạy và in log trực tiếp
chia job submit --no-wait --submission-id exp1 --working-dir . -- python my_loop.py --iters 5
chia job logs -f exp1                                    # xem lại log
chia job list
chia job stop exp1 --kill-tracked-pids                   # dừng job và giết các subprocess (make, verilator...)
```

- `--working-dir .` đóng gói thư mục hiện tại lên cluster. Driver và mọi container đều
  thấy các file `.py` trong đó, nên có thể `import` module của bạn bên trong node.
  **Không** để file lớn (build, waveform) trong thư mục này.
- `Ctrl+C` chỉ ngừng xem log, **job vẫn chạy**. Muốn dừng thì dùng `chia job stop`.
- `chia viz my_loop.py` vẽ đồ thị các node từ source code mà không cần chạy.

## 4. Viết node: `@ChiaFunction`

```python
from chia.base.ChiaFunction import ChiaFunction, get

@ChiaFunction(resources={"chipyard": 1})          # chỉ chạy ở container có "chipyard"
def compile_program(src: str) -> bytes:
    ...                                           # code này chạy BÊN TRONG container chipyard
    return elf_bytes
```

Bốn cách gọi:

| Cách gọi | Chạy ở đâu | Trả về |
|---|---|---|
| `compile_program(src)` | Ngay trong driver, như hàm thường (vẫn được profile) | giá trị |
| `compile_program.chia_remote(src)` | Container phù hợp, **không chặn** | `ObjectRef` |
| `compile_program.chia_remote_blocking(src)` | Container phù hợp, chặn | giá trị |
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

**Chạy nhiều task song song rồi gom kết quả** (fan-out/fan-in):

```python
refs = [sim.run.chia_remote(sim, build_ref, elf, f"t{i}.riscv", "/home/ray")
        for i, elf in enumerate(elfs)]
results = get(refs)                   # get() nhận cả list
```

Trên một máy, số task chạy cùng lúc bằng số slot resource. Ví dụ `{"verilator_run": 1}`
chỉ cho 1 simulator chạy một lúc. Muốn chạy nhiều hơn, xem mục 5.

Các tuỳ chọn hay dùng trong `@ChiaFunction(...)` hoặc `.options(...)`:
- `resources`: nhãn mà worker phải có.
- `num_cpus`: số CPU dành cho task (mặc định 1). Đặt `0.1` cho task nhẹ.
- `max_retries`: số lần chạy lại khi worker chết (mặc định 3). Đặt `0` cho task có tác dụng phụ.

Công cụ nâng cao:
- `chia_wait(tracked, num_returns=1, pending_timeout=120, retry=True)` với `TrackedRef`:
  xử lý task nào xong trước thì làm trước, và tự gửi lại task bị kẹt.
- `chia_cancel(ref, force=True)`: huỷ task và giết các subprocess nó đã sinh.
- `_chia_setup=` và `_chia_cleanup=` khi gọi: chạy hook trên worker trước và sau node.

**Quy tắc vàng:** truyền **dữ liệu** (str, bytes, dataclass) giữa các node, không truyền
**đường dẫn file**. Ngay trên một máy, mỗi container có filesystem riêng: file mà node
chạy trong container chipyard tạo ra sẽ không có trong container verilator. Các node có
sẵn đều tuân theo quy tắc này, ví dụ `BuildArtifact` chứa nguyên bytes của simulator.

## 5. Resources và `cluster-local.yaml`

Mỗi node type trong yaml là một loại container:

```yaml
available_node_types:
    hello_verilator:
        resources: {"verilator_run": 1}     # số slot: bao nhiêu task verilator_run:1 chạy cùng lúc
        num_workers: 1                      # số container
        compatible_ips: ["${THIS_MACHINE}"] # giữ dấu nháy
        docker:
            image: "ghcr.io/ucb-bar/chia-verilator-run:latest"
            container_name: "chia-verilator-${USER}"
```

Cách nghĩ về resource:

- **Số lượng là "slot", không phải CPU thật.** Muốn chạy 4 simulator song song, đổi thành
  `{"verilator_run": 4}` (4 slot trong 1 container) hoặc `num_workers: 4` (4 container),
  rồi chạy `chia up --add`.
- **Phân số để dùng chung:** các LLM node yêu cầu `opencode_creds: 0.01`, nên một container
  `{"opencode_creds": 1}` chạy được tới 100 prompt song song. Ngược lại, `chipyard: 1`
  chiếm trọn container.
- **Thêm loại worker mới:** thêm một node type mới, ví dụ Claude Code (mục 6). Chạy
  `chia up --add`, rồi `chia status` để thấy resource mới.
- **Tên resource của node có sẵn là cố định.** Container phải quảng bá đúng tên mà node yêu cầu:

| Node / LLM | Resource cần |
|---|---|
| `ChiselBuildNode.build`, `BashTool` trong tutorial | `chipyard` |
| `VerilatorRunNode.run` | `verilator_run` |
| `OpenCodeLLM` / `ClaudeCodeLLM` / `CodexLLM` / `CopilotLLM` / `AntigravityLLM` | `opencode_creds` / `claude_creds` / `codex_creds` / `copilot_creds` / `antigravity_creds` |
| `OpenAILLM` / `OpenRouterLLM` / `GroqLLM` / `FireworksLLM` / `NvidiaLLM` | `openai_creds` / `openrouter_creds` / `groq_creds` / `fireworks_creds` / `nvidia_nim_creds` |
| `BedrockLLM` / `VertexGeminiLLM` / `OllamaLLM` / `VLLMLLM` | `bedrock_creds` / `vertex_creds` / `ollama_creds` / `vllm_creds` |

Muốn xem node yêu cầu resource gì, đọc dòng `@ChiaFunction(resources=...)` trong
`~/work/chia/chia/...`.

**Tool đang chạy vẫn giữ resource.** `BashTool(task_options={"resources": {"chipyard": 1}})`
chiếm slot chipyard duy nhất. Phải gọi `bash.stop()` trước khi build, nếu không build sẽ
chờ mãi.

Muốn giữ lại chỉnh sửa trong container, chọn một trong hai cách:
- Cho agent chạy `git diff > /tmp/x.patch`, rồi đọc patch về qua một node.
- Mount thư mục host vào container: `run_options: ["-v /var/home/<user>/cy-work:/home/ray/work:z"]`.
  `:z` là cần thiết vì SELinux trên Bazzite.

## 6. Agent: LLM + ChiaTool

### Gọi LLM

```python
from chia.models.opencode import OpenCodeLLM          # cần container {"opencode_creds": ...}
llm = OpenCodeLLM(model="opencode/big-pickle", system_message="...", timeout_seconds=1800)
resp = get(llm.prompt.chia_remote(llm, "Câu hỏi...", tools=[...]))
print(resp.result, resp.success)       # QueryResult: result, returncode, stderr, stream_result, success
```

- `timeout_seconds` mặc định là **600 giây cho mỗi lần thử**, thử tối đa `retries=3`
  lần. Ở step 4 Hello World, agent tự build thử nên vượt 600 giây hai lần. Hãy tăng
  timeout, hoặc dặn agent "đừng tự build".
- Gọi LLM qua API (không qua CLI agent): `OpenAILLM`, `OpenRouterLLM`, `OllamaLLM`,...
  trong `chia/models/openai_providers.py` và `ollama.py`. Các lớp này cũng nhận `tools=`.

**Đổi sang Claude Code.** Đăng nhập `claude` trên host một lần (setup-bazzite mục 6),
rồi thêm node type sau vào yaml:

```yaml
    claude:
        resources: {"claude_creds": 1}
        num_workers: 1
        compatible_ips: ["${THIS_MACHINE}"]
        docker:
            image: "ghcr.io/ucb-bar/chia-claude-code:latest"
            container_name: "chia-claude-${USER}"
            run_options:
                - "--user $(id -u):$(id -g)"
                - "-v ~/.claude:/home/ray/.claude"
            run_setup_commands:
                - echo "user:x:$(id -u):$(id -g)::/home/ray:/bin/bash" >> /etc/passwd
                - cp /home/ray/.claude/.claude.json /home/ray/.claude.json
```

Trong loop dùng `from chia.models.claude import ClaudeCodeLLM` và
`llm = ClaudeCodeLLM(model="...", timeout_seconds=1800)`.

### Tool cho agent (MCP)

**Tạo một `ChiaTool` là đã deploy nó:** server MCP lên ngay trong container khớp
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
from chia.chipyard.verilator_run_node import VerilatorRunNode

class SimTool(ChiaTool):
    def setup(self, build_artifact, binaries):
        self.build_artifact, self.binaries = build_artifact, binaries
        self.mcp.add_tool(self.run_test, name=f"{self.name}_run_test")

    def run_test(self, test_name: str) -> str:
        """Chạy test `test_name` trên simulator hiện tại và trả về log.

        Args:
            test_name: một trong các tên test có sẵn.
        """
        sim = VerilatorRunNode()
        # Tool gọi lại node: dùng chia_remote_blocking để trả giá trị thật cho agent.
        r = sim.run.chia_remote_blocking(sim, self.build_artifact, self.binaries[test_name],
                                         f"{test_name}.riscv", "/home/ray")
        return r.log[-4000:]
```

- **Docstring và type hint là "giao diện"** mà agent đọc, nên viết rõ ràng.
- Tool trả về `str`, `dict`, dataclass hoặc pydantic là tốt nhất.
- Với việc chạy lâu, dùng `AsyncBashTool` hoặc `AsyncJobTool` (kiểu submit + poll) để
  agent không bị timeout.
- Tool server nghe ở cổng `8000+` trên host (container dùng `--net=host`). Đừng để dịch
  vụ khác chiếm dải cổng này.

## 7. Các node có sẵn

| Module | Node | Việc |
|---|---|---|
| `chia.chipyard.chisel_build_node` | `ChiselBuildNode(chipyard_path, config, target=BuildTarget.VERILATOR, make_jobs=32, timeout_seconds=600, ...)` → `.build()` → `BuildArtifact` (`success`, `stdout`, `stderr`, bytes simulator) | Elaborate Chisel và build simulator Verilator. **Build lỗi không raise**, phải kiểm tra `success` |
| `chia.chipyard.verilator_run_node` | `VerilatorRunNode().run(artifact, elf_bytes, name, work_dir, plusargs={}, timeout_cycles=None, capture_waveform=False, ...)` → `RunResult` (`log`, `out`, `returncode`, `success`) | Chạy ELF trên simulator. `log` là stdout chương trình, `out` là trace lệnh và `printf` của RTL |
| `chia.chipyard.riscv_build_node` | `RiscvBuildNode` | Biên dịch chương trình RISC-V |
| `chia.chipyard.spike_build_node`, `cosim_node`, `torture_run_node`, `riscv_dv_gen_node`, `riscv_objdump_node`, `firemarshal_node`, `macrocompiler`, `chipyard_hammer`, `circt` | ... | Spike golden model, co-sim, riscv-torture/riscv-dv, objdump, FireMarshal, SRAM macro, Hammer, CIRCT |
| `chia.simulators.gem5`, `chia.simulators.champsim` | `Gem5Node`, ChampSim | Mô phỏng kiến trúc (image `chia-gem5`, `chia-champsim`) |
| `chia.vlsi.hammer`, `chia.vlsi.sram_cacti` | `HammerNode`, CACTI | Luồng VLSI, đặc tả SRAM |
| `chia.github` | `GithubIssuesNode`, `GithubPullsNode` | Đọc issue/PR (cần `GITHUB_TOKEN`) |
| `chia.database` | `SQLiteNode`, `PostgresNode` | Lưu kết quả thí nghiệm |

Với 23 GB RAM, `make_jobs=32` mặc định vẫn build được RocketConfig. Nếu bị OOM, giảm
xuống: `ChiselBuildNode(..., make_jobs=8)`. Chữ ký đầy đủ nằm trong docstring của
`~/work/chia/chia/<module>.py`. Các case study thật nằm trong `~/work/chia/examples/`.

## 8. Cache và bypass

Hai tính năng này giúp chạy lại thí nghiệm mà không tốn lại build hay LLM:

- `_chia_tag="iter3_opt1"` khi gọi: đặt tên cho lần gọi đó.
- **Cache** (ghi): trong YAML thí nghiệm, đặt `cache: {run_verilator_test: true}`. Kết
  quả thật được lưu xuống đĩa theo tag.
- **Bypass** (đọc/thay thế): đặt `bypass: {fn: {bypass: true, tags: ["iter0_.*"]}}` và
  đăng ký provider. Node vẫn được lập lịch qua Ray nhưng trả về dữ liệu đã ghi sẵn. Dùng
  để thử logic orchestration mà không tốn tiền LLM hay thời gian build.

```python
from chia.base.cache import start_cache, get_active_cache
from chia.base.bypass import Bypass

start_cache(size=4, units="GB", cache_dir_path="/var/home/<user>/chia_cache", yaml_path="exp.yaml")
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

## 9. Profiling

```python
import ray
from chia.trace.profiler import start_collector, get_profiler
ray.init(address="auto", ignore_reinit_error=True)
start_collector(log_dir="/tmp/chia_profiles")          # ghi JSONL trên host

@ChiaFunction(resources={"verilator_run": 1})
def run_test(...):
    ...
    get_profiler().add_info({"cycles": cycles})        # gắn số liệu riêng vào sự kiện
```

```bash
chia viz-profile /tmp/chia_profiles/<file>.jsonl --format html     # timeline tương tác
chia viz-profile /tmp/chia_profiles/<file>.jsonl --format table    # CSV thời gian theo hàm
```

## 10. Loop mẫu: agent sửa RTL có phản hồi

`examples/rtl_agent_loop.py` mở rộng step 4 của Hello World thành một vòng lặp thật:

1. Biên dịch song song các test C trong container chipyard.
2. Mỗi vòng: OpenCode sửa Chisel qua `BashTool`, rồi `stop()` tool để nhả slot chipyard.
3. `ChiselBuildNode` build. Nếu `success=False`, log lỗi được đưa vào prompt của vòng sau.
4. `VerilatorRunNode` chạy mọi test.
5. Kiểm tra chuỗi mong đợi trong `RunResult.out`. Nếu chưa có, đưa log vào prompt và lặp lại.

```bash
cp ~/CHIA-workflow/examples/rtl_agent_loop.py ~/chia-hello-world/
cd ~/chia-hello-world
chia up ~/CHIA-workflow/configs/cluster-local.yaml -y
chia job submit --working-dir . -- python rtl_agent_loop.py --iters 3
```

Trên một máy, giữ `--pin` ở giá trị mặc định `chipyard`. Loop mẫu dùng đúng API trong
mã nguồn CHIA 2.0.1 nhưng **chưa được chạy thử**. Nếu gặp lỗi, gửi log để chỉnh lại.
Để viết loop của riêng bạn, thay `TASK`, `EXPECT`, `TESTS` và config Chipyard
(`"RocketConfig"`).

Một số mẫu khác dễ làm từ đây:
- **Agent làm orchestrator:** đưa cho agent một `SimTool` (mục 6), để agent tự quyết
  chạy test nào.
- **Quét cấu hình (sweep):** dùng nhiều `ChiselBuildNode` với các `config` khác nhau.
  Trên một máy chúng chạy lần lượt, vì chỉ có 1 slot chipyard.
- **So sánh model:** cùng một prompt, chạy với `OpenCodeLLM` và `ClaudeCodeLLM`, rồi so kết quả.

## 11. Lỗi hay gặp

| Triệu chứng | Nguyên nhân / cách sửa |
|---|---|
| `Head node ready` nhưng worker báo `Failed to connect to GCS` | Thiếu `systemd-run --user --scope` hoặc `loginctl enable-linger` (setup-bazzite mục 5) |
| `expected ',' or ']', but got '{'` khi `chia up` | `compatible_ips: [${THIS_MACHINE}]` thiếu dấu nháy |
| `No available node types can fulfill resource request {'xxx': ...}` | Không container nào quảng bá resource `xxx`. Thêm node type vào yaml rồi `chia up --add` |
| Task treo ở PENDING, `chia status` cho thấy resource đã dùng hết | Một `ChiaTool` chưa `stop()` vẫn giữ slot, hoặc một job cũ vẫn chạy (`chia job list`) |
| `Timeout on attempt 1/3` ở LLM | Tăng `timeout_seconds` của LLM, hoặc bảo agent không tự build |
| Lệnh agent bị cắt ngang sau 120 giây | `BashTool(timeout_seconds=...)`, hoặc dùng `AsyncBashTool` |
| `FileNotFoundError` cho file vừa tạo ở node trước | Hai node chạy ở hai container. Truyền bytes/str thay vì đường dẫn |
| Build bị kill giữa chừng, máy đơ | Hết RAM: giảm `make_jobs`, đóng bớt ứng dụng, chỉ giữ 1 worker chipyard |
| Job vẫn chạy sau `Ctrl+C` | Đúng như thiết kế. Dùng `chia job stop <id> --kill-tracked-pids` |
| RTL mất sau khi bật lại cluster | Container bị xoá khi `chia down`. Lưu patch hoặc mount volume (mục 5) |
