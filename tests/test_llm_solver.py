# -*- coding: utf-8 -*-
"""llm_solver 单测：序列化 / 解析 / 重问与兜底 / 旗数上限（FakeClient 注入，无需真端点）。"""
import random

from decision import llm_solver

CFG = {"ask_retries": 2}


class FakeClient:
    """按脚本回放的桩：str → 正常回答；Exception → 抛出；默认"垃圾"。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0
        self.last_messages = None

    def observe(self, cells, mines_left):
        pass

    def complete(self, messages):
        self.calls += 1
        self.last_messages = messages
        r = self.replies.pop(0) if self.replies else "垃圾回答"
        if isinstance(r, Exception):
            raise r
        return r


def _board(spec):
    cells = []
    for r, row in enumerate(spec):
        for c, ch in enumerate(row):
            if ch == "#":
                st, d = "covered", None
            elif ch == "F":
                st, d = "flag", None
            elif ch == "?":
                st, d = "question", None
            elif ch == ".":
                st, d = "0", 0
            else:
                st, d = ch, int(ch)
            cells.append({"c": c, "r": r, "state": st, "digit": d})
    return cells


# ---------------------------------------------------------------- 序列化
def test_serialize_grid():
    cells = _board(["#1.", "#.."])
    txt = llm_solver.serialize_board(cells, mines_left=9)
    assert "扫雷棋盘 3x2" in txt
    assert "还剩 9 颗雷" in txt
    assert "r0 # 1 ." in txt
    assert "r1 # . ." in txt
    assert "#=未翻开" in txt


# ---------------------------------------------------------------- 解析
def test_parse_valid():
    cells = _board(["#1.", "#.."])
    a = llm_solver.parse_action('好的 {"type":"reveal","c":0,"r":0} 完毕', cells)
    assert a == {"type": "reveal", "cell": {"c": 0, "r": 0}}


def test_parse_invalid_cases():
    cells = _board(["#1F", "#.."])
    bad = [
        "",                              # 空
        "完全没有 JSON",                  # 无 JSON
        '{"type":"dig","c":0,"r":0}',     # 非法 type
        '{"type":"reveal","c":5,"r":5}',  # 越界
        '{"type":"reveal","c":"0","r":0}',  # 非整数坐标
        '{"type":"reveal","c":2,"r":0}',  # 目标是已翻开数字格
        '{"type":"reveal","c":true,"r":0}',  # bool 冒充 int
        '{"type":"reveal","c":0}',        # 缺字段
    ]
    for t in bad:
        assert llm_solver.parse_action(t, cells) is None, t


# ---------------------------------------------------------------- solve 契约
def test_solve_direct_valid():
    cells = _board(["#1.", "#.."])  # covered 格：(0,0) 与 (0,1)
    cl = FakeClient(['{"type":"reveal","c":0,"r":1}'])
    out = llm_solver.solve(cells, 10, rng=random.Random(1), cfg=CFG, client=cl)
    assert out["action"]["type"] == "reveal"
    assert out["action"]["cell"] == {"c": 0, "r": 1}
    assert out["action"]["reason"] == "llm"
    assert out["flags"] == [] and out["tie_break"] is False
    assert out["stats"] == {"asked": 1, "invalid": 0, "fallback": 0}


def test_solve_reask_then_valid():
    cells = _board(["#1.", "#.."])
    cl = FakeClient(["我觉得那里不错", '{"type":"flag","c":0,"r":0}'])
    out = llm_solver.solve(cells, 10, rng=random.Random(1), cfg=CFG, client=cl)
    assert cl.calls == 2
    assert out["action"]["type"] == "flag"
    assert out["flags"] == [{"c": 0, "r": 0}]  # flag 动作走 flags 清单先落
    assert out["stats"] == {"asked": 2, "invalid": 1, "fallback": 0}
    # 纠错重问带历史上下文与原因
    assert any("无效" in m["content"] for m in cl.last_messages[1:])


def test_solve_fallback_random():
    cells = _board(["#1.", "#.."])
    cl = FakeClient([])  # 永远垃圾
    out = llm_solver.solve(cells, 10, rng=random.Random(1), cfg=CFG, client=cl)
    assert cl.calls == 3  # 1 + ask_retries
    assert out["action"]["type"] == "reveal"
    assert out["action"]["reason"] == "llm_fallback_random"
    assert cells[(out["action"]["cell"]["r"]) * 3
                 + out["action"]["cell"]["c"]]["state"] == "covered"
    assert out["stats"]["fallback"] == 1
    assert out["stats"]["invalid"] == 3


def test_solve_exception_reask():
    cells = _board(["#1.", "#.."])
    cl = FakeClient([TimeoutError("t"), '{"type":"reveal","c":0,"r":0}'])
    out = llm_solver.solve(cells, 10, rng=random.Random(1), cfg=CFG, client=cl)
    assert cl.calls == 2
    assert out["action"]["reason"] == "llm"
    assert out["stats"] == {"asked": 2, "invalid": 1, "fallback": 0}


def test_solve_flag_cap():
    mines_total = 2
    cells = _board(["F1.", "#.."])  # covered 格：(0,0)、(0,1)；已有 1 旗
    cl = FakeClient(['{"type":"flag","c":0,"r":1}'])
    out = llm_solver.solve(cells, mines_total, rng=random.Random(1),
                           cfg=CFG, client=cl)
    assert out["action"]["type"] == "flag"
    assert out["flags"] == [{"c": 0, "r": 1}]
    # 旗数已满（2/2）→ flag 判无效 → 重问仍 flag → 兜底 reveal
    cells_full = _board(["FF.", "#.."])
    cl2 = FakeClient(['{"type":"flag","c":0,"r":1}'] * 3)
    out2 = llm_solver.solve(cells_full, mines_total, rng=random.Random(1),
                            cfg=CFG, client=cl2)
    assert out2["action"]["type"] == "reveal"
    assert out2["action"]["reason"] == "llm_fallback_random"
    assert out2["stats"]["fallback"] == 1


# ---------------------------------------------------------------- 历史
def test_solve_history_persist():
    class HistClient(FakeClient):
        """带历史属性的桩（同 OpenAIClient/StubClient 接口）。"""

        def __init__(self, replies):
            super().__init__(replies)
            self.history = []
            self.last_action = None

    cells = _board(["#1.", "#.."])
    cl = HistClient(['{"type":"reveal","c":0,"r":0}'])
    llm_solver.solve(cells, 10, rng=random.Random(1), cfg=CFG, client=cl)
    assert cl.last_action == {"type": "reveal", "c": 0, "r": 0}
    assert cl.history == []

    cells[0]["state"], cells[0]["digit"] = "1", 1  # 主循环反馈：落点翻开显示1
    cl.replies = ['{"type":"reveal","c":0,"r":1}']
    out2 = llm_solver.solve(cells, 10, rng=random.Random(1), cfg=CFG, client=cl)
    user_msg = cl.last_messages[1]["content"]  # [0]=system, [1]=user
    assert "最近动作与结果" in user_msg
    assert "reveal(0,0) → 已翻开（显示1）" in user_msg
    assert cl.last_action == {"type": "reveal", "c": 0, "r": 1}
    assert out2["stats"]["fallback"] == 0


# ---------------------------------------------------------------- 桩模式
def test_stub_mode_clean():
    cfg = {"ask_retries": 2, "brain": "stub", "stub_error_rate": 0.0,
           "stub_seed": 7}
    cells = _board(["#" * 5] * 5)
    mines_total = 5
    rng = random.Random(7)
    outs = []
    for _ in range(30):
        out = llm_solver.solve(cells, mines_total, rng=rng, cfg=cfg)
        outs.append(out)
        assert out["action"]["reason"] == "llm"
        c, r = out["action"]["cell"]["c"], out["action"]["cell"]["r"]
        assert cells[r * 5 + c]["state"] == "covered"
        if out["action"]["type"] == "reveal":
            cells[r * 5 + c]["state"] = "0"  # 模拟翻开（纯合成演化）
        # flag 不改盘面（保持 covered 供后续循环），只验证旗数上限守卫
        if sum(1 for x in cells if x["state"] == "flag") >= mines_total:
            mines_total += 1  # 桩可能持续插旗，抬上限避免全部走兜底
    assert all(o["stats"]["fallback"] == 0 for o in outs)
