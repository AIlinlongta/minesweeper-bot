# -*- coding: utf-8 -*-
"""m3 端到端闭环入口（设计 §4.1 boot / §4.2 单决策周期 / §5.x 兜底 / §5.8 全局边界）。

单周期：capture → (窗口移动自愈) → perceive（unknown 重采样）→ LED 计数器
对账（§4.4b：不一致 → 整盘重感知 → calibrate_robust）→ scene 判定
（lost/won → 结算）→ solver.solve → 落旗清单 + 主动作 → 反馈回灌
（未生效 → 前台化后重判一次）→ FrameState 落盘（scripts/_m3_frames/）。

周期内异常隔离（§5.8）：第 1 次失败仅重试；连续 ≥2 次走 calibrate_robust；
连续 3 次结束本局（result=error）。run_m3 捕获 CalibrateError/FatalError →
致命退出（exit 1）并保留 traceback + 最后 FrameState。
CLI：python main.py --runs 20 --seed N
"""
import argparse
import json
import random
import subprocess
import sys
import time
import traceback
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from action import action  # noqa: E402
from decision import llm_solver, solver  # noqa: E402
from perception import calibrate, capture, grid_perception, state, windowing  # noqa: E402

FRAMES_DIR = ROOT / "scripts" / "_m3_frames"
POLL = 0.25          # 动作后渲染等待
MAX_CYCLES = 600     # 单局安全上限（初级 <100；高级 480 格需更多周期）
CYCLE_RETRY = 3      # §5.8：连续周期失败上限
INJECT_TRIES = 3     # 周期内注入重试：瞬时丢点击前台化后重点击


class FatalError(RuntimeError):
    """环境不可用/自愈链全失败 → 致命退出（exit 1）。"""


# --------------------------------------------------------------------------- #
# boot（§4.1 / §5.1 / §5.2）
# --------------------------------------------------------------------------- #
def _reg_get(name):
    import win32api
    import win32con
    try:
        hk = win32api.RegOpenKeyEx(win32con.HKEY_CURRENT_USER,
                                   r"Software\Microsoft\winmine", 0,
                                   win32con.KEY_QUERY_VALUE)
        v, _ = win32api.RegQueryValueEx(hk, name)
        win32api.RegCloseKey(hk)
        return int(v)
    except Exception:
        return None


def _reg_set(values):
    import win32api
    import win32con
    hk = win32api.RegCreateKey(win32con.HKEY_CURRENT_USER,
                               r"Software\Microsoft\winmine")
    for k, v in values.items():
        win32api.RegSetValueEx(hk, k, 0, win32con.REG_DWORD, v)
    win32api.RegCloseKey(hk)


def ensure_board(height: int = 9, width: int = 9, mines: int = 10):
    """锁定盘型（自定义 Difficulty=3 + Height/Width/Mines），返回备份供调用方 restore。"""
    backup = {k: v for k in ("Difficulty", "Height", "Width", "Mines")
              if (v := _reg_get(k)) is not None}
    _reg_set({"Difficulty": 3, "Height": height, "Width": width,
              "Mines": mines})
    return backup


def _kill(win):
    if not windowing.verify_game_pid(win["pid"]):
        raise FatalError(f"REFUSE taskkill pid={win['pid']}: 进程映像非 winmine")
    subprocess.run(["taskkill", "/F", "/PID", str(win["pid"])],
                   check=False, capture_output=True)
    time.sleep(1.0)


def _launch_window(cfg) -> dict:
    """冷启动并按 pid 绑窗 + 前台化。"""
    proc = subprocess.Popen([cfg["exe_path"]])
    time.sleep(2.5)
    win = windowing.find_game_window(cfg, pid=proc.pid)
    if win is None:
        raise FatalError("启动后仍找不到窗口")
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.3)
    return win


def _calib_16x(win) -> dict:
    """校准并要求 1x 渲染（cell_size==16，防 2x；不满足抛 CalibrateError）。"""
    calib = calibrate.detect_board(grab_checked(win))
    if not (calib["rows"] > 0 and calib["cell_size"] == 16):
        raise calibrate.CalibrateError(f"cell_size={calib['cell_size']} 非 1x 渲染")
    return calib


