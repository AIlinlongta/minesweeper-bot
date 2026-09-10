# -*- coding: utf-8 -*-
"""LLM 问答式解题器（阶段0 附加实验，§4.2 决策层可切换后端）。

与 decision/solver.solve 同契约（action/flags/tie_break），决策来源换成 LLM：
每周期把棋面序列化为人读文本 → LLM 输出一个动作 JSON → 校验合法后回灌主循环。
不强求解题正确度，只要求全闭环：
  畸形/非法输出 → 纠错重问 ask_retries 次 → 仍失败 → 随机合法格兜底（闭环不断）。

模式（config/llm.yaml brain 字段）：
  stub   离线桩（可注入畸形率联调重问/兜底路径，无需网络与密钥）
  openai OpenAI 兼容 /chat/completions（base_url + api_key_env + model）

消息结构：system 固定角色+JSON 约束；user = 最近 3 步动作+结果的历史 + 棋面
快照 + 步法要求。历史落在 client 实例上跨周期持久，新盘（全 covered）清空。
"""
import json
import os
import random
import re
import time

import requests

from decision.solver import board_dims

_TRACE_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts", "_llm_trace.log")


def _trace(msg: str) -> None:
    """实时输出 LLM 交互轨迹：stdout 即时刷新 + 追加写 _llm_trace.log。"""
    line = f"[llm {time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(_TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

ACTION_RE = re.compile(r"\{[^{}]*\}", re.S)

SYSTEM_PROMPT = """你是谨慎的扫雷玩家，根据局面走一步。严格只输出一个 JSON 对象，
格式：{"type":"reveal"或"flag","c":<列>,"r":<行>}（c/r 为整数坐标；type=reveal
翻开该格，type=flag 在你确信是雷时插旗）。除该 JSON 外不要输出任何其他文字。"""

PROMPT_TMPL = """{history}当前局面：

{board}

要求：
- 只能对 #（未翻开）格操作：reveal 翻开它，或 flag 插旗（你确信是雷时）。
- 一次只走一步，优先走你判断最安全的步。"""


def _outcome(state: str) -> str:
    """上一动作在当前盘面的落点结果（人读一句话）。"""
    if state == "covered":
        return "未生效（格仍为 #）"
    if state == "flag":
        return "旗已生效"
    if state == "question":
        return "旗被再次右键成问号"
    if state == "mine_red":
        return "踩雷出局"
    if state == "0":
        return "已翻开（空白）"
    if state.isdigit():
        return f"已翻开（显示{state}）"
    return f"状态={state}"


def serialize_board(cells: list[dict], mines_left: int) -> str:
    """棋面 → 人读文本（列标题 + 行号 + 字符网格 + 图例）。"""
    rows, cols = board_dims(cells)
    sym = {}
    for x in cells:
        st = x["state"]
        if st == "covered":
            sym[(x["c"], x["r"])] = "#"
        elif st == "flag":
            sym[(x["c"], x["r"])] = "F"
        elif st == "question":
            sym[(x["c"], x["r"])] = "?"
        elif st.isdigit() and st != "0":
            sym[(x["c"], x["r"])] = st
        else:
            sym[(x["c"], x["r"])] = "."
    lines = [f"扫雷棋盘 {cols}x{rows}（c=列0-{cols - 1}, r=行0-{rows - 1}），"
             f"还剩 {mines_left} 颗雷未标记。"]
    lines.append("    " + " ".join(str(c) for c in range(cols)))
    for r in range(rows):
        lines.append(f"r{r} " + " ".join(sym[(c, r)] for c in range(cols)))
    lines.append("图例：#=未翻开 F=旗 ?=问号 .=周围无雷 数字=周围雷数")
    return "\n".join(lines)


