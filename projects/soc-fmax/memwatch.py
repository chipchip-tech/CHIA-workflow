"""Đo RAM đỉnh theo từng bước: luồng nền trên host đọc `docker stats` mỗi `interval` giây.

  mw = MemWatch(prefix="chia-"); mw.start()
  with mw.step("build"): ...
  mw.peaks()  -> {"build": {"chia-chipyard-dingus-0": 9.8, ...}, ...}   (GB)

Driver của CHIA chạy trên host nên gọi được `docker`. Lỗi của docker chỉ làm thiếu số, không làm dừng loop.
"""
from __future__ import annotations

import contextlib
import re
import subprocess
import threading
import time

_UNITS = {"b": 1e-9, "kib": 1024 / 1e9, "kb": 1e-6, "mib": 1024 ** 2 / 1e9, "mb": 1e-3,
          "gib": 1024 ** 3 / 1e9, "gb": 1.0, "tib": 1024 ** 4 / 1e9}


def _gb(text: str) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text)
    if not m:
        return None
    return float(m.group(1)) * _UNITS.get(m.group(2).lower(), 0.0)


class MemWatch:
    def __init__(self, prefix: str = "chia-", interval: float = 5.0):
        self.prefix, self.interval = prefix, interval
        self._peaks: dict[str, dict[str, float]] = {}
        self._step = "idle"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self.error = ""

    def start(self) -> "MemWatch":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _sample(self) -> dict[str, float]:
        p = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}}\t{{.MemUsage}}"],
                           capture_output=True, text=True, timeout=60)
        if p.returncode != 0:
            self.error = p.stderr.strip()[-300:]
            return {}
        out = {}
        for line in p.stdout.splitlines():
            name, _, use = line.partition("\t")
            if name.startswith(self.prefix):
                g = _gb(use.split("/")[0])
                if g is not None:
                    out[name] = g
        return out

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                s = self._sample()
            except Exception as e:          # noqa: BLE001 - chỉ là số đo phụ
                self.error = str(e)[-300:]
                s = {}
            with self._lock:
                d = self._peaks.setdefault(self._step, {})
                for k, v in s.items():
                    d[k] = max(d.get(k, 0.0), v)
                tot = sum(s.values())
                d["_tổng"] = max(d.get("_tổng", 0.0), tot)
            self._stop.wait(self.interval)

    @contextlib.contextmanager
    def step(self, name: str):
        prev = self._step
        with self._lock:
            self._step = name
        t0 = time.time()
        try:
            yield
        finally:
            with self._lock:
                self._peaks.setdefault(name, {})["_phút"] = round((time.time() - t0) / 60, 2)
                self._step = prev

    def peaks(self) -> dict:
        with self._lock:
            return {k: {n: round(v, 2) for n, v in d.items()} for k, d in self._peaks.items()}