def boot(cfg) -> dict:
    """enable_dpi_aware → 找窗（§5.1 重试 5 次）→ 无窗自启动 → 1x 校验
    （异常冷重启一次，launch_guarded 经验）。"""
    windowing.enable_dpi_aware()
    win = None
    for _ in range(5):
        win = windowing.find_game_window(cfg)
        if win:
            break
        time.sleep(cfg.get("poll_interval_s", 2))
    if win is None:
        win = _launch_window(cfg)
    else:
        windowing.bring_to_foreground(win["hwnd"])
        time.sleep(0.3)
    try:
        calib = _calib_16x(win)
    except calibrate.CalibrateError:
        # 遮挡/出屏自愈：移回可见区+前台化后重校准一次（bring_to_foreground
        # 内含 ensure_on_screen），仍败才冷重启——减少粗暴重启
        windowing.bring_to_foreground(win["hwnd"])
        time.sleep(0.5)
        try:
            calib = _calib_16x(win)
        except calibrate.CalibrateError:
            _kill(win)
            win = _launch_window(cfg)
            calib = _calib_16x(win)  # 再失败 → CalibrateError 上抛致命
    return {"win": win, "calib": calib, "cfg": cfg}


def grab_checked(win) -> object:
    """§5.2 截屏自检：黑屏重试 3 次 + 前台化重抓 1 次，仍失败致命。"""
    for _ in range(3):
        frame = capture.grab_client(win)
        if capture.self_check(frame):
            return frame
        time.sleep(0.3)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.3)
    frame = capture.grab_client(win)
    if capture.self_check(frame):
        return frame
    raise FatalError("截屏失败（黑屏/窗口遮挡）")


# --------------------------------------------------------------------------- #
# 感知/对账/反馈
# --------------------------------------------------------------------------- #
def _perceive(win, calib):
    """抓帧+整盘分类，unknown 重采样 ×3（§5.4）。仍 unknown 原样返回。"""
    frame = grab_checked(win)
    board = grid_perception.classify_board(frame, calib)
    for _ in range(2):
        if not any(c["state"] == "unknown" for c in board):
            break
        time.sleep(0.2)
        frame = grab_checked(win)
        board = grid_perception.classify_board(frame, calib)
    return frame, board


def _reconciled(win, calib, mines_total, log):
    """感知 + 计数器对账（§4.4b 自愈链：整盘重感知 → 前台化重感知）。

    返回 (frame, board, counter, ok)；两轮均不一致 → ok=False（计 reconcile_fail）。
    """
    counter = None
    for stage in ("perceive", "foreground"):
        frame, board = _perceive(win, calib)
        counter = state.read_mines_counter(frame)
        if state.reconcile(board, counter, mines_total):
            return frame, board, counter, True
        log(f"[reconcile] {stage} mismatch: counter={counter} "
            f"flags={sum(1 for c in board if c['state'] == 'flag')} "
            f"covered={sum(1 for c in board if c['state'] == 'covered')}")
        windowing.bring_to_foreground(win["hwnd"])
        time.sleep(0.3)
    return frame, board, counter, False


def _feedback_changed(win, calib, kind, c, r) -> bool:
    """反馈回灌（§4.5/§5.6）：reveal→目标格非 covered；flag→目标格 flag。
    单格分类（省整盘重分类，高级盘 480 格显著提速），未生效 → 前台化后重判一次。"""
    def check():
        frame = grab_checked(win)
        cell = grid_perception.slice_cell(frame, calib, c, r)
        st = grid_perception.classify_cell(cell)["state"]
        return st != "covered" if kind == "reveal" else st == "flag"
    if check():
        return True
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.25)
    return check()


def _cell_covered(win, calib, c, r) -> bool:
    """单格分类：是否仍为 covered（LLM 多格连开前检查，跳过已展开格）。"""
    frame = grab_checked(win)
    cell = grid_perception.slice_cell(frame, calib, c, r)
    return grid_perception.classify_cell(cell)["state"] == "covered"


def _is_fresh(board: list[dict]) -> bool:
    return all(c["state"] == "covered" for c in board)


def _face_click(win) -> None:
    frame = grab_checked(win)
    face = calibrate.detect_anchor(frame).get("face_rect")
    if not face:
        raise RuntimeError("face not found")
    action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                    win["client_rect"]["y"] + face["y"] + face["h"] // 2))
    time.sleep(0.7)


def _solver_cells(board: list[dict]) -> list[dict]:
    """CellClassify → SolverCell（question 按 covered 参与求解）。"""
    out = []
    for c in board:
        st = c["state"]
        if st == "question":
            st = "covered"
        if st == "unknown":
            raise ValueError("unknown cell in solver input")
        out.append({"c": c["c"], "r": c["r"], "state": st,
                    "digit": int(st) if st.isdigit() else None})
    return out


def _window_moved(win: dict) -> bool:
    try:
        return windowing.get_window_rect(win["hwnd"]) != win["window_rect"]
    except Exception:
        return True