def parse_action(text: str, cells: list[dict]) -> dict | None:
    """从 LLM 回答提取第一个合法动作；无法解析/越界/非法状态 → None。

    合法性：type ∈ {reveal, flag}；坐标为 int 且在盘内；目标格必须 covered。"""
    index = {(x["c"], x["r"]): x for x in cells}
    for blob in ACTION_RE.findall(text or ""):
        try:
            d = json.loads(blob)
        except json.JSONDecodeError:
            continue
        t, c, r = d.get("type"), d.get("c"), d.get("r")
        if t not in ("reveal", "flag"):
            continue
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (c, r)):
            continue
        if (c, r) not in index:
            continue
        if index[(c, r)]["state"] != "covered":  # 只允许对未翻开格操作
            continue
        return {"type": t, "cell": {"c": c, "r": r}}
    return None


class OpenAIClient:
    """OpenAI 兼容 /chat/completions 客户端（requests 直连）。"""

    def __init__(self, cfg: dict):
        self.url = cfg["base_url"].rstrip("/") + "/chat/completions"
        env = cfg.get("api_key_env", "LLM_API_KEY")
        self.key = os.environ.get(env, "")
        if not self.key:
            raise RuntimeError(f"environment variable {env} not set")
        self.model = cfg["model"]
        self.temperature = cfg.get("temperature", 0.2)
        self.timeout = cfg.get("timeout_s", 20)
        self.history: list[str] = []      # 最近 3 步动作+结果（solve 维护）
        self.last_action: dict | None = None

    def observe(self, cells, mines_left) -> None:  # 真端点无需观察钩子
        pass

    def complete(self, messages: list[dict]) -> str:
        prompt_chars = sum(len(m["content"]) for m in messages)
        t0 = time.monotonic()
        _trace(f"ask model={self.model} prompt_chars={prompt_chars}")
        try:
            resp = requests.post(
                self.url,
                headers={"Authorization": f"Bearer {self.key}"},
                json={"model": self.model, "temperature": self.temperature,
                      "messages": messages},
                timeout=self.timeout)
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"] or ""
        except Exception as e:
            _trace(f"error {type(e).__name__}: {e} "
                   f"({time.monotonic() - t0:.1f}s)")
            raise
        _trace(f"reply ({time.monotonic() - t0:.1f}s): {raw[:300]!r}")
        return raw


class StubClient:
    """离线桩：按规则生成回答，注入 stub_error_rate 比例的畸形输出联调兜底。"""

    def __init__(self, cfg: dict, rng: random.Random):
        self.error_rate = cfg.get("stub_error_rate", 0.05)
        self.rng = rng
        self._cells: list[dict] = []
        self._flags: int = 0
        self.history: list[str] = []      # solve 维护（桩不消费，仅为接口一致）
        self.last_action: dict | None = None

    def observe(self, cells, mines_left) -> None:
        self._cells = cells
        self._flags = mines_left  # solve 传入的是 mines_left

    def complete(self, messages: list[dict]) -> str:
        covered = [(x["c"], x["r"]) for x in self._cells
                   if x["state"] == "covered"]
        if not covered:
            return "no covered cells left"
        roll = self.rng.random()
        if roll < self.error_rate * 0.5:
            return "我觉得右下角那里应该不错"            # 无 JSON
        if roll < self.error_rate * 0.75:
            return '{"type":"reveal","c":99,"r":99}'    # 越界
        if roll < self.error_rate * 0.9:
            return '{"type":"dig","c":0,"r":0}'         # 非法 type
        c, r = self.rng.choice(covered)
        # 少量插旗动作（不超剩余雷数，避免 LED 负数不可读）
        if self.rng.random() < 0.15 and self._flags > 0:
            return json.dumps({"type": "flag", "c": c, "r": r})
        return json.dumps({"type": "reveal", "c": c, "r": r})


_CLIENT_CACHE: dict = {}


def _get_client(cfg: dict, rng: random.Random):
    if cfg.get("brain") == "openai":
        key = ("openai", cfg.get("base_url"), cfg.get("model"))
        if key not in _CLIENT_CACHE:
            _CLIENT_CACHE[key] = OpenAIClient(cfg)
        return _CLIENT_CACHE[key]
    key = ("stub", cfg.get("stub_seed"), cfg.get("stub_error_rate"))
    if key not in _CLIENT_CACHE:
        _CLIENT_CACHE[key] = StubClient(cfg, rng)
    return _CLIENT_CACHE[key]


