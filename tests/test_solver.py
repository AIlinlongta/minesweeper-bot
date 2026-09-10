# -*- coding: utf-8 -*-
"""m2 单测：合成棋盘自洽 + 约束传播 / 0 展开（Step1-2）+ 概率层 / solve 编排（Step3-4）。

用例对应 §6.1：test_solver_propagate_safe / propagate_mine / flood_zeros /
test_solver_probability / test_solver_tie_break。
"""
import random

import pytest

from decision.solver import (board_dims, flood_zeros, neighbors, probability,
                             propagate, solve)
from _boardsim import MineHit, flood_reveal, open_view, plant

# 4×4 手算基准盘：雷在 (3,0)/(3,3)，左 8 格为连通 0 区
FLOOD_MINES = {(3, 0), (3, 3)}
FLOOD_DIGITS = plant(FLOOD_MINES, 4, 4)
FLOOD_OPENED = flood_reveal(FLOOD_DIGITS, 4, 4, (0, 0))
assert len(FLOOD_OPENED) == 12  # 模块加载即自检：0 连片 + 边界数字


def _cells(spec: dict[tuple[int, int], str]) -> list[dict]:
    """手写小盘构造器：{(c,r): state} → SolverCell board（digit 由 state 推导）。"""
    out = []
    for (c, r), st in sorted(spec.items()):
        digit = int(st) if st.isdigit() else None
        out.append({"c": c, "r": r, "state": st, "digit": digit})
    return out


# --------------------------------------------------------------------------- #
# Step1 合成棋盘生成器自洽
# --------------------------------------------------------------------------- #
def test_plant_self_consistent():
    rng = random.Random(42)
    for rows, cols, nmines in [(9, 9, 10), (4, 4, 4), (6, 6, 12)]:
        cells = [(c, r) for r in range(rows) for c in range(cols)]
        mines = set(rng.sample(cells, nmines))
        digits = plant(mines, rows, cols)
        assert set(digits) == set(cells) - mines          # 非雷格全覆盖
        for (c, r), d in digits.items():
            assert 0 <= d <= 8
        # 独立交叉验证（雷心视角）：每颗雷为其相邻非雷格各贡献 1
        mine_centric = 0
        for (mc, mr) in mines:
            mine_centric += sum(1 for p in neighbors(mc, mr, rows, cols) if p not in mines)
        assert sum(digits.values()) == mine_centric


def test_flood_reveal_simulator():
    opened = flood_reveal(FLOOD_DIGITS, 4, 4, (0, 0))
    assert (0, 0) in opened and (2, 1) in opened          # 0 区 + 边界数字
    assert (3, 0) not in opened and (3, 3) not in opened  # 雷格不可翻开
    assert flood_reveal(FLOOD_DIGITS, 4, 4, (2, 0)) == {(2, 0)}   # 数字格单开
    with pytest.raises(MineHit):
        flood_reveal(FLOOD_DIGITS, 4, 4, (3, 0))


def test_open_view_contract():
    digits = plant({(1, 1)}, 3, 3)
    board = open_view(digits, 3, 3, opened={(0, 0)}, flags={(2, 2)})
    assert len(board) == 9
    by_pos = {(x["c"], x["r"]): x for x in board}
    assert by_pos[(0, 0)] == {"c": 0, "r": 0, "state": "1", "digit": 1}
    assert by_pos[(2, 2)] == {"c": 2, "r": 2, "state": "flag", "digit": None}
    assert by_pos[(1, 1)] == {"c": 1, "r": 1, "state": "covered", "digit": None}
    assert len(by_pos) == 9                               # 全盘无缺格


def test_board_dims():
    assert board_dims([]) == (0, 0)
    assert board_dims(_cells({(0, 0): "0", (3, 2): "1"})) == (3, 4)


# --------------------------------------------------------------------------- #
# Step2 propagate 确定性推理
# --------------------------------------------------------------------------- #
def test_solver_propagate_safe():
    # 数字 1 的唯一雷已旗 → 周边其余 covered 全部必安全（§6.1）
    board = _cells({
        (0, 0): "flag", (1, 0): "1", (2, 0): "covered",
        (0, 1): "covered", (1, 1): "covered", (2, 1): "covered",
    })
    new_board, events = propagate(board)
    got = {(e["type"], e["c"], e["r"]) for e in events}
    assert got == {("safe", 2, 0), ("safe", 0, 1), ("safe", 1, 1), ("safe", 2, 1)}
    # 必安全格在返回 board 中保持 covered（真值待上层翻开回填）
    by_pos = {(x["c"], x["r"]): x for x in new_board}
    assert by_pos[(2, 0)]["state"] == "covered"
    assert by_pos[(0, 0)]["state"] == "flag"


