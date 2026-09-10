# -*- coding: utf-8 -*-
"""模板通道验收：
A. 合成全覆盖：14 类精灵各合成一格 → classify 必须逐一命中（数字 1-8/空白/问号/旗/3 雷态）。
B. 实盘对照：新盘全 covered → 中心首击后逐格 classify vs 颜色 oracle。
"""
import subprocess
import sys
import time

import cv2
import numpy as np
import yaml

ROOT = r"D:\workbuddy\projects\minesweeper-bot"
sys.path.insert(0, ROOT)

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from perception.template_match import load as load_templates  # noqa: E402
from action import action  # noqa: E402

ORACLE = {
    (0, 0, 255): 1, (0, 128, 0): 2, (255, 0, 0): 3, (0, 0, 128): 4,
    (128, 0, 0): 5, (0, 128, 128): 6, (0, 0, 0): 7, (128, 128, 128): 8,
}

log = open(ROOT + r"\scripts\_tmpl_eval.txt", "w", encoding="utf-8")


def out(msg):
    print(msg, file=log)


def oracle_digit(cell):
    """中心 8x8 主色查表（cv2 BGR → 转 RGB 再比对）。"""
    region = cell[4:12, 4:12].reshape(-1, 3)
    cnt = {}
    for p in region:
        cnt[tuple(int(v) for v in p)[::-1]] = cnt.get(tuple(int(v) for v in p)[::-1], 0) + 1
    for rgb, _ in sorted(cnt.items(), key=lambda kv: -kv[1]):
        if rgb in ORACLE:
            return ORACLE[rgb]
    return None


def part_a() -> bool:
    tpls = load_templates()
    ok = True
    canvas_bg = np.full((16, 16, 3), 192, np.uint8)
    for label, t in sorted(tpls.items()):
        cell = t.copy()
        res = grid_perception.classify_cell(cell)
        expect = {
            "blank": "0", "question": "question", "flag": "flag",
            "mine": "mine", "mine_red": "mine_red", "mine_cross": "mine_cross",
            **{f"digit_{i}": str(i) for i in range(1, 9)},
        }[label]
        hit = res["state"] == expect
        ok &= hit
        out(f"A {label:11s} expect={expect:8s} got={res['state']:8s} "
            f"src={res['source']} conf={res['confidence']} {'OK' if hit else 'FAIL'}")
    # covered 合成（白斜面边框）
    cov = np.full((16, 16, 3), 192, np.uint8)
    cov[0, :] = 255; cov[:, 0] = 255
    cov[-1, :] = 128; cov[:, -1] = 128
    res = grid_perception.classify_cell(cov)
    hit = res["state"] == "covered"
    ok &= hit
    out(f"A {'covered':11s} expect={'covered':8s} got={res['state']:8s} {'OK' if hit else 'FAIL'}")
    return ok


