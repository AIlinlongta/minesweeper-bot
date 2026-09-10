# -*- coding: utf-8 -*-
"""m4 脚本级验收（阶段0 附加实验）：LLM 问答式决策全闭环（§6.2 变体）。

  A 全闭环：runs 局全部正常结算（won/lost，无 error/stuck/restarted）
  B 决策有效率：最终动作由 LLM 决定的比例 = 1 - fallback/actions ≥ 阈值
  C 兜底压力冒烟：stub_error_rate=0.5 下 pressure-runs 局仍全闭环（信息项）
  D LED 对账 reconcile_fail = 0

默认桩模式（config/llm.yaml brain=stub，可注入畸形率）；真端点接入后
（brain=openai + 环境变量密钥）同脚本复跑即真 LLM 闭环。
输出 scripts/_m4_eval.txt；A/B/D 全过 exit 0。"""
import argparse
import sys
from pathlib import Path

import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

import main as main_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20260910)
    ap.add_argument("--stub-error-rate", type=float, default=0.05)
    ap.add_argument("--min-llm-rate", type=float, default=0.95)
    ap.add_argument("--pressure-runs", type=int, default=3)
    args = ap.parse_args()
    out = open(ROOT / "scripts/_m4_eval.txt", "w", encoding="utf-8")

    def log(*a):
        print(*a)
        print(*a, file=out)

    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    with open(ROOT / "config/llm.yaml", encoding="utf-8") as f:
        llm_cfg = yaml.safe_load(f) or {}
    cfg["brain"] = "llm"
    llm_cfg["stub_error_rate"] = args.stub_error_rate
    cfg["llm"] = llm_cfg

    def aggregate(report):
        details = report["details"]
        asked = sum(d.get("llm_stats", {}).get("asked", 0) for d in details)
        invalid = sum(d.get("llm_stats", {}).get("invalid", 0) for d in details)
        fallback = sum(d.get("llm_stats", {}).get("fallback", 0) for d in details)
        actions = sum(d["cycles"] for d in details)
        llm_rate = 1 - fallback / max(1, actions)
        return asked, invalid, fallback, actions, llm_rate

    # ---- 常规畸形率 ----
    report = main_mod.run_m3(cfg, runs=args.runs, seed=args.seed,
                             log=lambda *a: log(*a))
    asked, invalid, fallback, actions, llm_rate = aggregate(report)
    settled = [d for d in report["details"] if d["result"] in ("won", "lost")]
    a_ok = report["runs"] == args.runs and len(settled) == args.runs
    b_ok = llm_rate >= args.min_llm_rate
    d_total = sum(d["reconcile_fail"] for d in report["details"])
    d_ok = d_total == 0
    log("")
    log("=== m4 acceptance (LLM closed-loop, stub) ===")
    log(f"[A] settled {len(settled)}/{args.runs} all won/lost: "
        f"{'PASS' if a_ok else 'FAIL'}")
    log(f"[B] llm_rate={llm_rate:.4f} (asked={asked} invalid={invalid} "
        f"fallback={fallback} actions={actions}) >= {args.min_llm_rate}: "
        f"{'PASS' if b_ok else 'FAIL'}")
    log(f"[D] reconcile_fail total={d_total}: {'PASS' if d_ok else 'FAIL'}")
    for d in report["details"]:
        st = d.get("llm_stats", {})
        log(f"    run {d['run']:2d}: {d['result']:7s} cycles={d['cycles']:3d} "
            f"asked={st.get('asked', 0):3d} invalid={st.get('invalid', 0):3d} "
            f"fallback={st.get('fallback', 0):2d} "
            f"rec_fail={d['reconcile_fail']}")

    # ---- 兜底压力冒烟（信息项）----
    cfg["llm"]["stub_error_rate"] = 0.5
    preport = main_mod.run_m3(cfg, runs=args.pressure_runs, seed=args.seed + 1,
                              log=lambda *a: log(*a))
    pasked, pinv, pfb, pacts, prate = aggregate(preport)
    p_settled = [d for d in preport["details"] if d["result"] in ("won", "lost")]
    p_ok = len(p_settled) == args.pressure_runs
    log("")
    log(f"[C] pressure (stub_error_rate=0.5) settled {len(p_settled)}/"
        f"{args.pressure_runs} llm_rate={prate:.4f} "
        f"(asked={pasked} fallback={pfb}): "
        f"{'PASS' if p_ok else 'FAIL'} (info)")

    verdict = a_ok and b_ok and d_ok and p_ok
    log(f"eval_m4: {'PASS' if verdict else 'FAIL'}")
    out.close()
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
