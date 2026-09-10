# -*- coding: utf-8 -*-
"""m1 数字样本加速采集（xyzzy 安全翻格，调试加速器，不参与验收/决策）。

与 eval_grid 的差异：翻格前先用 xyzzy 后门判定「是否雷」，安全才翻 → 不触雷，
可安全翻更多格（含边缘/雷簇旁），从而暴露 eval_grid 随机采样采不到的 4-8 数字。

流程（中级 16×16，难度由用户预设在界面）：
  1. 每局：重置 → 中心首击（必安全，兼作 xyzzy 极性校准锚点）→ 循环：
       选格 → xyzzy 判定 → 雷/不可判则跳过，安全则翻 → 全盘采样（oracle vs OCR）；
  2. 统计同 eval_grid：数字/空白样本数、OCR 正确率、数字分布直方图。

用法: python scripts/eval_grid_xyzzy.py [局数=10] [安全翻格上限=20]
"""
import subprocess
import sys
import time
from collections import Counter

import numpy as np
import win32con
import win32gui
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, grid_perception, windowing, xyzzy  # noqa: E402
from action import action  # noqa: E402

# 经典 winmine 数字色（RGB）→ 数字（oracle）
ORACLE = {
    (0, 0, 255): 1, (0, 128, 0): 2, (255, 0, 0): 3, (0, 0, 128): 4,
    (128, 0, 0): 5, (0, 128, 128): 6, (0, 0, 0): 7, (128, 128, 128): 8,
}
DIGITS = {str(i) for i in range(1, 9)}  # "1".."8"


def load_cfg() -> dict:
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def oracle_digit(cell: np.ndarray) -> int | None:
    """中心 8×8 区域内出现最多的「数字色」对应的数字（None=空白）。"""
    region = cell[4:12, 4:12].reshape(-1, 3)
    cnt: Counter = Counter()
    for p in region:
        b, g, r = [int(v) for v in p]
        cnt[(r, g, b)] += 1
    for rgb, _ in cnt.most_common():
        if rgb in ORACLE:
            return ORACLE[rgb]
    return None


def restart(win, frame_cache):
    anchor = calibrate.detect_anchor(frame_cache)
    face = anchor.get("face_rect")
    if not face:
        return False
    fx = win["client_rect"]["x"] + face["x"] + face["w"] // 2
    fy = win["client_rect"]["y"] + face["y"] + face["h"] // 2
    action.restart((fx, fy))
    time.sleep(0.6)
    return True


def ensure_visible(win):
    """强制窗口可见且在屏幕内（规避最小化/-32000 导致抓帧失败/卡）。"""
    hwnd = win["hwnd"]
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    time.sleep(0.3)
    l, t, r, b = win32gui.GetWindowRect(hwnd)
    if r - l <= 0 or b - t <= 0 or l < -1000 or t < -1000:
        win32gui.SetWindowPos(hwnd, 0, 80, 60, 0, 0, 0x0001 | 0x0004)
        time.sleep(0.3)
    win32gui.SetForegroundWindow(hwnd)
    time.sleep(0.3)


def set_intermediate(win) -> tuple[bool, dict]:
    """读取当前难度，校验是否受支持形状（不自设热键）。"""
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    return calib["rows"] == 16 and calib["cols"] in (16, 30), calib


def classify_sample(frame, calib, c, r, sampled, stats):
    """单格 oracle vs OCR 比对（只在存活棋盘调用，避免雷灰黑色被 oracle 误判为7）。"""
    if (c, r) in sampled:
        return
    cell = grid_perception.slice_cell(frame, calib, c, r)
    if int(cell[0, 0].max()) >= 240:
        return  # 未翻开
    sampled.add((c, r))
    od = oracle_digit(cell)
    state = grid_perception.classify_cell(cell)["state"]
    if od is not None:
        stats["total"] += 1
        stats["digit_counter"][od] += 1
        if state in DIGITS and int(state) == od:
            stats["correct"] += 1
        elif state in DIGITS:
            stats["mism"].append((c, r, od, state))
        else:
            stats["ocr_miss"] += 1
            stats["mism"].append((c, r, od, state))
    else:
        stats["blank"] += 1
        if state in DIGITS:
            stats["false_pos"] += 1
        else:
            stats["blank_ok"] += 1


def sample_board(frame, calib, sampled, stats):
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            classify_sample(frame, calib, c, r, sampled, stats)


def exploded_mine(frame: np.ndarray, calib: dict, c: int, r: int) -> bool:
    """刚点的格是否变成「红底黑色地雷」（winmine 翻中雷的标记）。"""
    cell = grid_perception.slice_cell(frame, calib, c, r)
    region = cell[3:-3, 3:-3]
    if region.size == 0:
        return False
    b = region[:, :, 0].astype(int)
    g = region[:, :, 1].astype(int)
    rr = region[:, :, 2].astype(int)
    red = (rr > 150) & (rr > g * 1.5) & (rr > b * 1.5)
    return float(red.mean()) > 0.25


def _opened_count(frame, calib) -> int:
    ox, oy, cs = calib["board_origin"]["x"], calib["board_origin"]["y"], calib["cell_size"]
    got = 0
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            if int(frame[oy + r * cs, ox + c * cs].max()) < 240:
                got += 1
    return got


