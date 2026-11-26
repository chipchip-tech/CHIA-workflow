"""orchestrate.py - điều phối task CHIA của một lần chạy: gửi task, đợi (song song khi được), ghi sự kiện cho trang
status, và ba nhánh Sim / FPGA / ASIC dùng chung cho baseline.py và agent.py.

Mỗi task là một mục trong report["tasks"]: {id, name, label, block, round, state (run|ok|bad), t_submit, t_start,
t_end, seconds, host}. t_start/t_end lấy từ worker (flow.common.timed), nên timeline đúng cả khi nhiều task chạy cùng lúc.
"""
from __future__ import annotations

import time
from typing import Callable

import ray
from chia.base.ChiaFunction import get

from flow import tasks_asic as A
from flow import tasks_fpga as F
from flow import tasks_sim as S

# Task -> khối của cluster (resource) chạy nó, và nhãn tiếng Anh cho trang status.
BLOCK = {"sim_build": "chipyard", "sim_run": "verilator", "fpga_sim": "vivado", "fpga": "vivado",
         "asic_gls": "chipyard", "agent": "claude"}
LABEL = {"sim_build": "lint + build", "sim_run": "run testbench", "fpga_sim": "xsim (early check)",
         "fpga": "synth → bitstream · power · GLS", "asic_synth": "synth", "asic_floorplan": "floorplan",
         "asic_place": "place", "asic_cts": "CTS", "asic_route": "route", "asic_finish": "finish (GDS)",
         "asic_drc": "DRC (KLayout)", "asic_lvs": "LVS (KLayout)", "asic_gls": "gate-level sim", "agent": "Claude"}


def block_of(name: str) -> str:
    return BLOCK.get(name, "asic" if name.startswith("asic_") else "?")


def label_of(name: str) -> str:
    if name.startswith("asic_sta_"):
        return f"STA {name.removeprefix('asic_sta_')}"
    return LABEL.get(name, name)


class Runner:
    """Gửi task và đợi kết quả. on_change() được gọi mỗi khi trạng thái đổi (ghi status.html); log(name, res) in một
    dòng cho log của job."""

    def __init__(self, report: dict, on_change: Callable[[], None], log: Callable[[str, dict], None]):
        self.report, self.on_change, self.log = report, on_change, log
        report.setdefault("tasks", [])
        self._refs = {}                                  # id task -> ObjectRef (không ghi vào report)

    def submit(self, name: str, ref, round_: str | None = None) -> dict:
        t = {"id": len(self.report["tasks"]), "name": name, "label": label_of(name), "block": block_of(name),
             "round": round_, "state": "run", "t_submit": time.time()}
        self._refs[t["id"]] = ref
        self.report["tasks"].append(t)
        self.on_change()
        return t

    def wait(self, handles: list[dict]) -> dict[str, dict]:
        """Đợi các task đã gửi; xử lý theo thứ tự xong trước. Trả {tên task: kết quả}. Task raise thì coi là lỗi."""
        pending = {self._refs.pop(h["id"]): h for h in handles}
        out = {}
        while pending:
            ready, _ = ray.wait(list(pending), num_returns=1)
            for ref in ready:
                h = pending.pop(ref)
                try:
                    res = get(ref)
                except Exception as e:                       # noqa: BLE001
                    res = {"ok": False, "log_tail": f"task {h['name']} lỗi: {type(e).__name__}: {e}"}
                h.update({"state": "ok" if res.get("ok") else "bad", "t_start": res.get("t_start", h["t_submit"]),
                          "t_end": res.get("t_end", time.time()), "seconds": res.get("seconds"),
                          "host": res.get("host")})
                out[h["name"]] = res
                self.log(h["name"], res)
                self.on_change()
        return out

    def run(self, name: str, ref, round_: str | None = None) -> dict:
        return self.wait([self.submit(name, ref, round_)])[name]


# ---------------------------------------------------------------------------
# Ba nhánh. Mỗi hàm trả {tên task: kết quả}; dừng ở task đầu tiên không qua gate.
# ---------------------------------------------------------------------------

def run_sim(R: Runner, d: str, cfg: dict, plus: list[str], round_: str | None = None) -> dict:
    sub = {"sim_build": R.run("sim_build", S.sim_build.chia_remote(d, cfg), round_)}
    if sub["sim_build"]["ok"]:
        sub["sim_run"] = R.run("sim_run", S.sim_run.chia_remote(d, cfg, plus), round_)
    return sub


def run_fpga(R: Runner, d: str, cfg: dict, power_cycles: int, early_sim: bool = False,
             round_: str | None = None) -> dict:
    sub = {}
    if early_sim:
        sub["fpga_sim"] = R.run("fpga_sim", F.fpga_sim.chia_remote(d, cfg), round_)
        if not sub["fpga_sim"]["ok"]:
            return sub
    sub["fpga"] = R.run("fpga", F.fpga_build.chia_remote(d, cfg, power_cycles=power_cycles), round_)
    return sub


def run_asic(R: Runner, d: str, cfg: dict, plus: list[str], round_: str | None = None) -> dict:
    """synth → … → finish lần lượt (mỗi bước cần kết quả bước trước), rồi DRC ‖ LVS ‖ STA mỗi corner ‖ GLS cùng lúc."""
    sub = {}
    for st in A.MAKE_STAGES:
        sub[f"asic_{st}"] = R.run(f"asic_{st}", A.asic_stage.chia_remote(d, cfg, st), round_)
        if not sub[f"asic_{st}"]["ok"]:
            return sub
    slot = {"resources": {"asic": cfg["asic"].get("check_slots", 0.25)}}
    hs = [R.submit("asic_drc", A.asic_drc.options(**slot).chia_remote(d, cfg), round_),
          R.submit("asic_lvs", A.asic_lvs.options(**slot).chia_remote(d, cfg), round_)]
    for c in sub["asic_finish"]["sta_corner_list"]:
        hs.append(R.submit(f"asic_sta_{c[0]}", A.asic_sta.options(**slot).chia_remote(d, cfg, c), round_))
    hs.append(R.submit("asic_gls", A.asic_gls.chia_remote(d, cfg, plus), round_))
    done = R.wait(hs)
    order = ["asic_drc", "asic_lvs"] + [h["name"] for h in hs if h["name"].startswith("asic_sta_")] + ["asic_gls"]
    sub.update({k: done[k] for k in order})
    return sub


def asic_merged(sub: dict) -> dict:
    """Số liệu của bước ASIC: finish + DRC + LVS + STA (gộp corner) + GLS."""
    out = {}
    for t in ("asic_finish", "asic_drc", "asic_lvs", "asic_gls"):
        out.update({k: v for k, v in sub.get(t, {}).items()
                    if k not in ("ok", "rc", "seconds", "log_tail", "stage", "t_start", "t_end", "host")})
    out.update(A.sta_summary({k.removeprefix("asic_sta_"): v for k, v in sub.items() if k.startswith("asic_sta_")}))
    out["ok"] = bool(sub) and all(v.get("ok") for v in sub.values())
    return out


def first_failed(sub: dict) -> str | None:
    return next((k for k, v in sub.items() if not v.get("ok")), None)
