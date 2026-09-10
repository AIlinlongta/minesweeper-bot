# -*- coding: utf-8 -*-
"""m2 脚本级验收（§6.2 第二条）：无二选一可解局 100% 全解（纯矩阵，无窗口依赖）。

流程：合成初级盘（9×9/10）批量 → 模拟首击安全（随机 0 格开片）→ solve 主循环
（先落 flags 清单 → 再执行 action）→ 逐局分类：
  win    零 guess 全解（可解局，opened == 非雷格总数）
  guess  出现概率步/并列（certainty<1.0）→ 计入不可解样本，立即停
  loss / error / bad_flag / dup_flag → 求解器缺陷
验收线：缺陷类计数全 0、可解局数 ≥1、可解局 100% 全解（win 即全解闭环）。
另附 16×16/40 信息性冒烟（approx 回退路径不崩即可，不计入验收）。
输出 scripts/_m2_eval.txt；PASS exit 0。"""
import random
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from decision.solver import solve  # noqa: E402
from tests._boardsim import MineHit, flood_reveal, open_view, plant  # noqa: E402

GAMES = 500
SEED = 20260909
SMOKE_GAMES = 50          # 16×16/40 信息性冒烟
CERTAIN_EPS = 1e-9

log = open(ROOT / "scripts/_m2_eval.txt", "w", encoding="utf-8")


def out(msg):
    print(msg, file=log)
    print(msg)


def run_board(mines: set, rows: int, cols: int, mines_total: int, rng: random.Random) -> dict:
    """单局求解闭环：返回 status 与统计字段。"""
    digits = plant(mines, rows, cols)
    zeros = sorted(p for p, d in digits.items() if d == 0)
    opened = flood_reveal(digits, rows, cols, zeros[rng.randrange(len(zeros))])
    flags: set = set()
    steps, reveal_cnt, flag_cnt = 0, 0, 0
    while True:
        if len(opened) == rows * cols - mines_total:
            return {"status": "win", "steps": steps, "reveals": reveal_cnt,
                    "flags": flag_cnt}
        board = open_view(digits, rows, cols, opened, flags)
        try:
            res = solve(board, mines_total)
        except ValueError as e:
            return {"status": "error", "steps": steps, "err": str(e)}
        bad = [f for f in res["flags"] if (f["c"], f["r"]) not in mines]
        if bad:
            return {"status": "bad_flag", "cell": bad[0], "steps": steps}
        dup = [f for f in res["flags"] if (f["c"], f["r"]) in flags]
        if dup:
            return {"status": "dup_flag", "cell": dup[0], "steps": steps}
        for f in res["flags"]:
            flags.add((f["c"], f["r"]))
        flag_cnt += len(res["flags"])
        act = res["action"]
        if act["type"] == "flag":            # action == flags[0]，已落，下轮重解
            continue
        steps += 1
        if act["certainty"] < 1.0 - CERTAIN_EPS:
            return {"status": "guess", "cell": (act["cell"]["c"], act["cell"]["r"]),
                    "reason": act["reason"], "certainty": act["certainty"],
                    "steps": steps, "opened": len(opened)}
        cell = (act["cell"]["c"], act["cell"]["r"])
        if cell in mines:
            return {"status": "loss", "cell": cell, "reason": act["reason"],
                    "certainty": act["certainty"], "steps": steps}
        try:
            opened |= flood_reveal(digits, rows, cols, cell)
        except MineHit:
            return {"status": "loss", "cell": cell, "reason": act["reason"],
                    "certainty": act["certainty"], "steps": steps}
        reveal_cnt += 1


def batch(rows: int, cols: int, mines_total: int, games: int, seed: int,
          tag: str, accept: bool) -> bool:
    """跑一批并汇总；accept=True 时执行 m2 验收断言。"""
    rng = random.Random(seed)
    all_cells = [(c, r) for r in range(rows) for c in range(cols)]
    results, t0 = [], time.perf_counter()
    for i in range(games):
        mines = set(rng.sample(all_cells, mines_total))
        res = run_board(mines, rows, cols, mines_total, rng)
        res["game"] = i
        results.append(res)
    dt = time.perf_counter() - t0
    status = Counter(r["status"] for r in results)
    wins = [r for r in results if r["status"] == "win"]
    guesses = [r for r in results if r["status"] == "guess"]
    hard = {k: status.get(k, 0) for k in ("loss", "error", "bad_flag", "dup_flag")}
    out(f"[{tag}] {rows}x{cols}/{mines_total} games={games} seed={seed} "
        f"time={dt:.2f}s")
    out(f"[{tag}] status={dict(status)}")
    if wins:
        steps = [r["steps"] for r in wins]
        out(f"[{tag}] win steps min/avg/max = {min(steps)}/{sum(steps)/len(steps):.1f}"
            f"/{max(steps)}")
    reasons = Counter(r.get("reason") for r in guesses)
    if guesses:
        cs = [r["certainty"] for r in guesses]
        out(f"[{tag}] guess reasons={dict(reasons)} "
            f"certainty min/avg={min(cs):.3f}/{sum(cs)/len(cs):.3f}")
    for r in results:
        if r["status"] not in ("win", "guess"):
            out(f"[{tag}] DEFECT game{r['game']}: {r}")
    ok = True
    if accept:
        if any(hard.values()):
            ok = False
            out(f"[{tag}] FAIL 求解器缺陷: {hard}")
        if len(wins) < 1:
            ok = False
            out(f"[{tag}] FAIL 可解局数为 0（样本或求解器异常）")
        else:
            out(f"[{tag}] 可解局 {len(wins)}/{games} 全部 100% 全解 "
                f"(opened == {rows * cols - mines_total})")
    else:
        out(f"[{tag}] 信息性冒烟：status 含 win/guess 之外即提示（不计入验收）")
    return ok


def main():
    out(f"m2 eval start {time.strftime('%F %T')}")
    ok_main = batch(9, 9, 10, GAMES, SEED, "M2", accept=True)
    ok_smoke = batch(16, 16, 40, SMOKE_GAMES, SEED + 1, "SMOKE", accept=False)
    overall = ok_main and ok_smoke
    out(f"OVERALL: {'PASS' if overall else 'FAIL'}")
    sys.exit(0 if overall else 1)


if __name__ == "__main__":
    main()