def test_solver_propagate_mine():
    # 数字 3、已旗 2、仅 1 个未决 covered → 该格必雷（§6.1）
    board = _cells({
        (0, 0): "covered", (1, 0): "3",
        (0, 1): "flag", (1, 1): "flag",
        (0, 2): "covered", (1, 2): "covered",
    })
    new_board, events = propagate(board)
    got = {(e["type"], e["c"], e["r"]) for e in events}
    assert got == {("mine", 0, 0)}
    by_pos = {(x["c"], x["r"]): x for x in new_board}
    assert by_pos[(0, 0)]["state"] == "flag"              # 推定雷同步置旗


def test_solver_propagate_chain():
    # 链式传播：A(0,0)"2" 推出两雷 → 新旗使 B(2,1)"1" 与 (2,0)"1"/(1,2)"1" 推出安全格
    board = _cells({
        (0, 0): "2", (1, 0): "covered", (2, 0): "1", (3, 0): "covered",
        (0, 1): "covered", (1, 1): "2", (2, 1): "1", (3, 1): "0",
        (0, 2): "covered", (1, 2): "1", (2, 2): "0", (3, 2): "0",
    })
    new_board, events = propagate(board)
    got = {(e["type"], e["c"], e["r"]) for e in events}
    assert got == {("mine", 1, 0), ("mine", 0, 1), ("safe", 3, 0), ("safe", 0, 2)}
    by_pos = {(x["c"], x["r"]): x for x in new_board}
    assert by_pos[(1, 0)]["state"] == "flag" and by_pos[(0, 1)]["state"] == "flag"
    assert by_pos[(3, 0)]["state"] == "covered" and by_pos[(0, 2)]["state"] == "covered"


def test_solver_propagate_inconsistent():
    # 数字 1 邻接 2 旗 → 约束矛盾（感知异常信号）
    board = _cells({
        (0, 0): "1", (1, 0): "flag", (0, 1): "flag", (1, 1): "covered",
    })
    with pytest.raises(ValueError, match="inconsistent"):
        propagate(board)


def test_solver_propagate_empty():
    assert propagate([]) == ([], [])


# --------------------------------------------------------------------------- #
# Step2 flood_zeros
# --------------------------------------------------------------------------- #
def test_solver_flood_zeros():
    board = open_view(FLOOD_DIGITS, 4, 4, opened=FLOOD_OPENED)
    region = flood_zeros(board, 0, 0)
    assert region == {
        (0, 0), (1, 0), (0, 1), (1, 1),                   # 0 连通块
        (0, 2), (1, 2), (0, 3), (1, 3),
        (2, 0), (2, 1), (2, 2), (2, 3),                   # 边界数字格
    }
    assert not (region & FLOOD_MINES)                     # 0 区不邻雷
    assert flood_zeros(board, 2, 0) == set()              # 起点非 0 → 空集


def test_solver_flood_zeros_isolated_component():
    # 1×5 长条盘（雷在 (2,0)）：两端 0 格互不连通，各只展开自己的邻域
    board = _cells({
        (0, 0): "0", (1, 0): "1", (2, 0): "covered", (3, 0): "1", (4, 0): "0",
    })
    assert flood_zeros(board, 0, 0) == {(0, 0), (1, 0)}
    assert flood_zeros(board, 4, 0) == {(4, 0), (3, 0)}   # 孤 0 各自成区


# --------------------------------------------------------------------------- #
# Step3 probability 精确边际概率
# --------------------------------------------------------------------------- #
def test_solver_probability_50_50():
    # 双约束夹击：两格恰一雷 → 各 1/2（经典二选一）
    board = _cells({(0, 0): "1", (1, 0): "covered",
                    (0, 1): "covered", (1, 1): "1"})
    probs = probability(board, 1)
    by = {(p["c"], p["r"]): p for p in probs}
    assert set(by) == {(1, 0), (0, 1)}
    for p in by.values():
        assert p["mine_prob"] == pytest.approx(0.5)
        assert p["safe_prob"] == pytest.approx(0.5)
        assert p["approx"] is False


