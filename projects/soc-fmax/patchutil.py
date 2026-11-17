"""Tách patch theo submodule Chipyard: dùng chung cho loop.py (driver) và ab_tool.py (khối chipyard).
Module nhẹ, không import ray/chia, để worker nào cũng import được."""
from __future__ import annotations

import re


def split_patch(patch: str, subs: list[str]) -> dict[str, str]:
    """Tách patch theo submodule chứa file (tiền tố dài nhất), viết lại đường dẫn tương đối với submodule đó.
    Khoá "" là repo gốc. Mỗi file cần header `diff --git` hoặc `--- a/...`."""
    head = "diff --git " if re.search(r"^diff --git ", patch, re.M) else "--- "
    sections, cur = [], []
    for line in patch.splitlines(True):
        if line.startswith(head) and cur:
            sections.append(cur)
            cur = []
        cur.append(line)
    if cur:
        sections.append(cur)
    subs = sorted(subs, key=len, reverse=True)
    out: dict[str, list[str]] = {}
    for sec in sections:
        path = None
        for l in sec:
            m = re.match(r"^(?:\+\+\+ b/|--- a/|diff --git a/)(\S+)", l)
            if m and m.group(1) != "/dev/null":
                path = m.group(1)
                break
        if path is None:
            if any(l.strip() for l in sec):
                raise ValueError("patch có đoạn không đọc được đường dẫn file:\n" + "".join(sec[:5]))
            continue
        sub = next((s for s in subs if path == s or path.startswith(s + "/")), "")
        pre = sub + "/" if sub else ""

        def strip(l: str) -> str:
            if not pre:
                return l
            return (l.replace(f" a/{pre}", " a/").replace(f" b/{pre}", " b/")
                    .replace(f"--- a/{pre}", "--- a/").replace(f"+++ b/{pre}", "+++ b/")
                    .replace(f"rename from {pre}", "rename from ").replace(f"rename to {pre}", "rename to "))
        out.setdefault(sub, []).extend(strip(l) if l.startswith(("diff --git", "--- ", "+++ ", "rename ")) else l
                                       for l in sec)
    return {k: "".join(v) for k, v in out.items()}
