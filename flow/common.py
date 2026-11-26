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