# --------------------------------------------------------------------------- #
# 单局（§4.2 主循环）
# --------------------------------------------------------------------------- #
def play_one_game(ctx, run_id: int, rng: random.Random, log=print) -> dict:
    win, calib = ctx["win"], ctx["calib"]
    cfg = ctx["cfg"]
    mines_total = calib["mines_total"]
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    frames_path = FRAMES_DIR / f"run_{run_id}.jsonl"
    res = {"run": run_id, "result": "playing", "cycles": 0,
           "guess_ties": 0, "prob_min": 0, "reconcile_fail": 0,
           "certainty_min": 1.0, "first_reason": None, "error": None}
    fail = 0
    frame_id = 0

    # 开局必须全新盘（boot 脏盘/上局残留 → 脸符重开 ×3）
    _, board = _perceive(win, calib)
    for _ in range(3):
        if _is_fresh(board):
            break
        _face_click(win)
        _, board = _perceive(win, calib)
    else:
        res["result"] = "dirty"
        return res

    while res["cycles"] < MAX_CYCLES:
        try:
            if _window_moved(win):  # §6.3 窗口移动/丢失自愈
                new = windowing.find_game_window(cfg, pid=win["pid"])
                if new is None:  # 丢窗（关闭/崩溃）→ 冷重启拉起，老局等价重开
                    log(f"[run {run_id}] window lost -> relaunch")
                    new = _launch_window(cfg)
                    calib = _calib_16x(new)
                    ctx["win"], ctx["calib"] = new, calib
                else:
                    ctx["win"] = win = new
                    windowing.bring_to_foreground(win["hwnd"])
                    time.sleep(0.2)
                    calib = calibrate.calibrate_robust(win)
                    ctx["calib"] = calib
                win = new
                mines_total = calib["mines_total"]

            frame, board, counter, rec_ok = _reconciled(
                win, calib, mines_total, log)
            if not rec_ok:
                res["reconcile_fail"] += 1
            scene = state.detect_scene(board)
            mines_remaining = counter if counter is not None else \
                mines_total - sum(1 for c in board if c["state"] == "flag")
            game_info = {**calib, "mines_remaining": mines_remaining,
                         "window_rect": win["window_rect"]}

            if scene in ("won", "lost"):
                with open(frames_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(state.build_frame(
                        frame_id, time.time(), game_info, board, scene,
                        None), ensure_ascii=False) + "\n")
                res["result"] = scene
                res["cycles"] += 1
                log(f"[run {run_id}] scene={scene} cycles={res['cycles']}")
                return res

            if cfg.get("brain") == "llm":
                # LLM 问答式决策（含新盘首击，实验保持决策 100% 来自 LLM；
                # 畸形/超时 → 重问 → 随机合法格兜底，闭环不断）
                sol = llm_solver.solve(_solver_cells(board), mines_total,
                                       rng=rng, cfg=cfg.get("llm") or {})
                act = sol["action"]
            elif _is_fresh(board):
                # §6.3 首步安全：新局首击固定角落（角格 0 连片概率 67%，
                # 高于边 51%/中心 33%；首击雷由游戏规则保证不成立）
                act = {"type": "reveal", "cell": {"c": 0, "r": 0},
                       "reason": "first_corner", "certainty": 1.0}
                sol = {"flags": [], "tie_break": False}
            else:
                sol = solver.solve(_solver_cells(board), mines_total, rng=rng)
                act = sol["action"]
            lst = sol.get("stats")
            if lst:  # LLM 决策统计（asked/invalid/fallback）逐周期累计
                acc = res.setdefault("llm_stats", {"asked": 0, "invalid": 0,
                                                   "fallback": 0})
                for k in acc:
                    acc[k] += int(lst.get(k, 0))
            if res["first_reason"] is None:
                res["first_reason"] = act["reason"]
            res["certainty_min"] = min(res["certainty_min"], act["certainty"])
            if sol["tie_break"]:
                res["guess_ties"] += 1
            elif act["reason"] == "prob_min":
                res["prob_min"] += 1

            for fc in sol["flags"]:  # 先落确定旗
                action.flag(fc["c"], fc["r"], win, calib)
            kind = act["type"]
            c0, r0 = act["cell"]["c"], act["cell"]["r"]
            # LLM 多格 reveal 连开（步骤3 一次多安全格）：首格之外逐格点击，
            # 点击前确认仍 covered（前格 0 连片可能已展开后格）；不逐格强校验
            # 反馈，漏开的下一周期重感知自然补上
            extra = (act.get("cells") or [])[1:] if kind == "reveal" else []
            # 注入 + 周期内重试：失败 → 前台化 → 重点击（瞬时丢点击自愈，
            # 不升级 §5.8；kind==flag 时 cell==flags[0]，上方已落）
            for attempt in range(INJECT_TRIES):
                if attempt:
                    windowing.bring_to_foreground(win["hwnd"])
                    time.sleep(0.3)
                if kind == "reveal":
                    action.reveal(c0, r0, win, calib)
                elif attempt:  # flag 重试重点击（首轮已随 flags 落过）
                    action.flag(c0, r0, win, calib)
                time.sleep(POLL)
                if _feedback_changed(win, calib, kind, c0, r0):
                    break
            else:
                raise RuntimeError(f"inject not effective at {act['cell']}")
            for x in extra:  # 多格连开（flag 类型无 extra）
                if _cell_covered(win, calib, x["c"], x["r"]):
                    action.reveal(x["c"], x["r"], win, calib)
                    time.sleep(POLL)

            frame_id += 1
            with open(frames_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(state.build_frame(
                    frame_id, time.time(), game_info, board, "playing", act),
                    ensure_ascii=False) + "\n")
            res["cycles"] += 1
            fail = 0
        except Exception as e:  # §5.8 周期异常隔离
            fail += 1
            log(f"[run {run_id}] cycle error x{fail}: {e}")
            if fail >= CYCLE_RETRY:
                res["result"], res["error"] = "error", str(e)
                return res
            time.sleep(0.4)
            # 丢窗自愈：窗口已消失（关闭/崩溃）→ 冷重启拉起继续；
            # 环境级自愈成功重置 fail，防误升级（老局等价重开，首击走 fresh 分支）
            try:
                alive = windowing.find_game_window(cfg, pid=win["pid"]) is not None
            except Exception:
                alive = False
            if not alive:
                log(f"[run {run_id}] window lost -> relaunch")
                try:
                    win = _launch_window(cfg)
                    calib = _calib_16x(win)
                except (calibrate.CalibrateError, FatalError) as we:
                    res["result"], res["error"] = "error", f"relaunch failed: {we}"
                    return res
                ctx["win"], ctx["calib"] = win, calib
                mines_total = calib["mines_total"]
                fail = 0
                continue
            if fail >= 2:  # 连续失败 → 鲁棒校准（可能触发 restart 自愈）
                try:
                    calib = calibrate.calibrate_robust(win)
                except calibrate.CalibrateError as ce:
                    res["result"], res["error"] = "error", f"calibrate_robust: {ce}"
                    return res
                ctx["calib"] = calib
                mines_total = calib["mines_total"]
                _, board = _perceive(win, calib)
                if _is_fresh(board) and res["cycles"] > 0:
                    # robust 链重启了游戏 → 本局已被重置，判 invalid 防统计污染
                    res["result"], res["error"] = "restarted", "robust restart mid-game"
                    return res
    res["result"], res["error"] = "stuck", "MAX_CYCLES"
    return res