def part_b() -> bool:
    windowing.enable_dpi_aware()
    with open(ROOT + r"\config\window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = windowing.find_game_window(cfg)
    if not win:
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    # 先重启新局（避免污染盘校准），再自校准
    frame = capture.grab_client(win)
    anchor = calibrate.detect_anchor(frame)
    face = anchor.get("face_rect")
    if face:
        fx = win["client_rect"]["x"] + face["x"] + face["w"] // 2
        fy = win["client_rect"]["y"] + face["y"] + face["h"] // 2
        action.restart((fx, fy))
        time.sleep(0.6)
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        out("B NO BOARD"); return False
    if (calib["rows"], calib["cols"]) not in ((9, 9), (16, 16), (16, 30)):
        out(f"B BAD SHAPE {calib['rows']}x{calib['cols']}（盘面非新局）"); return False
    # 新盘全 covered 校验
    states = [it["state"] for it in grid_perception.classify_board(frame, calib)]
    n_cov = states.count("covered")
    out(f"B fresh covered={n_cov}/{len(states)}")
    b_ok = n_cov == len(states)
    # 中心首击（死盘则重开重试，保证有数字样本）；存活后追加随机点击增加样本
    tpls = load_templates()

    def board_dead(f):
        for rr in range(calib["rows"]):
            for cc2 in range(calib["cols"]):
                cell = grid_perception.slice_cell(f, calib, cc2, rr)
                if int(cell[0, 0].max()) >= 240:
                    continue
                if float(np.abs(cell.astype(np.int16) - tpls["mine_red"].astype(np.int16)).mean()) < 30:
                    return True
        return False

    def restart_face():
        anchor = calibrate.detect_anchor(capture.grab_client(win))
        face = anchor.get("face_rect")
        if face:
            action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                            win["client_rect"]["y"] + face["y"] + face["h"] // 2))
            time.sleep(0.6)

    def is_mine_like(cell):
        return any(float(np.abs(cell.astype(np.int16) - tpls[lb].astype(np.int16)).mean()) < 30
                   for lb in ("mine", "mine_red", "mine_cross"))

    import random
    rng = random.Random(7)

    # 多命采样：死盘重开，累计 ≥30 个数字样本（去重按格坐标）
    samples = {}   # (c, r) -> (oracle, state)

    def sample_frame(f):
        for rr in range(calib["rows"]):
            for cc2 in range(calib["cols"]):
                if (cc2, rr) in samples:
                    continue
                cell = grid_perception.slice_cell(f, calib, cc2, rr)
                if int(cell[0, 0].max()) >= 240:
                    continue
                if is_mine_like(cell):
                    continue
                od = oracle_digit(cell)
                if od is None:
                    continue
                state = grid_perception.classify_cell(cell)["state"]
                samples[(cc2, rr)] = (od, state)

    restart_face()
    for life in range(6):
        dead = False
        for _ in range(6):
            frame = capture.grab_client(win)
            covered = [(c2, r2) for r2 in range(calib["rows"]) for c2 in range(calib["cols"])
                       if int(grid_perception.slice_cell(frame, calib, c2, r2)[0, 0].max()) >= 240]
            if not covered:
                break
            c2, r2 = rng.choice(covered)
            action.reveal(c2, r2, win, calib)
            time.sleep(0.45)
            frame = capture.grab_client(win)
            sample_frame(frame)
            if board_dead(frame):
                dead = True
                break
        out(f"B life {life}: samples={len(samples)} dead={dead}")
        if len(samples) >= 30 or not dead:
            break
        restart_face()

    total = len(samples)
    correct = sum(1 for od, state in samples.values() if state == str(od))
    mism = [(c, r, od, state) for (c, r), (od, state) in samples.items() if state != str(od)]
    hist: dict[int, int] = {}
    for od, _ in samples.values():
        hist[od] = hist.get(od, 0) + 1
    for c, r, od, state in mism:
        cell = grid_perception.slice_cell(capture.grab_client(win), calib, c, r)
        diffs = {lb: round(float(np.abs(cell.astype(np.int16) - t.astype(np.int16)).mean()), 1)
                 for lb, t in sorted(load_templates().items())}
        out(f"B cell({c},{r}) oracle={od} got={state} tl={tuple(int(v) for v in cell[0,0])} "
            f"diffs={diffs}")
        cv2.imwrite(ROOT + rf"\scripts\_mism_{c}_{r}.png",
                    cv2.resize(cell, None, fx=12, fy=12, interpolation=cv2.INTER_NEAREST))
    acc = correct / total if total else 0.0
    out(f"B digit_samples={total} correct={correct} acc={acc:.3f} hist={dict(sorted(hist.items()))}")
    if mism:
        out(f"B mismatches: {mism[:20]}")
    return b_ok and acc >= 0.99


if __name__ == "__main__":
    a = part_a()
    b = False
    try:
        b = part_b()
    except Exception as ex:
        out(f"B exception: {ex!r}")
    out(f"RESULT A={'PASS' if a else 'FAIL'} B={'PASS' if b else 'FAIL'}")
    log.close()
    with open(ROOT + r"\scripts\_tmpl_eval.txt", encoding="utf-8") as f:
        print(f.read())
    sys.exit(0 if (a and b) else 1)
