# -*- coding: utf-8 -*-
"""扫雷求解器（纯矩阵，可脱离图像单测）— 设计 §三.3.9。

输入契约 SolverCell（§2.8）: {"c": int, "r": int, "state": str, "digit": int|None}
  state ∈ covered / flag / "0".."8"（mine 不进入求解，触雷由上层判定 lost）
  board 为全盘列表（rows×cols 全覆盖），维度由 max(c/r)+1 推断。

m2 分步交付（已全部实现）：
  Step2 propagate（确定性约束传播）+ flood_zeros（0 连片展开）
  Step3 probability（精确枚举边际概率，超上限回退近似）
  Step4 solve（三层编排：确定解 → 概率最小 → 并列随机）
"""
from __future__ import annotations

import math
import random

# 单连通分量前沿格枚举上限（超出回退近似并打 approx 标记；9×9/16×16 实战远达不到）
FRONT_CAP = 26


def board_dims(board: list[dict]) -> tuple[int, int]:
    """由全盘 cell 列表推断 (rows, cols)。空盘 → (0, 0)。"""
    if not board:
        return 0, 0
    return (max(cell["r"] for cell in board) + 1,
            max(cell["c"] for cell in board) + 1)


def neighbors(c: int, r: int, rows: int, cols: int) -> list[tuple[int, int]]:
    """8 邻坐标（盘内）。"""
    out = []
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            if dr == 0 and dc == 0:
                continue
            nc, nr = c + dc, r + dr
            if 0 <= nc < cols and 0 <= nr < rows:
                out.append((nc, nr))
    return out


def propagate(board: list[dict]) -> tuple[list[dict], list[dict]]:
    """确定性约束传播，迭代到不动点（§三.3.9）。

    对每个数字格：F=周边旗数，U=周边未决 covered（不含已推定安全格），rem=N-F：
      rem == 0      → U 全部必安全（事件 type=safe）
      rem == len(U) → U 全部必雷（事件 type=mine，返回 board 中同步置 flag）
    必安全格进入内部 resolved 集参与链式推导；返回 board 中仍为 covered
    （真值待上层翻开后由感知回填，§2.8 无 safe 态）。
    约束矛盾（rem<0 或 rem>len(U)）→ ValueError（感知异常信号，上层触发重感知）。

    返回 (新 board, events)；events 元素:
      {"type": "safe"|"mine", "c": int, "r": int, "by": [来源格 c, r]}
    """
    rows, cols = board_dims(board)
    work = [{**cell} for cell in board]
    index = {(cell["c"], cell["r"]): cell for cell in work}
    resolved_safe: set[tuple[int, int]] = set()
    events: list[dict] = []

    changed = True
    while changed:
        changed = False
        for cell in work:
            st = cell["state"]
            if not st.isdigit():
                continue
            dc, dr, n = cell["c"], cell["r"], int(st)
            nbrs = neighbors(dc, dr, rows, cols)
            flagged = sum(1 for p in nbrs if index[p]["state"] == "flag")
            unknown = [p for p in nbrs
                       if index[p]["state"] == "covered" and p not in resolved_safe]
            rem = n - flagged
            if rem < 0 or rem > len(unknown):
                raise ValueError(
                    f"inconsistent constraint at ({dc},{dr}): digit={n} "
                    f"flagged={flagged} unknown={len(unknown)}")
            if not unknown:
                continue
            if rem == 0:
                for p in unknown:
                    resolved_safe.add(p)
                    events.append({"type": "safe", "c": p[0], "r": p[1], "by": [dc, dr]})
                changed = True
            elif rem == len(unknown):
                for p in unknown:
                    index[p]["state"] = "flag"
                    events.append({"type": "mine", "c": p[0], "r": p[1], "by": [dc, dr]})
                changed = True
    return work, events


