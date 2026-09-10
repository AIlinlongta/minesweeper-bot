# -*- coding: utf-8 -*-
"""m3 批量验收（§6.2/§6.3/§6.4）：连续 20 局初级 9x9/10 端到端。

判定线（真值 = 游戏自身胜负渲染，非后门）：
  A 连续 20 局全部正常结算（won/lost；error/stuck/dirty/restarted = FAIL）
  B 通关率 ≥ 80%（含运气局口径）
  C 可解局（全程零猜测：guess_ties=0 且 prob_min=0）100% 通关
  D LED 计数器对账通过率 100%（累计 reconcile_fail == 0）

输出 scripts/_m3_eval.txt；A-D 全过 exit 0。
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from main import run_m3  # noqa: E402

RUNS = 20


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    log_path = ROOT / "scripts/_m3_eval.txt"
    log_file = open(log_path, "w", encoding="utf-8")

    def out(msg):
        print(msg, file=log_file)
        print(msg)

    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    report = run_m3(cfg, runs=args.runs, seed=args.seed,
                    log=lambda m: (log_file.write(str(m) + "\n"),
                                   log_file.flush(), print(m)))
    details = report["details"]

    a_ok = (report["runs"] == args.runs and
            all(d["result"] in ("won", "lost") for d in details))
    b_ok = report["won_rate"] >= 0.8
    solvable = [d for d in details
                if d["guess_ties"] == 0 and d["prob_min"] == 0]
    c_ok = all(d["result"] == "won" for d in solvable)
    d_total = sum(d["reconcile_fail"] for d in details)
    d_ok = d_total == 0

    out("")
    out("=== m3 acceptance (§6.2) ===")
    out(f"[A] settled {report['runs']}/{args.runs} all won/lost: "
        f"{'PASS' if a_ok else 'FAIL'}")
    out(f"[B] win_rate={report['won_rate']:.2f} ({report['won']}/"
        f"{report['runs']}) >= 0.80: {'PASS' if b_ok else 'FAIL'}")
    out(f"[C] solvable games n={len(solvable)} solved="
        f"{sum(1 for d in solvable if d['result'] == 'won')}: "
        f"{'PASS' if c_ok else 'FAIL'}")
    out(f"[D] reconcile_fail total={d_total}: {'PASS' if d_ok else 'FAIL'}")
    for d in details:
        out(f"    run {d['run']:2d}: {d['result']:4s} cycles={d['cycles']:3d} "
            f"guess={d['guess_ties']} prob_min={d['prob_min']} "
            f"rec_fail={d['reconcile_fail']} first={d['first_reason']}")
    out(f"report: {json.dumps({k: v for k, v in report.items() if k != 'details'}, ensure_ascii=False)}")
    log_file.close()
    ok = a_ok and b_ok and c_ok and d_ok
    print(f"eval_m3: {'PASS' if ok else 'FAIL'} -> {log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
