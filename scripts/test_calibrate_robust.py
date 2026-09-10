# -*- coding: utf-8 -*-
"""calibrate_robust 脚本级验收（m1 收尾②，v1.1 §4.3b 独立验收）。

  T1 happy path：新局帧 → 立即返回有效 calib（9×9 走 anchor_assisted，16×16 走 grid_projection）
  T2 非新局自愈：翻开一格后 detect_board=0×0 → 链内脸符 restart → 返回有效 calib 且盘面已重开
  T3 遮挡自愈：tkinter 普通置顶红窗盖住客户区 → 链内 bring_to_foreground 抬回 winmine → 返回有效 calib
  T4 致命路径：注入黑帧 capture_fn（脸符不存在，无点击副作用）→ 全链失败 → CalibrateError

  输出逐项 PASS/FAIL/SKIP，全部通过 exit 0。"""
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from perception import calibrate, capture, windowing  # noqa: E402
from perception.calibrate import CalibrateError, calibrate_robust  # noqa: E402
from action import action  # noqa: E402


def restart_face(win):
    anchor = calibrate.detect_anchor(capture.grab_client(win))
    face = anchor.get("face_rect")
    if face:
        action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                        win["client_rect"]["y"] + face["y"] + face["h"] // 2))
        time.sleep(0.6)


def board_fresh(win, calib) -> bool:
    frame = capture.grab_client(win)
    oy, ox, cs = calib["board_origin"]["y"], calib["board_origin"]["x"], calib["cell_size"]
    n = sum(1 for r in range(calib["rows"]) for c in range(calib["cols"])
            if int(frame[oy + r * cs, ox + c * cs].max()) >= 240)
    return n == calib["rows"] * calib["cols"]


def setup():
    windowing.enable_dpi_aware()
    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    win = windowing.find_game_window(cfg)
    if not win:
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.3)
    restart_face(win)
    return cfg, win


def t1(win) -> bool:
    windowing.bring_to_foreground(win["hwnd"])
    time.sleep(0.2)
    c = calibrate_robust(win)
    ok = calibrate.validate(c) and c["rows"] > 0
    print(f"T1 happy: {c['rows']}x{c['cols']} conf={c['confidence']} "
          f"method={c['method']} -> {'PASS' if ok else 'FAIL'}")
    return ok


def t2(win) -> bool:
    # 制造真正不可检测的盘态：随机点击直至死盘（雷全揭示）→ detect_board 失效。
    # （注：只翻开一格时投影仍成立，detect_board 成功是正确行为，不能作为测试前提。）
    import random
    rng = random.Random(11)
    calib = calibrate.detect_board(capture.grab_client(win))
    if calib["rows"] == 0:
        restart_face(win)
        calib = calibrate.detect_board(capture.grab_client(win))
        if calib["rows"] == 0:
            print("T2: no fresh board to start from -> FAIL")
            return False
    undetectable = False
    for _ in range(40):
        frame = capture.grab_client(win)
        cur = calibrate.detect_board(frame)
        if cur["rows"] == 0:
            undetectable = True
            break
        oy, ox, cs = cur["board_origin"]["y"], cur["board_origin"]["x"], cur["cell_size"]
        covered = [(c, r) for r in range(cur["rows"]) for c in range(cur["cols"])
                   if int(frame[oy + r * cs, ox + c * cs].max()) >= 240]
        if not covered:                       # 通关（极罕见）→ 重开再来
            restart_face(win)
            continue
        c, r = rng.choice(covered)
        action.reveal(c, r, win, cur)
        time.sleep(0.4)
    if not undetectable:
        print("T2: 40 击内未进入不可检测态 -> FAIL")
        return False
    c2 = calibrate_robust(win)                # 应经 restart 自愈
    fresh = board_fresh(win, c2)
    ok = calibrate.validate(c2) and c2["rows"] > 0 and fresh
    print(f"T2 non-fresh: 死盘不可检测={undetectable} robust={c2['rows']}x{c2['cols']} "
          f"重开新局={fresh} -> {'PASS' if ok else 'FAIL'}")
    return ok


def t3(win) -> bool:
    try:
        import tkinter as tk
    except Exception as e:
        print(f"T3 occlusion: tkinter 不可用({e!r}) -> SKIP")
        return True
    frame = capture.grab_client(win)
    calib = calibrate.detect_board(frame)
    if calib["rows"] == 0:
        restart_face(win)
        calib = calibrate.detect_board(capture.grab_client(win))
    cr = win["client_rect"]
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)          # 初始盖住 winmine（新窗口在顶层）
    root.geometry(f"{cr['w']}x{cr['h']}+{cr['x']}+{cr['y']}")
    root.configure(bg="red")
    root.update()
    time.sleep(0.4)
    occluded = calibrate.detect_board(capture.grab_client(win))["rows"] == 0
    c = calibrate_robust(win)                  # 应经 bring_to_foreground 清遮挡
    root.destroy()
    ok = occluded and calibrate.validate(c) and c["rows"] > 0
    print(f"T3 occlusion: 遮挡生效={occluded} robust={c['rows']}x{c['cols']} "
          f"-> {'PASS' if ok else 'FAIL'}")
    return ok


def t4(win) -> bool:
    def black(_w):
        return np.zeros((240, 320, 3), np.uint8)
    try:
        calibrate_robust(win, capture_fn=black,
                         recapture_rounds=2, restart_rounds=2, settle_s=0.1)
        print("T4 fatal: 未抛 CalibrateError -> FAIL")
        return False
    except CalibrateError as e:
        print(f"T4 fatal: CalibrateError({e}) -> PASS")
        return True
    except Exception as e:
        print(f"T4 fatal: 异常类型错误 {e!r} -> FAIL")
        return False


def main():
    cfg, win = setup()
    r = [t1(win), t2(win), t3(win), t4(win)]
    names = ["T1", "T2", "T3", "T4"]
    for n, v in zip(names, r):
        if not v:
            print(f"SUMMARY {n} FAIL")
    print(f"OVERALL: {'PASS' if all(r) else 'FAIL'}")
    sys.exit(0 if all(r) else 1)


if __name__ == "__main__":
    main()