def flood_zeros(board: list[dict], c: int, r: int) -> set[tuple[int, int]]:
    """0 连片展开集合（§三.3.9）：起点须为已翻开数字 0。

    返回起点出发 8 连通的 0 格集合及其全部 8 邻（感知层同步翻开的候选，
    含边界数字格；0 格按定义不邻雷，集合内不含雷位）。
    起点非 0 态 → 返回空集。
    """
    rows, cols = board_dims(board)
    index = {(cell["c"], cell["r"]): cell for cell in board}
    start = (c, r)
    if index.get(start, {}).get("state") != "0":
        return set()
    seen = {start}
    stack = [start]
    while stack:
        cc, rr = stack.pop()
        for p in neighbors(cc, rr, rows, cols):
            if p not in seen and index[p]["state"] == "0":
                seen.add(p)
                stack.append(p)
    region = set(seen)
    for p in seen:
        region.update(neighbors(p[0], p[1], rows, cols))
    return region


# --------------------------------------------------------------------------- #
# Step3 概率层：精确枚举 + 全局雷数守恒
# --------------------------------------------------------------------------- #
def _comb(n: int, k: int) -> int:
    """C(n,k)，k<0 或 k>n 记 0。"""
    if k < 0 or k > n:
        return 0
    return math.comb(n, k)


def _conv(a: dict[int, int], b: dict[int, int]) -> dict[int, int]:
    """雷数直方图卷积（分量间独立、全局雷数守恒用）。"""
    out: dict[int, int] = {}
    for ka, na in a.items():
        for kb, nb in b.items():
            out[ka + kb] = out.get(ka + kb, 0) + na * nb
    return out


def _constraints(board: list[dict], index: dict) -> list[tuple[list[tuple], int]]:
    """活跃约束 [(约束内 covered 格列表, rem)]；矛盾/越界 → ValueError。"""
    rows, cols = board_dims(board)
    cons = []
    for cell in board:
        st = cell["state"]
        if not st.isdigit():
            continue
        nbrs = neighbors(cell["c"], cell["r"], rows, cols)
        flagged = sum(1 for p in nbrs if index[p]["state"] == "flag")
        unknown = [p for p in nbrs if index[p]["state"] == "covered"]
        rem = int(st) - flagged
        if rem < 0 or rem > len(unknown):
            raise ValueError(
                f"inconsistent constraint at ({cell['c']},{cell['r']}): "
                f"digit={st} flagged={flagged} unknown={len(unknown)}")
        if unknown:
            cons.append((unknown, rem))
    return cons


def _enum_component(cells: list[tuple], cons: list[tuple[list[tuple], int]],
                    ) -> tuple[dict[int, int], list[dict[int, int]]]:
    """分量内回溯枚举全部合法雷位组合。

    返回 (h, percell)：h[k]=雷数为 k 的组合数；percell[i][k]=第 i 格为雷
    且总雷数 k 的组合数。用于全局雷数守恒下的精确边际概率。
    """
    pos = {p: i for i, p in enumerate(cells)}
    cell_cons: list[list[int]] = [[] for _ in cells]
    for ci, (cl, _rem) in enumerate(cons):
        for p in cl:
            cell_cons[pos[p]].append(ci)
    csize = [len(cl) for cl, _ in cons]
    n = len(cells)
    h: dict[int, int] = {}
    percell: list[dict[int, int]] = [{} for _ in cells]
    assigned = [0] * n
    cmines = [0] * len(cons)
    cseen = [0] * len(cons)

    def bt(i: int, k: int) -> None:
        if i == n:
            h[k] = h.get(k, 0) + 1
            for j in range(n):
                if assigned[j]:
                    percell[j][k] = percell[j].get(k, 0) + 1
            return
        for v in (0, 1):
            ok = True
            for ci in cell_cons[i]:
                m = cmines[ci] + v
                seen = cseen[ci] + 1
                rem = cons[ci][1]
                if m > rem or m + (csize[ci] - seen) < rem:
                    ok = False
                    break
            if not ok:
                continue
            assigned[i] = v
            for ci in cell_cons[i]:
                cmines[ci] += v
                cseen[ci] += 1
            bt(i + 1, k + v)
            for ci in cell_cons[i]:
                cmines[ci] -= v
                cseen[ci] -= 1
        assigned[i] = 0

    bt(0, 0)
    return h, percell


