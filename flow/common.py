"""common.py - tiện ích dùng chung cho mọi task của flow (chạy lệnh, đọc file, ghi thời điểm của task)."""
from __future__ import annotations

import functools
import hashlib
import os
import re
import signal
import subprocess
import time
from pathlib import Path

RESULT_RE = re.compile(r"^RESULT: (PASS|FAIL) (.*)$", re.M)


def judge_log(out: str, cfg: dict) -> tuple[bool, str | None, dict]:
    """PASS/FAIL của testbench theo khai báo project: sim.pass_patterns (mọi mẫu phải có) và sim.fail_patterns (không
    mẫu nào được có); không khai báo thì theo dòng `RESULT: PASS|FAIL k=v ...`. Trả (đạt, dòng kết quả, các cặp k=v)."""
    s = cfg["sim"]
    if s.get("pass_patterns"):
        miss = [p for p in s["pass_patterns"] if not re.search(p, out, re.M)]
        bad = [m.group(0) for p in s.get("fail_patterns") or [] for m in [re.search(p, out, re.M)] if m]
        n_pass = sum(len(re.findall(p, out, re.M)) for p in s["pass_patterns"])
        txt = f"PASS ({n_pass} pass lines)" if not miss and not bad else \
            "FAIL: " + "; ".join(([f"missing /{p}/" for p in miss]) + [f"found {b!r}" for b in bad])
        return not miss and not bad, txt, {}
    m = RESULT_RE.search(out)
    fields = dict(kv.split("=", 1) for kv in m.group(2).split() if "=" in kv) if m else {}
    return bool(m) and m.group(1) == "PASS", (m.group(0) if m else None), fields


def sh(cmd: str, cwd: str, env: dict | None = None, timeout: int = 1800) -> tuple[int, str, float]:
    """Chạy lệnh bash, trả (mã thoát, stdout+stderr, số giây)."""
    t0 = time.time()
    p = subprocess.run(["bash", "-c", cmd], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    return p.returncode, p.stdout + p.stderr, round(time.time() - t0, 2)


def run_logged(cmd: list[str], cwd: str, timeout: int, log_path: str, env: dict | None = None) -> tuple[int, float, str]:
    """Chạy trong process group riêng, log ra file; quá giờ thì giết cả nhóm (make, yosys, openroad)."""
    t0 = time.time()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        p = subprocess.Popen(cmd, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
                             env={**os.environ, **(env or {})})
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            rc = -1
    return rc, round(time.time() - t0, 2), read(log_path)[-3000:]


def read(path) -> str:
    try:
        return open(path, errors="replace").read()
    except OSError:
        return ""


def num(pattern: str, text: str, cast=float):
    m = re.search(pattern, text, re.M)
    return cast(m.group(1)) if m else None


def sha256(paths: list[Path]) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}


def timed(fn):
    """Ghi vào kết quả của task: thời điểm bắt đầu/kết thúc trên worker (t_start, t_end, dùng cho timeline của trang
    status: task chạy song song thì thời điểm driver nhận kết quả không phản ánh lúc task thật sự chạy) và host."""
    @functools.wraps(fn)
    def wrap(*args, **kwargs):
        t0 = time.time()
        res = fn(*args, **kwargs)
        res.setdefault("seconds", round(time.time() - t0, 2))
        res.update({"t_start": t0, "t_end": time.time(), "host": os.uname().nodename})
        return res
    return wrap


def profile_info(**info) -> None:
    """Ghi thêm thông tin vào profiler của CHIA (timeline); không có profiler thì bỏ qua."""
    try:
        from chia.trace.profiler import get_profiler
        get_profiler().add_info(info)
    except Exception:                                            # noqa: BLE001
        pass