def solve(cells: list[dict], mines_total: int, rng: random.Random | None = None,
          cfg: dict | None = None, client=None) -> dict:
    """LLM 决策后端，与 solver.solve 同契约。

    返回 {"action", "flags", "tie_break": False,
          "stats": {"asked", "invalid", "fallback"}}。
    flags 仅在动作类型为 flag 时含该格（主循环先落旗再反馈校验）。

    消息结构：system 固定角色/JSON 约束 + user（历史 + 棋面 + 步法要求）。
    历史：最近 3 步动作与结果（落在 client.history，跨周期持久；client 无该
    属性时退化为无历史——单测 FakeClient 路径）。新盘（全 covered）自动清空。"""
    rng = rng or random.Random()
    cfg = cfg or {}
    flags_now = sum(1 for x in cells if x["state"] == "flag")
    mines_left = mines_total - flags_now
    board_txt = serialize_board(cells, mines_left)
    if client is None:
        client = _get_client(cfg, rng)
    client.observe(cells, mines_left)

    # --- 历史维护：结算上一动作结果 + 新盘清空 ---
    hist = getattr(client, "history", None)
    if hist is None:
        hist = []
    else:
        la = getattr(client, "last_action", None)
        if all(x["state"] == "covered" for x in cells):
            hist.clear()
            client.last_action = None
        elif la is not None:
            cells_now = {(x["c"], x["r"]): x["state"] for x in cells}
            hist.append(f"{la['type']}({la['c']},{la['r']}) → "
                        f"{_outcome(cells_now.get((la['c'], la['r']), '?'))}")
            del hist[:-3]
            client.last_action = None
    hist_txt = ""
    if hist:
        hist_txt = "最近动作与结果：\n" + "\n".join(f"- {h}" for h in hist) + "\n"

    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",
                 "content": PROMPT_TMPL.format(board=board_txt,
                                               history=hist_txt)}]
    stats = {"asked": 0, "invalid": 0, "fallback": 0}
    parsed, last_err = None, ""
    for i in range(1 + int(cfg.get("ask_retries", 2))):
        if i:  # 纠错重问
            messages.append({"role": "user",
                             "content": f"你的上次输出无效（{last_err}）。请重新"
                                        f"严格只输出一个 JSON 动作："
                                        f'{{"type":"reveal"或"flag","c":<列>,"r":<行>}}'})
        stats["asked"] += 1
        try:
            raw = client.complete(messages)
        except Exception as e:  # 超时/网络：记一次无效，退回初始对话重问
            stats["invalid"] += 1
            last_err = f"请求异常 {type(e).__name__}"
            messages = messages[:2]
            continue
        parsed = parse_action(raw, cells)
        if parsed is None:
            stats["invalid"] += 1
            last_err = "无法解析出合法动作"
            messages.append({"role": "assistant", "content": raw[:200]})
            continue
        if parsed["type"] == "flag" and flags_now >= mines_total:
            stats["invalid"] += 1
            last_err = "旗数已达雷数上限，不能继续插旗"
            messages.append({"role": "assistant", "content": raw[:200]})
            parsed = None
            continue
        break
    if parsed is None:  # 兜底：随机合法格（保证闭环不断）
        covered = [x for x in cells if x["state"] == "covered"]
        cell = rng.choice(covered)
        parsed = {"type": "reveal", "cell": {"c": cell["c"], "r": cell["r"]}}
        stats["fallback"] += 1
        reason = "llm_fallback_random"
    else:
        reason = "llm"
    act = {"type": parsed["type"], "cell": parsed["cell"],
           "reason": reason, "certainty": 1.0}
    flags = [parsed["cell"]] if parsed["type"] == "flag" else []
    if hist is not None and hasattr(client, "last_action"):
        # 记录本动作，供下一周期结算结果（含兜底动作，历史须如实反映）
        client.last_action = {"type": act["type"],
                              "c": act["cell"]["c"], "r": act["cell"]["r"]}
    return {"action": act, "flags": flags, "tie_break": False,
            "stats": stats}
