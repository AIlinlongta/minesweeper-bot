# -*- coding: utf-8 -*-
"""m1 网格分类准确率评估（多难度 + 每局多点随机块采样）。

真值：经典 winmine 数字色唯一 → 以中心区域「颜色→数字」为独立 oracle（仅测试用，不参与运行时）。

流程：
  1. 切到中级 16×16（F6 快捷键），自校准校验 rows/cols；
  2. 每局：重置 → 首击中心（首步安全，翻开一片）→ 再随机多翻几块（翻到雷即止，已翻开的数字不变仍有效）；
  3. 整盘逐格分类 → OCR 读数 vs oracle 颜色比对。

目的：多点/多难度以覆盖 1-8 全数字与空白样本，避免初级单点只采到 1~3。
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

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from action import action  # noqa: E402

# 经典 winmine 数字色（RGB）→ 数字（oracle）
ORACLE = {
    (0, 0, 255): 1, (0, 128, 0): 2, (255, 0, 0): 3, (0, 0, 128): 4,
    (128, 0, 0): 5, (0, 128, 128): 6, (0, 0, 0): 7, (128, 128, 128): 8,
}
DIGITS = {str(i) for i in range(1, 9)}  # "1".."8"（不含表示空白的 "0"）


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
    """读取当前难度（不自设热键，版本热键不可靠）。校验是否受支持形状。"""
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    return calib["rows"] == 16 and calib["cols"] in (16, 30), calib


def classify_sample(frame, calib, c, r, sampled, stats):
    """对单格作 oracle vs OCR 比对（只在存活棋盘调用，避免雷灰黑色被 oracle 误判为7）。"""
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


def exploded_mine(frame: np.ndarray, calib: dict, c: int, r: int) -> bool:
    """判断刚点的格是否变成「红底黑色地雷」（winmine 翻中雷的标记）。

    被点中的雷：格内大面积红色背景 + 黑地雷（区别于数字3的稀疏纯红像素）。
    """
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
    """返回 rows×cols 布尔矩阵，True=已翻开。"""
    ox, oy, cs = calib["board_origin"]["x"], calib["board_origin"]["y"], calib["cell_size"]
    flags = np.zeros((calib["rows"], calib["cols"]), dtype=bool)
    for r in range(calib["rows"]):
        for c in range(calib["cols"]):
            if int(frame[oy + r * cs, ox + c * cs].max()) < 240:
                flags[r, c] = True
    return flags


def _frontier_pick(flags: np.ndarray, rng, frontier_ratio: float) -> tuple[int, int] | None:
    """前沿偏置选格：优先从「已翻开格的邻格」中选（边界数字多、相对安全）；
    frontier_ratio 概率走前沿，否则纯随机。返回未翻开格 (c,r)。
    """
    rows, cols = flags.shape
    if frontier_ratio > 0 and rng.random() < frontier_ratio:
        frontier = []
        for r in range(rows):
            for c in range(cols):
                if flags[r, c]:
                    continue
                # 周围 8 邻域是否有已翻开格
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
    # 纯随机：找任意未翻开格
    covered = np.argwhere(~flags)
    if len(covered) == 0:
        return None
    idx = covered[int(rng.integers(0, len(covered)))]
    return int(idx[1]), int(idx[0])


def reveal_random_blocks(win, calib, n: int, stats: dict,
                         frontier_ratio: float = 0.7) -> tuple[np.ndarray, int]:
    """重置后连翻：中心安全首击，再随机翻 n 块。

    翻雷判定（双保险）：刚点格「红底黑雷」OR 单步新增翻面 ≥15（雷全部亮相）。
    命中任一条 → 立即停、不采样死亡棋盘；除非从头到尾安全翻完（返回存活态）。
    """
    rng = np.random.default_rng()
    rows, cols = calib["rows"], calib["cols"]
    sampled: set = set()
    # 中心首击（安全）
    action.reveal(cols // 2, rows // 2, win, calib)
    time.sleep(0.5)
    frame = capture.grab_client(win)
    prev_open = _opened_count(frame, calib)
    for r in range(rows):
        for c in range(cols):
            classify_sample(frame, calib, c, r, sampled, stats)
    revealed = 0

    for _ in range(n):
        flags = _open_flag(frame, calib)
        picked = _frontier_pick(flags, rng, frontier_ratio)
        if picked is None:
            break
        action.reveal(picked[0], picked[1], win, calib)
        time.sleep(0.45)
        frame = capture.grab_client(win)
        cur_open = _opened_count(frame, calib)
        delta = cur_open - prev_open
        prev_open = cur_open
        if exploded_mine(frame, calib, picked[0], picked[1]) or delta >= 15:
            revealed += 1
            break  # 翻雷死亡（红底雷 或 雷全体亮相）→ 不采样死亡棋盘
        revealed += 1
        for r in range(rows):
            for c in range(cols):
                classify_sample(frame, calib, c, r, sampled, stats)
    return frame, revealed


def main() -> int:
    windowing.enable_dpi_aware()
    cfg = load_cfg()
    win = windowing.find_game_window(cfg)
    if not win:
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    ensure_visible(win)

    games = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    reveals = int(sys.argv[2]) if len(sys.argv) > 2 else 12

    # 读取当前难度并校验（难度由用户界面预先设定，脚本不自切）
    ok, calib = set_intermediate(win)
    if not ok:
        print(f"FAIL: unsupported board, got rows={calib['rows']} cols={calib['cols']}")
        return 2
    print(f"calib: {calib['rows']}x{calib['cols']} cell={calib['cell_size']} "
          f"origin={calib['board_origin']}", flush=True)

    # 累计统计（dict 便于增量采样器就地更新）
    stats = {
        "total": 0, "correct": 0, "ocr_miss": 0, "false_pos": 0,
        "blank": 0, "blank_ok": 0, "mism": [],
        "digit_counter": Counter[int](),
    }

    for g in range(games):
        frame = capture.grab_client(win)
        restart(win, frame)
        time.sleep(0.2)
        frame, opened = reveal_random_blocks(win, calib, reveals, stats)

        print(f"[game {g+1}/{games}] reveals_ok={opened} "
              f"acc_digit={stats['correct']}/{stats['total']} "
              f"blanks={stats['blank']} digits_seen={sorted(stats['digit_counter'])}",
              flush=True)

    total_digits = stats["total"]
    correct = stats["correct"]
    ocr_misses = stats["ocr_miss"]
    false_positives = stats["false_pos"]
    blank_samples = stats["blank"]
    blank_ok = stats["blank_ok"]
    digit_counter = stats["digit_counter"]
    mismatch_samples = stats["mism"]

    acc = (correct / total_digits) if total_digits else 0.0
    blank_acc = (blank_ok / blank_samples) if blank_samples else 0.0
    print(f"\ngames={games} digit_samples={total_digits} correct={correct} "
          f"ocr_miss={ocr_misses} acc={acc:.3f} | blank_samples={blank_samples} "
          f"blank_ok={blank_ok} blank_acc={blank_acc:.3f} false_pos={false_positives}")
    print("digit_hist:", dict(sorted(digit_counter.items())))
    if mismatch_samples:
        print("mismatch(c,r,true,ocr):", mismatch_samples[:30])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())