# --------------------------------------------------------------------------- #
# run_m3（§3.11 RunReport）
# --------------------------------------------------------------------------- #
def run_m3(cfg: dict, runs: int = 20, seed: int | None = None,
           log=print, height: int = 9, width: int = 9,
           mines: int = 10) -> dict:
    """连续 runs 局端到端（默认初级 9x9/10，可指定高级 16x30/99）。返回 RunReport。"""
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    backup = ensure_board(height, width, mines)
    log(f"[boot] registry backup={backup}")
    rng = random.Random(seed)
    report = {"runs": 0, "won": 0, "won_rate": 0.0,
              "solved_all_solvable": True, "details": []}
    ctx = None
    try:
        ctx = boot(cfg)
        for i in range(1, runs + 1):
            r = play_one_game(ctx, i, rng, log)
            report["runs"] += 1
            report["won"] += r["result"] == "won"
            report["details"].append(r)
            log(f"[report] run {i}: {r['result']} cycles={r['cycles']} "
                f"guess_ties={r['guess_ties']} prob_min={r['prob_min']} "
                f"reconcile_fail={r['reconcile_fail']} "
                f"certainty_min={r['certainty_min']}")
            if r["result"] not in ("won", "lost"):
                report["solved_all_solvable"] = False
                break
            try:  # 结算 → 脸符重开下一局
                _face_click(ctx["win"])
            except Exception as e:
                log(f"[restart] {e}; calibrate_robust 自愈")
                ctx["calib"] = calibrate.calibrate_robust(ctx["win"])
        report["won_rate"] = report["won"] / max(1, report["runs"])
    finally:
        if ctx:
            try:
                _kill(ctx["win"])
            except Exception:
                pass
        if backup:
            _reg_set(backup)
        log(f"[boot] registry restored={backup}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--height", type=int, default=9)
    ap.add_argument("--width", type=int, default=9)
    ap.add_argument("--mines", type=int, default=10)
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
    try:
        report = run_m3(cfg, runs=args.runs, seed=args.seed,
                        height=args.height, width=args.width,
                        mines=args.mines)
    except FatalError as e:
        print(f"FATAL: {e}")
        return 1
    except calibrate.CalibrateError as e:
        print(f"FATAL: 雷区定位失败: {e}")
        return 1
    except Exception:
        traceback.print_exc()
        return 1
    print(json.dumps({k: v for k, v in report.items() if k != "details"},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