def probability(board: list[dict], mines_total: int) -> list[dict]:
    """全部 covered 格的边际雷概率（§三.3.9，验收线：概率和 == 剩余雷数）。

    精确解：数字约束按连通分量精确枚举 → 分量雷数直方图卷积 → 与全局
    剩余雷数（mines_total - 旗数）和无约束内部格（均匀）守恒合并。
    分量前沿格数超 FRONT_CAP → 回退单约束近似（approx=True，不保证守恒）。
    约束矛盾 / 组合不可行（含旗数>雷总数）→ ValueError。
    返回 [{"c","r","safe_prob","mine_prob","approx"}]（盘内行主序）。
    """
    rows, cols = board_dims(board)
    index = {(cell["c"], cell["r"]): cell for cell in board}
    covered = [(x["c"], x["r"]) for x in board if x["state"] == "covered"]
    n_flags = sum(1 for x in board if x["state"] == "flag")
    remaining = mines_total - n_flags
    if remaining < 0 or remaining > len(covered):
        raise ValueError(
            f"infeasible mine count: mines_total={mines_total} flags={n_flags} "
            f"covered={len(covered)}")
    cons = _constraints(board, index)

    # 前沿格 = 被活跃约束引用的 covered；其余为内部格
    front_set = {p for cl, _ in cons for p in cl}
    interior = [p for p in covered if p not in front_set]

    # 连通分量（约束↔格二部图）
    cell_cons: dict[tuple, list[int]] = {p: [] for p in front_set}
    for ci, (cl, _rem) in enumerate(cons):
        for p in cl:
            cell_cons[p].append(ci)
    comps: list[list[tuple]] = []
    comp_cons: list[list[int]] = []
    unseen = set(front_set)
    while unseen:
        seed = unseen.pop()
        cells, cis, stack = [seed], [], [seed]
        while stack:
            cur = stack.pop()
            for ci in cell_cons[cur]:
                if ci not in cis:
                    cis.append(ci)
                    for p in cons[ci][0]:
                        if p not in cells:
                            cells.append(p)
                            unseen.discard(p)
                            stack.append(p)
        cells.sort()
        comps.append(cells)
        comp_cons.append(sorted(cis))

    out = {p: {"c": p[0], "r": p[1], "mine_prob": 0.0, "safe_prob": 1.0,
               "approx": False} for p in covered}
    approx = any(len(cells) > FRONT_CAP for cells in comps)
    if approx:
        # 单约束近似：p = 邻接约束中最大 rem/|U|；内部格均摊剩余
        psum = 0.0
        for p in covered:
            if p in front_set:
                v = max(cons[ci][1] / len(cons[ci][0]) for ci in cell_cons[p])
                out[p]["mine_prob"] = min(max(v, 0.0), 1.0)
                psum += out[p]["mine_prob"]
        if interior:
            v = (remaining - psum) / len(interior)
            v = min(max(v, 0.0), 1.0)
            for p in interior:
                out[p]["mine_prob"] = v
        for rec in out.values():
            rec["safe_prob"] = 1.0 - rec["mine_prob"]
            rec["approx"] = True
        return [out[p] for p in covered]

    # 精确解：分量直方图 → 前后缀卷积 → 全局守恒
    n_int = len(interior)
    hs, gcells = [], []  # gcells[i] = 分量 i 每格 percell 直方图
    for cells, cis in zip(comps, comp_cons):
        sub = [cons[ci] for ci in cis]
        h, per = _enum_component(cells, sub)
        if not h:  # 分量内无合法组合 → 约束矛盾
            raise ValueError("infeasible component constraints")
        hs.append(h)
        gcells.append((cells, per))
    h_all: dict[int, int] = {0: 1}
    for h in hs:
        h_all = _conv(h_all, h)
    total = sum(cnt * _comb(n_int, remaining - k) for k, cnt in h_all.items())
    if total == 0:
        raise ValueError("infeasible: no assignment matches remaining mines")
    # 前缀/后缀卷积，取"其他分量"直方图
    pre: list[dict[int, int]] = [{0: 1}] * (len(hs) + 1)
    for i, h in enumerate(hs):
        pre[i + 1] = _conv(pre[i], h)
    suf: list[dict[int, int]] = [{0: 1}] * (len(hs) + 1)
    for i in range(len(hs) - 1, -1, -1):
        suf[i] = _conv(hs[i], suf[i + 1])
    for i, (cells, per) in enumerate(gcells):
        others = _conv(pre[i], suf[i + 1])
        for j, p in enumerate(cells):
            num = 0
            for k, cnt in per[j].items():
                for ko, no in others.items():
                    num += cnt * no * _comb(n_int, remaining - k - ko)
            out[p]["mine_prob"] = num / total
    if n_int:
        num_int = sum(cnt * _comb(n_int - 1, remaining - k - 1)
                      for k, cnt in h_all.items())
        p_int = num_int / total
        for p in interior:
            out[p]["mine_prob"] = p_int
    for rec in out.values():
        rec["safe_prob"] = 1.0 - rec["mine_prob"]
    return [out[p] for p in covered]


