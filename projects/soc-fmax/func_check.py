"""func_check: so vết commit của benchmark với baseline, bỏ qua cycle (thêm tầng pipeline đổi cycle, không đổi vết).

Vết lấy từ `+verbose` của Rocket (stderr của simulator; RocketCore.scala, nhánh không có commit log):
  C0: <cycle> [<valid>] pc=[<pc>] W[r<rd>=<wdata>][<wen>] R[r<rs1>=<v1>] R[r<rs2>=<v2>] inst=[<inst>] DASM(<inst>)
Một dòng mỗi lệnh rời pipeline (valid=0 là lệnh gây exception).

So gì (từng lệnh, theo thứ tự):
  - pc, inst, valid phải giống hệt;
  - giá trị ghi W khi cả hai bên có wen=1 và rd ≠ 0 (x0: Rocket in wdata rác; lệnh latency dài ghi về sau, wen=0, không so giá trị);
  - không so R (giá trị nguồn), không so cycle.
Phần phụ thuộc thời gian (không so):
  - trước chương trình: mọi lệnh ở pc < 0x80000000 (bootrom chờ nạp ELF, số vòng tuỳ thời gian);
  - đọc bộ đếm (mcycle, minstret, cycle, time, instret, hpmcounter): giá trị là "nhiễm" (taint). Taint lan qua
    thanh ghi nguyên/thực và bộ nhớ (địa chỉ tính từ giá trị nguồn R + imm, khối 8 byte). Giá trị ghi bị nhiễm
    không so; nhánh hoặc jalr có nguồn nhiễm thì dừng so (luồng lệnh sau đó có thể rẽ khác, hợp lệ);
  - từ lần ghi đầu tiên vào `tohost` (HTIF syscall: printf, exit; vòng chờ fromhost tuỳ thời gian) thì dừng.
Baseline lưu vết đã nén (zlib) trong cache; ứng viên so dòng theo dòng ngay trên worker verilator, không ghi vết
ra đĩa và không chuyển vết qua Ray.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import zlib
from dataclasses import dataclass, field

from chia.base.ChiaFunction import ChiaFunction, get

FUNC_VERSION = "f1"                         # đổi khi đổi cách GHI vết (tag ftrace_)
CHECK_VERSION = "c2"                        # đổi khi đổi cách SO vết (tag fcheck_); c2: bỏ ghi vào x0
FUNC_BENCHES = ("dhrystone", "qsort", "mm", "spmv")
WORK_DIR = "/home/ray/soc_fmax_func"        # trên verilator_run
PROGRAM_BASE = 0x8000_0000

_LINE_RE = re.compile(
    r"^C(\d+):\s+(\d+)\s+\[(\d)\]\s+pc=\[([0-9a-fA-F]+)\]\s+W\[r\s*(\d+)=([0-9a-fA-F]+)\]\[(\d)\]\s+"
    r"R\[r\s*(\d+)=([0-9a-fA-F]+)\]\s+R\[r\s*(\d+)=([0-9a-fA-F]+)\]\s+inst=\[([0-9a-fA-F]+)\]")
_REC = struct.Struct("<QIBBQ")              # pc, inst, valid, rd | 0x80 khi wen, wdata
_MASK64 = (1 << 64) - 1
COUNTER_CSRS = frozenset([0xB00, 0xB02, 0xC00, 0xC01, 0xC02, *range(0xB03, 0xB20), *range(0xC03, 0xC20)])


# ---------------------------------------------------------------------------
# ELF: địa chỉ tohost
# ---------------------------------------------------------------------------

def elf_symbol(elf: bytes, name: str) -> int | None:
    """Giá trị của symbol `name` trong ELF64 little-endian (.symtab), None nếu không có."""
    if elf[:4] != b"\x7fELF" or elf[4] != 2 or elf[5] != 1:
        return None
    shoff, = struct.unpack_from("<Q", elf, 0x28)
    shentsize, shnum = struct.unpack_from("<HH", elf, 0x3A)
    secs = [struct.unpack_from("<IIQQQQIIQQ", elf, shoff + i * shentsize) for i in range(shnum)]
    want = name.encode()
    for s in secs:
        if s[1] != 2:                                   # SHT_SYMTAB
            continue
        strtab = secs[s[6]]
        st_off, st_size = strtab[4], strtab[5]
        for j in range(s[5] // 24):
            st_name, _info, _other, _shndx, value, _size = struct.unpack_from("<IBBHQQ", elf, s[4] + j * 24)
            end = elf.index(b"\0", st_off + st_name, st_off + st_size)
            if elf[st_off + st_name:end] == want:
                return value
    return None


# ---------------------------------------------------------------------------
# Đọc vết
# ---------------------------------------------------------------------------

@dataclass
class Commit:
    pc: int
    inst: int
    valid: int
    rd: int
    wdata: int
    wen: int
    r1: int
    v1: int
    r2: int
    v2: int


def parse_line(line: str) -> Commit | None:
    m = _LINE_RE.match(line)
    if not m:
        return None
    g = m.groups()
    return Commit(pc=int(g[3], 16), inst=int(g[11], 16) & 0xFFFF_FFFF, valid=int(g[2]), rd=int(g[4]),
                  wdata=int(g[5], 16) & _MASK64, wen=int(g[6]), r1=int(g[7]), v1=int(g[8], 16) & _MASK64,
                  r2=int(g[9]), v2=int(g[10], 16) & _MASK64)


def _sext(x: int, bits: int) -> int:
    return x - (1 << bits) if x & (1 << (bits - 1)) else x


class Taint:
    """Taint từ bộ đếm thời gian, theo thanh ghi (x0..x31, f0..f31) và bộ nhớ (khối 8 byte).

    step() trả "stop" khi gặp nhánh/jalr có nguồn nhiễm, "tohost" khi ghi vào tohost, còn lại "ok";
    self.skip_w = True khi giá trị ghi của lệnh này không được so."""

    def __init__(self, tohost: int | None):
        self.x: set[int] = set()
        self.f: set[int] = set()
        self.mem: set[int] = set()
        self.tohost = tohost
        self.skip_w = False

    def step(self, c: Commit) -> str:
        i = c.inst
        op, rd, f3 = i & 0x7F, (i >> 7) & 0x1F, (i >> 12) & 7
        rs1, rs2, rs3 = (i >> 15) & 0x1F, (i >> 20) & 0x1F, (i >> 27) & 0x1F
        tx = lambda r: r != 0 and r in self.x                       # noqa: E731
        self.skip_w = False
        if not c.valid:
            return "ok"

        def set_x(t: bool):
            if rd:
                (self.x.add if t else self.x.discard)(rd)
            self.skip_w = t

        def set_f(t: bool):
            (self.f.add if t else self.f.discard)(rd)

        if op in (0x03, 0x07, 0x23, 0x27, 0x2F):                    # địa chỉ = rs1 + imm (R[0] là rs1)
            imm = 0 if op == 0x2F else _sext((i >> 20) if op in (0x03, 0x07) else (((i >> 25) << 5) | ((i >> 7) & 0x1F)), 12)
            addr = (c.v1 + imm) & _MASK64 if c.r1 == rs1 else None
            blk = addr & ~7 if addr is not None else None
            if op in (0x23, 0x27):                                  # store
                if self.tohost is not None and addr is not None and (addr & ~7) == (self.tohost & ~7):
                    return "tohost"
                t = (op == 0x23 and tx(rs2)) or (op == 0x27 and rs2 in self.f)
                if blk is not None:
                    if t:
                        self.mem.add(blk)
                    elif f3 == 3:
                        self.mem.discard(blk)
                return "ok"
            t = blk is not None and blk in self.mem
            if op == 0x07:
                set_f(t)
            else:
                set_x(t)
                if op == 0x2F and blk is not None:                  # AMO: bộ nhớ nhận rs2 (hoặc kết quả)
                    (self.mem.add if (t or tx(rs2)) else self.mem.discard)(blk)
            return "ok"
        if op == 0x63:                                              # branch
            return "stop" if (tx(rs1) or tx(rs2)) else "ok"
        if op == 0x67:                                              # jalr
            if tx(rs1):
                return "stop"
            set_x(False)
            return "ok"
        if op == 0x73:
            csr = i >> 20
            if f3 and csr in COUNTER_CSRS:
                set_x(True)
            elif f3:
                set_x(False)                                        # CSR khác: không nhiễm
            return "ok"
        if op in (0x37, 0x17, 0x6F):                                # lui, auipc, jal
            set_x(False)
            return "ok"
        if op in (0x13, 0x1B):
            set_x(tx(rs1))
            return "ok"
        if op in (0x33, 0x3B):
            set_x(tx(rs1) or tx(rs2))
            return "ok"
        if op in (0x43, 0x47, 0x4B, 0x4F):                          # FMA
            set_f(rs1 in self.f or rs2 in self.f or rs3 in self.f)
            return "ok"
        if op == 0x53:
            k = (i >> 25) >> 2
            if k in (0x14, 0x18, 0x1C):                             # so sánh, fcvt.int, fmv.x/fclass: đích x
                set_x(rs1 in self.f or (k == 0x14 and rs2 in self.f))
            elif k in (0x1A, 0x1E):                                 # fcvt.f.int, fmv.f.x: nguồn x
                set_f(tx(rs1))
            else:
                set_f(rs1 in self.f or rs2 in self.f)
            return "ok"
        return "ok"


def iter_commits(lines, stats: dict):
    """Lệnh của chương trình (từ lệnh đầu tiên ở pc >= 0x80000000). stats: số dòng, số dòng C0 đọc không được."""
    started = False
    for line in lines:
        stats["lines"] = stats.get("lines", 0) + 1
        if not line.startswith("C"):
            continue
        c = parse_line(line)
        if c is None:
            stats["bad"] = stats.get("bad", 0) + 1
            if len(stats.setdefault("bad_samples", [])) < 3:
                stats["bad_samples"].append(line.rstrip()[:200])
            continue
        if not started:
            if c.pc < PROGRAM_BASE:
                continue
            started = True
        yield c


def record(lines, tohost: int | None) -> tuple[bytes, dict]:
    """Vết baseline: bản ghi nén tới điểm dừng (tohost) hoặc hết vết."""
    stats, out, n = {}, bytearray(), 0
    stop = "hết vết"
    taint = Taint(tohost)
    for c in iter_commits(lines, stats):
        st = taint.step(c)
        if st == "tohost":
            stop = f"ghi tohost ở lệnh {n}"
            break
        out += _REC.pack(c.pc, c.inst, c.valid, c.rd | (0x80 if c.wen else 0), c.wdata)
        n += 1
    stats.update(n=n, stop=stop)
    return zlib.compress(bytes(out), 6), stats


def compare(lines, base_blob: bytes, tohost: int | None) -> tuple[bool, str, dict]:
    """So vết ứng viên với baseline. Trả (đạt, mô tả, thống kê)."""
    base = zlib.decompress(base_blob)
    nb = len(base) // _REC.size
    stats, n, skipped = {}, 0, 0
    taint = Taint(tohost)
    for c in iter_commits(lines, stats):
        st = taint.step(c)
        if st == "tohost":
            stats.update(n=n, skipped_w=skipped)
            return (n == nb, f"dừng ở ghi tohost, lệnh {n}" + ("" if n == nb else f" (baseline {nb})"), stats)
        if st == "stop":
            stats.update(n=n, skipped_w=skipped)
            return True, f"dừng ở nhánh phụ thuộc bộ đếm, lệnh {n}/{nb}", stats
        if n >= nb:
            stats.update(n=n, skipped_w=skipped)
            return False, f"dài hơn baseline ({nb} lệnh); lệnh thừa pc={c.pc:#x} inst={c.inst:#010x}", stats
        pc, inst, valid, rdw, wdata = _REC.unpack_from(base, n * _REC.size)
        bwen, brd = rdw >> 7, rdw & 0x1F
        if (c.pc, c.inst, c.valid) != (pc, inst, valid):
            stats.update(n=n, skipped_w=skipped)
            return False, (f"lệnh {n}: pc/inst khác: baseline pc={pc:#x} inst={inst:#010x} valid={valid}, "
                           f"ứng viên pc={c.pc:#x} inst={c.inst:#010x} valid={c.valid}"), stats
        if bwen and c.wen and brd != 0:          # x0: Rocket in wdata rác (csrw/j có rd=0), không ghi thật
            if taint.skip_w:
                skipped += 1
            elif (brd, wdata) != (c.rd, c.wdata):
                stats.update(n=n, skipped_w=skipped)
                return False, (f"lệnh {n} pc={pc:#x} inst={inst:#010x}: ghi khác: baseline x{brd}={wdata:#x}, "
                               f"ứng viên x{c.rd}={c.wdata:#x}"), stats
        n += 1
    stats.update(n=n, skipped_w=skipped)
    return (n == nb, "hết vết" + ("" if n == nb else f": ngắn hơn baseline ({n}/{nb} lệnh)"), stats)


# ---------------------------------------------------------------------------
# Node: chạy simulator với +verbose, đọc stderr theo dòng
# ---------------------------------------------------------------------------

@dataclass
class TraceResult:
    bench: str
    ok: bool                       # sim xong (rc 0) và vết đọc được (baseline) / vết khớp (ứng viên)
    returncode: int
    message: str
    n: int = 0                     # số lệnh đã ghi/so
    lines: int = 0
    bad_lines: int = 0
    bad_samples: list = field(default_factory=list)
    skipped_w: int = 0
    blob: bytes = b""              # chỉ baseline: vết nén
    digest: str = ""               # sha256 của vết baseline đã giải nén (12 ký tự)
    seconds: float = 0.0
    log_tail: str = ""


@ChiaFunction(resources={"verilator_run": 1}, max_retries=0)
def trace_bench(artifact, elf: bytes, bench: str, base_blob: bytes | None = None, seed: int | None = None,
                max_cycles: int = 200_000_000, timeout_s: int = 7200) -> TraceResult:
    """Chạy một benchmark với +verbose. base_blob=None: ghi vết baseline; có base_blob: so với nó."""
    t0 = time.time()
    task = os.path.join(WORK_DIR, uuid.uuid4().hex[:8])
    os.makedirs(task, exist_ok=True)
    try:
        sim = os.path.join(task, artifact.simulator_binary_name)
        with open(sim, "wb") as f:
            f.write(artifact.simulator_binary_content)
        os.chmod(sim, 0o755)
        for name, content in getattr(artifact, "runtime_libs", ()) or ():
            with open(os.path.join(task, os.path.basename(name)), "wb") as f:
                f.write(content)
        elf_path = os.path.join(task, f"{bench}.riscv")
        with open(elf_path, "wb") as f:
            f.write(elf)
        tohost = elf_symbol(elf, "tohost")
        argv = [sim, "+permissive", f"+max-cycles={max_cycles}", "+verbose"]
        if seed is not None:
            argv.append(f"+verilator+seed+{seed}")
        argv += ["+permissive-off", elf_path]
        env = dict(os.environ)
        env["LD_LIBRARY_PATH"] = task + (":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
        log_path = os.path.join(task, f"{bench}.log")
        with open(log_path, "wb") as logf:
            p = subprocess.Popen(argv, cwd=task, stdout=logf, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                 env=env, text=True, errors="replace", bufsize=1 << 20)
            timer = _Killer(p, timeout_s)
            try:
                if base_blob is None:
                    blob, st = record(p.stderr, tohost)
                    ok_trace, msg = st.get("n", 0) > 0, st["stop"]
                else:
                    ok_trace, msg, st = compare(p.stderr, base_blob, tohost)
                    blob = b""
                    if not ok_trace:            # ứng viên đã sai: không cần chạy tiếp
                        p.kill()
                for _ in p.stderr:              # đọc hết để sim không bị nghẽn ống
                    pass
                rc = p.wait()
            finally:
                timer.cancel()
        log_tail = open(log_path, errors="replace").read()[-1500:]
        if timer.fired:
            msg = f"quá giờ {timeout_s} s; " + msg
        sim_ok = rc == 0 or (base_blob is not None and not ok_trace)
        r = TraceResult(bench=bench, ok=ok_trace and rc == 0 and not timer.fired, returncode=rc,
                        message=msg + ("" if sim_ok else f"; simulator rc={rc}"),
                        n=st.get("n", 0), lines=st.get("lines", 0), bad_lines=st.get("bad", 0),
                        bad_samples=st.get("bad_samples", []), skipped_w=st.get("skipped_w", 0), blob=blob,
                        digest=hashlib.sha256(zlib.decompress(blob)).hexdigest()[:12] if blob else "",
                        seconds=time.time() - t0, log_tail=log_tail)
        if tohost is None:
            r.message += "; ELF không có symbol tohost (so tới hết vết)"
        return r
    finally:
        shutil.rmtree(task, ignore_errors=True)


class _Killer:
    def __init__(self, proc, timeout_s: int):
        import threading
        self.fired = False

        def kill():
            self.fired = True
            proc.kill()
        self._t = threading.Timer(timeout_s, kill)
        self._t.daemon = True
        self._t.start()

    def cancel(self):
        self._t.cancel()


@dataclass
class FuncResult:
    ok: bool
    benches: dict                  # {bench: TraceResult (blob bỏ trống khi trả về ứng viên)}
    seconds: float = 0.0
    version: str = FUNC_VERSION


@ChiaFunction(num_cpus=0, max_retries=0)
def run_func(artifact, elfs: dict, base: "FuncResult | None" = None, seed: int | None = None,
             benches: tuple = FUNC_BENCHES) -> FuncResult:
    """Ghi vết baseline (base=None) hoặc so ứng viên với `base`; các benchmark chạy song song trên verilator_run."""
    import ray
    t0 = time.time()
    art = ray.put(artifact)
    refs = {b: trace_bench.chia_remote(art, elfs["bench"][b], b,
                                       None if base is None else base.benches[b].blob, seed)
            for b in benches}
    res = {b: get(r) for b, r in refs.items()}
    return FuncResult(ok=all(r.ok for r in res.values()), benches=res, seconds=time.time() - t0)


def summary_lines(fr: FuncResult) -> list[str]:
    out = []
    for b, r in fr.benches.items():
        extra = f", {r.bad_lines} dòng C0 đọc không được (vd {r.bad_samples[:1]})" if r.bad_lines else ""
        out.append(f"  {b:10s} {'ĐẠT' if r.ok else 'HỎNG'}  {r.n} lệnh, {r.message}"
                   f"{f', bỏ so {r.skipped_w} giá trị nhiễm' if r.skipped_w else ''}{extra} ({r.seconds / 60:.1f} phút)")
    return out