def _open_flag(frame, calib) -> np.ndarray:
    """rows×cols 布尔矩阵，True=已翻开。"""
    ox, oy, cs = calib["board_origin"]["x"], calib["board_origin"]["y"], calib["cell_size"]
    flags = np.zeros((calib["rows"], calib["cols"]), dtype=bool)
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            if int(frame[oy + r * cs, ox + c * cs].max()) < 240:
                flags[r, c] = True
    return flags


def _frontier_pick(flags: np.ndarray, rng, frontier_ratio: float) -> tuple[int, int] | None:
    """前沿偏置选格：优先「已翻开格的邻格」（边界数字多）；否则纯随机。返回未翻开格 (c,r)。"""
    rows, cols = flags.shape
    if frontier_ratio > 0 and rng.random() < frontier_ratio:
        frontier = []
        for r in range(rows):
            for c in range(cols):
                if flags[r, c]:
                    continue
                for dr in (-1, 0, 1):
                    for dc in (-1, 0, 1):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < rows and 0 <= nc < cols and flags[nr, nc]:
                            frontier.append((c, r))
                            break
                    else:
                        continue
                    break
        if frontier:
            return frontier[int(rng.integers(0, len(frontier)))]
    covered = np.argwhere(~flags)
    if len(covered) == 0:
        return None
    idx = covered[int(rng.integers(0, len(covered)))]
    return int(idx[1]), int(idx[0])


def main() -> int:
    windowing.enable_dpi_aware()
    cfg = load_cfg()
    win = windowing.find_game_window(cfg)
    if not win:
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    # 移到不遮 (0,0) 的位置：xyzzy 指示像素画在屏幕左上角，须露桌面
    win32gui.SetWindowPos(win["hwnd"], 0, 80, 60, 0, 0, 0x0001 | 0x0004)
    time.sleep(0.3)
    ensure_visible(win)

    games = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    max_reveals = int(sys.argv[2]) if len(sys.argv) > 2 else 20

    ok, calib = set_intermediate(win)
    if not ok:
        print(f"FAIL: unsupported board, got rows={calib['rows']} cols={calib['cols']}")
        return 2
    print(f"calib: {calib['rows']}x{calib['cols']} cell={calib['cell_size']} "
          f"origin={calib['board_origin']}", flush=True)

    stats = {
        "total": 0, "correct": 0, "ocr_miss": 0, "false_pos": 0,
        "blank": 0, "blank_ok": 0, "mism": [],
        "digit_counter": Counter[int](),
    }

    calibrated = False
    for g in range(games):
        frame = capture.grab_client(win)
        restart(win, frame)
        time.sleep(0.2)
        # 中心首击（必安全）
        cc, cr = calib["cols"] // 2, calib["rows"] // 2
        action.reveal(cc, cr, win, calib)
        time.sleep(0.5)
        frame = capture.grab_client(win)

        if not calibrated:
            sx, sy = windowing.resolve_screen_cell(win, calib, cc, cr)
            xyzzy.activate(win["hwnd"])
            if not xyzzy.calibrate(sx, sy):
                print("XYZZY_CALIB_FAIL: backdoor not responding (IME 拦截 / 版本无后门 / (0,0) 被遮挡?)")
                return 3
            calibrated = True
            print("xyzzy calibrated ok", flush=True)

        sampled: set = set()
        sample_board(frame, calib, sampled, stats)
        prev_open = _opened_count(frame, calib)
        revealed = 0
        unknown_run = 0

        for _ in range(max_reveals):
            flags = _open_flag(frame, calib)
            picked = _frontier_pick(flags, np.random.default_rng(), 0.7)
            if picked is None:
                break
            sx, sy = windowing.resolve_screen_cell(win, calib, picked[0], picked[1])
            is_mine = xyzzy.cell_is_mine(sx, sy)
            if is_mine is not False:  # 雷 或 不可判 → 跳过不点
                unknown_run = unknown_run + 1 if is_mine is None else 0
                if unknown_run > 15:
                    print(f"[game {g+1}] xyzzy unreliable (too many unknowns); stopping game", flush=True)
                    break
                continue
            unknown_run = 0
            action.reveal(picked[0], picked[1], win, calib)
            time.sleep(0.45)
            frame = capture.grab_client(win)
            cur_open = _opened_count(frame, calib)
            delta = cur_open - prev_open
            prev_open = cur_open
            if exploded_mine(frame, calib, picked[0], picked[1]) or delta >= 15:
                break  # 意外翻雷（xyzzy 误判）→ 不采样死亡棋盘
            revealed += 1
            sample_board(frame, calib, sampled, stats)

        print(f"[game {g+1}/{games}] reveals_ok={revealed} "
              f"acc_digit={stats['correct']}/{stats['total']} "
              f"blanks={stats['blank']} digits_seen={sorted(stats['digit_counter'])}",
              flush=True)

    total_digits = stats["total"]
    correct = stats["correct"]
    acc = (correct / total_digits) if total_digits else 0.0
    blank_acc = (stats["blank_ok"] / stats["blank"]) if stats["blank"] else 0.0
    print(f"\ngames={games} digit_samples={total_digits} correct={correct} "
          f"ocr_miss={stats['ocr_miss']} acc={acc:.3f} | blank_samples={stats['blank']} "
          f"blank_ok={stats['blank_ok']} blank_acc={blank_acc:.3f} false_pos={stats['false_pos']}")
    print("digit_hist:", dict(sorted(stats["digit_counter"].items())))
    if stats["mism"]:
        print("mismatch(c,r,true,ocr):", stats["mism"][:30])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