def test_solver_probability_thirds():
    # 单约束 rem1 压 3 格、R=1 → 各 1/3，概率和守恒
    board = _cells({(0, 0): "1", (1, 0): "covered",
                    (0, 1): "covered", (1, 1): "covered"})
    probs = probability(board, 1)
    assert sum(p["mine_prob"] for p in probs) == pytest.approx(1.0)
    assert all(p["mine_prob"] == pytest.approx(1 / 3) for p in probs)


def test_solver_probability_fresh_uniform():
    # 无任何数字的新局 → 内部格均匀分摊 10/81
    board = open_view({}, 9, 9, opened=set())
    probs = probability(board, 10)
    assert len(probs) == 81
    assert all(p["mine_prob"] == pytest.approx(10 / 81) for p in probs)
    assert sum(p["mine_prob"] for p in probs) == pytest.approx(10.0)


def test_solver_probability_conservation_random():
    # 随机局开一片后：Σ mine_prob == mines_total - 旗数（守恒验收线）
    rng = random.Random(7)
    for _ in range(5):
        mines = set(rng.sample([(c, r) for r in range(9) for c in range(9)], 10))
        digits = plant(mines, 9, 9)
        zeros = sorted(p for p, d in digits.items() if d == 0)
        opened = flood_reveal(digits, 9, 9, zeros[rng.randrange(len(zeros))])
        flags = set(sorted(mines - opened)[:2])           # 旗真雷（solver 语义）
        board = open_view(digits, 9, 9, opened, flags)
        probs = probability(board, 10)
        assert all(p["approx"] is False for p in probs)
        assert all(0.0 <= p["mine_prob"] <= 1.0 for p in probs)
        assert sum(p["mine_prob"] for p in probs) == pytest.approx(10 - len(flags))


def test_solver_probability_global_certain():
    # 三数字链压三格：R=1 → 中格必雷（唯一解）
    top = {(0, 0): "1", (1, 0): "1", (2, 0): "1",
           (0, 1): "covered", (1, 1): "covered", (2, 1): "covered"}
    by1 = {(p["c"], p["r"]): p for p in probability(_cells(top), 1)}
    assert by1[(1, 1)]["mine_prob"] == pytest.approx(1.0)
    assert by1[(0, 1)]["mine_prob"] == pytest.approx(0.0)
    # 3×4 四角链 + 4 内部格：唯一解 (0,1)/(3,1) 必雷，全局守恒下其余格确定安全
    quad = {(0, 0): "1", (1, 0): "1", (2, 0): "1", (3, 0): "1",
            (0, 1): "covered", (1, 1): "covered", (2, 1): "covered", (3, 1): "covered",
            (0, 2): "covered", (1, 2): "covered", (2, 2): "covered", (3, 2): "covered"}
    by2 = {(p["c"], p["r"]): p for p in probability(_cells(quad), 2)}
    assert by2[(0, 1)]["mine_prob"] == pytest.approx(1.0)
    assert by2[(3, 1)]["mine_prob"] == pytest.approx(1.0)
    assert by2[(1, 1)]["mine_prob"] == pytest.approx(0.0)
    assert by2[(0, 2)]["mine_prob"] == pytest.approx(0.0)   # 内部格
    assert sum(p["mine_prob"] for p in by2.values()) == pytest.approx(2.0)


def test_solver_probability_flags_and_infeasible():
    base = {(0, 0): "1", (1, 0): "flag", (0, 1): "covered", (1, 1): "covered"}
    probs = probability(_cells(base), 1)                  # 旗占 1 雷 → 剩余 0
    assert all(p["mine_prob"] == pytest.approx(0.0) for p in probs)
    with pytest.raises(ValueError, match="infeasible"):
        probability(_cells(base), 2)                      # 约束要 0 雷、R=1 → 不可行


def test_solver_probability_inconsistent():
    board = _cells({(0, 0): "1", (1, 0): "flag", (0, 1): "flag", (1, 1): "covered"})
    with pytest.raises(ValueError, match="inconsistent"):
        probability(board, 2)          # 雷总数先过数检（2=旗2+covered1），再到约束矛盾