# --------------------------------------------------------------------------- #
# Step4 solve 主入口：确定解 → 概率最小 → 并列随机（§2.9 SolverOutput）
# --------------------------------------------------------------------------- #
def solve(board: list[dict], mines_total: int,
          rng: random.Random | None = None) -> dict:
    """三层编排（§三.3.9）：propagate 确定解优先；无确定解 → probability
    取最小风险；风险并列 → 随机 tie_break（reason=guess_tie）。

    返回 SolverOutput：
      action  {"type": "reveal"|"flag", "cell": {c,r}, "reason":
               constraint_safe|prob_min|guess_tie, "certainty": float}
      flags   本周期需先落下的标旗清单 [{c,r}]（constraint_safe 派生雷）
      alternatives  概率兜底时全部 covered 格按 safe_prob 降序
      tie_break     并列随机发生时 True
    无 covered 格 / 感知矛盾 → ValueError（上层触发 scene 检查/重感知）。
    action.type=="flag" 时 cell == flags[0]（m3 落旗清单后按 action 收尾）。
    """
    rng = rng if rng is not None else random.Random()
    work, events = propagate(board)
    safes = [e for e in events if e["type"] == "safe"]
    flags = [{"c": e["c"], "r": e["r"]} for e in events if e["type"] == "mine"]
    if not any(x["state"] == "covered" for x in work):
        raise ValueError("solve: no covered cells")
    if safes:
        e0 = safes[0]
        return {"action": {"type": "reveal", "cell": {"c": e0["c"], "r": e0["r"]},
                           "reason": "constraint_safe", "certainty": 1.0},
                "flags": flags, "alternatives": [], "tie_break": False}
    if flags:
        return {"action": {"type": "flag", "cell": flags[0],
                           "reason": "constraint_safe", "certainty": 1.0},
                "flags": flags, "alternatives": [], "tie_break": False}
    # 概率兜底
    probs = probability(work, mines_total)
    best = max(p["safe_prob"] for p in probs)
    tied = [p for p in probs if abs(p["safe_prob"] - best) < 1e-12]
    if best >= 1.0 - 1e-12:
        # 全局确定安全（传播遗漏、守恒推出）：取首个，不随机
        pick, reason, tie = tied[0], "constraint_safe", False
    elif len(tied) > 1:
        # 启发式并列：优先 8 邻数字格最多的候选（后续可利用约束更多，
        # 经验上优于纯随机）；启发值仍并列 → rng 随机
        rows, cols = board_dims(work)
        index = {(x["c"], x["r"]): x for x in work}

        def _nbr_nums(p):
            return sum(1 for nc, nr in neighbors(p["c"], p["r"], rows, cols)
                       if index[(nc, nr)]["state"].isdigit())
        max_info = max(_nbr_nums(p) for p in tied)
        pick, reason, tie = (rng.choice([p for p in tied
                                         if _nbr_nums(p) == max_info]),
                             "guess_tie", True)
    else:
        pick, reason, tie = tied[0], "prob_min", False
    alts = sorted(({"c": p["c"], "r": p["r"], "safe_prob": p["safe_prob"]}
                   for p in probs),
                  key=lambda a: (-a["safe_prob"], a["c"], a["r"]))
    return {"action": {"type": "reveal", "cell": {"c": pick["c"], "r": pick["r"]},
                       "reason": reason, "certainty": best},
            "flags": [], "alternatives": alts, "tie_break": tie}
