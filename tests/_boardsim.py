# -*- coding: utf-8 -*-
"""合成棋盘生成器 + 轻量翻开盘模拟（m2 Step1，tests 专用，纯矩阵无图像依赖）。

  plant        雷位 → 全盘真值数字（0-8，雷格不在返回 dict 中）
  flood_reveal 模拟游戏点击展开（0 连片 + 边界数字；点雷抛 MineHit）
  open_view    真值 + 已翻/已旗集合 → SolverCell 契约 board（§2.8）

m2 单测与可解局批量验收（scripts/eval_m2.py，Step5）共用。
"""
from __future__ import annotations

from decision.solver import neighbors


class MineHit(Exception):
    """点击命中雷位（模拟触雷）。"""


def plant(mines: set[tuple[int, int]], rows: int, cols: int) -> dict[tuple[int, int], int]:
    """雷位集合 → {(c,r): 数字} 全盘真值（非雷格）。"""
    return {(c, r): sum(1 for p in neighbors(c, r, rows, cols) if p in mines)
            for r in range(rows) for c in range(cols) if (c, r) not in mines}


def flood_reveal(digits: dict[tuple[int, int], int], rows: int, cols: int,
                 click: tuple[int, int]) -> set[tuple[int, int]]:
    """模拟游戏左键翻开：数字 0 自动连片展开（含边界数字格）。

    返回翻开格集合（含起点）；点雷抛 MineHit。
    """
    if click not in digits:
        raise MineHit(f"mine at {click}")
    opened = {click}
    if digits[click] == 0:
        stack = [click]
        while stack:
            cur = stack.pop()
            for p in neighbors(cur[0], cur[1], rows, cols):
                if p not in opened and p in digits:
                    opened.add(p)
                    if digits[p] == 0:
                        stack.append(p)
    return opened


def open_view(digits: dict[tuple[int, int], int], rows: int, cols: int,
              opened: set[tuple[int, int]], flags: set[tuple[int, int]] = frozenset(),
              ) -> list[dict]:
    """真值 + 已翻/已旗集合 → SolverCell 契约 board（全盘 rows×cols）。"""
    flags = set(flags)
    board = []
    for r in range(rows):
        for c in range(cols):
            if (c, r) in flags:
                board.append({"c": c, "r": r, "state": "flag", "digit": None})
            elif (c, r) in opened:
                d = digits[(c, r)]
                board.append({"c": c, "r": r, "state": str(d), "digit": d})
            else:
                board.append({"c": c, "r": r, "state": "covered", "digit": None})
    return board