# --------------------------------------------------------------------------- #
# Step4 solve 三层编排
# --------------------------------------------------------------------------- #
def test_solver_constraint_safe_reveal():
    board = _cells({(0, 0): "flag", (1, 0): "1", (2, 0): "covered",
                    (0, 1): "covered", (1, 1): "covered", (2, 1): "covered"})
    out = solve(board, 1)
    assert out["action"]["type"] == "reveal"
    assert out["action"]["reason"] == "constraint_safe"
    assert out["action"]["certainty"] == 1.0
    assert out["flags"] == [] and out["tie_break"] is False
    assert (out["action"]["cell"]["c"], out["action"]["cell"]["r"]) == (2, 0)


def test_solver_constraint_safe_flag_only():
    # 只推出雷无安全格 → action=flag，flags 清单含全部派生雷
    board = _cells({(0, 0): "covered", (1, 0): "3", (0, 1): "flag", (1, 1): "flag",
                    (0, 2): "covered", (1, 2): "covered"})
    out = solve(board, 3)
    assert out["action"]["type"] == "flag"
    assert out["action"]["cell"] == {"c": 0, "r": 0}
    assert out["flags"] == [{"c": 0, "r": 0}]
    assert out["tie_break"] is False


def test_solver_constraint_safe_with_flags():
    # 链式盘：先落 2 旗，再 reveal 首个安全格（传播路径不用雷总数）
    board = _cells({
        (0, 0): "2", (1, 0): "covered", (2, 0): "1", (3, 0): "covered",
        (0, 1): "covered", (1, 1): "2", (2, 1): "1", (3, 1): "0",
        (0, 2): "covered", (1, 2): "1", (2, 2): "0", (3, 2): "0",
    })
    out = solve(board, 2)
    assert out["action"]["type"] == "reveal"
    assert out["flags"] == [{"c": 1, "r": 0}, {"c": 0, "r": 1}]
    # _cells 按 c 主序迭代：(1,2)"1" 先于 (2,0)"1" 推出 (0,2) 安全 → 首个安全格 (0,2)
    assert (out["action"]["cell"]["c"], out["action"]["cell"]["r"]) == (0, 2)


def test_solver_prob_global_certain_action():
    # 传播无解（四角链各约束 rem1 压 2-3 格）、概率路径出 certainty=1.0 → constraint_safe
    quad = {(0, 0): "1", (1, 0): "1", (2, 0): "1", (3, 0): "1",
            (0, 1): "covered", (1, 1): "covered", (2, 1): "covered", (3, 1): "covered",
            (0, 2): "covered", (1, 2): "covered", (2, 2): "covered", (3, 2): "covered"}
    out = solve(_cells(quad), 2)
    assert out["action"]["reason"] == "constraint_safe"
    assert out["action"]["certainty"] == 1.0
    assert out["action"]["cell"] == {"c": 0, "r": 2}   # 确定安全格取并列集首个（c 主序）
    assert out["tie_break"] is False


def test_solver_tie_break():
    # 经典二选一：reason=guess_tie、随机落点在并列集内、alternatives 降序
    board = _cells({(0, 0): "1", (1, 0): "covered",
                    (0, 1): "covered", (1, 1): "1"})
    out = solve(board, 1, rng=random.Random(1234))
    assert out["action"]["reason"] == "guess_tie" and out["tie_break"] is True
    assert out["action"]["certainty"] == pytest.approx(0.5)
    assert (out["action"]["cell"]["c"], out["action"]["cell"]["r"]) in {(1, 0), (0, 1)}
    alts = out["alternatives"]
    assert len(alts) == 2 and alts[0]["safe_prob"] >= alts[1]["safe_prob"]


def test_solver_fresh_opening_tie():
    # 全新局：无约束 → 均匀并列随机，certainty = 1 - 10/81
    out = solve(open_view({}, 9, 9, opened=set()), 10, rng=random.Random(99))
    assert out["tie_break"] is True and out["action"]["reason"] == "guess_tie"
    assert out["action"]["certainty"] == pytest.approx(1 - 10 / 81)
    assert len(out["alternatives"]) == 81


def test_solver_no_covered():
    board = _cells({(0, 0): "0", (1, 0): "0", (0, 1): "0", (1, 1): "0"})
    with pytest.raises(ValueError, match="no covered"):
        solve(board, 1)
