# -*- coding: utf-8 -*-
"""main.py / state.py m4 改动原子修复器：对抗并发回退进程，循环重打直到稳定。

用法：python _fix_main_m4.py  → 退出码 0 = 全部标记在位且复核稳定。"""
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parent.parent
MAIN = ROOT / "main.py"
STATE = ROOT / "perception" / "state.py"

EDITS = [
    ("from decision import solver  # noqa: E402",
     "from decision import llm_solver, solver  # noqa: E402"),
    ('''                if kind == "reveal":
                    action.reveal(c0, r0, win, calib)
                time.sleep(POLL)''',
     '''                if kind == "reveal":
                    action.reveal(c0, r0, win, calib)
                elif attempt:  # flag 重试重点击（首轮已随 flags 落过）
                    action.flag(c0, r0, win, calib)
                time.sleep(POLL)'''),
    ('''    ap.add_argument("--mines", type=int, default=10)
    args = ap.parse_args()
    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    try:''',
     '''    ap.add_argument("--mines", type=int, default=10)
    ap.add_argument("--brain", choices=("logic", "llm"), default="logic",
                    help="决策后端：logic=逻辑求解器（默认），llm=LLM 问答式")
    args = ap.parse_args()
    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    llm_path = ROOT / "config/llm.yaml"
    if llm_path.exists():
        with open(llm_path, encoding="utf-8") as f:
            cfg["llm"] = yaml.safe_load(f) or {}
    cfg["brain"] = args.brain
    try:'''),
]
MARKERS = ["import llm_solver", 'brain") == "llm', "elif attempt", "--brain",
           "llm_stats"]

STATE_EDITS = [
    ('''    flags = sum(1 for c in board if c["state"] == "flag")
    return mines_total - flags == counter_read''',
     '''    flags = sum(1 for c in board if c["state"] == "flag")
    shown = sum(1 for c in board if c["state"] in ("mine", "mine_red"))
    return mines_total - flags - shown == counter_read'''),
]
STATE_MARKERS = ["shown = sum"]


def main() -> int:
    targets = [(MAIN, EDITS, MARKERS), (STATE, STATE_EDITS, STATE_MARKERS)]
    for i in range(30):
        pending = False
        for path, edits, markers in targets:
            txt = path.read_text(encoding="utf-8")
            if not all(m in txt for m in markers):
                pending = True
                for old, new in edits:
                    if old in txt:
                        txt = txt.replace(old, new, 1)
                path.write_text(txt, encoding="utf-8")
        if not pending:
            # 全部就位后再复核一轮，确认未被并发回退
            time.sleep(0.3)
            ok = all(all(m in p.read_text(encoding="utf-8") for m in ms)
                     for p, _, ms in targets)
            if ok:
                print(f"stable after {i + 1} round(s)")
                return 0
    print("FAILED: reverter still active after 30 rounds")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
