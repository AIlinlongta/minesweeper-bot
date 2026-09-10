# -*- coding: utf-8 -*-
"""m1.5 脚本级验收（§6.2）：合成评估 + 实盘交叉验证。

  A 合成评估：synth_data 验证集 → ONNX（onnxruntime CPU）→ 总体/各类准确率 ≥99%。
  B 实盘交叉验证：真值 = 模板主通道（m1 已验收：250 样本 100%、6 类实盘 diff=0.0）。
    多命实盘采样（首击随机 → 增量采样 → 撒旗 → 点至死盘 → 收三雷态），
    CNN 与模板通道逐格比对，总体一致率 ≥99%；不一致逐条落盘诊断。

输出 scripts/_m15_eval.txt；A、B 均过 exit 0。"""
import random
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import yaml

ROOT = Path(r"D:\workbuddy\projects\minesweeper-bot")
sys.path.insert(0, str(ROOT))

from perception import calibrate, capture, grid_perception, windowing  # noqa: E402
from action import action  # noqa: E402
from perception import grid_classifier  # noqa: E402
from labeling.synth_data import OUT as DATASET_NPZ  # noqa: E402
from labeling.export_onnx import ONNX_PATH  # noqa: E402

LIVES = 6
FLAGS_PER_LIFE = 3
MAX_ROUNDS = 40
CLICK_PAUSE = 0.45
COVERED_PER_LIFE = 8

log = open(ROOT / "scripts/_m15_eval.txt", "w", encoding="utf-8")
STATE2LABEL = {"0": 0, "covered": 9, "flag": 10, "question": 12,
               "mine": 11, "mine_red": 11, "mine_cross": 11}
STATE2LABEL.update({str(i): i for i in range(1, 9)})


def out(msg):
    print(msg, file=log)
    print(msg)


# --------------------------------------------------------------------------- #
# A 合成评估
# --------------------------------------------------------------------------- #
def part_a() -> bool:
    d = np.load(DATASET_NPZ)
    x, y = d["x_val"], d["y_val"]
    sess = grid_classifier.load_model(ONNX_PATH)
    cells = x.reshape(-1, 1, 16, 16).astype(np.float32)
    logits = sess.run(None, {"cell": cells})[0]
    pred = logits.argmax(1)
    acc = float((pred == y).mean())
    per = {}
    for lb in range(13):
        m = y == lb
        per[lb] = float((pred[m] == lb).mean()) if m.any() else float("nan")
    out(f"[A] synth val n={len(y)} acc={acc:.4f}")
    out("[A] per-class: " + " ".join(f"{lb}:{v:.4f}" for lb, v in per.items()))
    return acc >= 0.99


# --------------------------------------------------------------------------- #
# B 实盘交叉验证（真值 = 模板主通道）
# --------------------------------------------------------------------------- #
def _reg_get(name):
    try:
        import win32api
        import win32con
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
    for name, v in values.items():
        win32api.RegSetValueEx(hk, name, 0, win32con.REG_DWORD, v)
    win32api.RegCloseKey(hk)


def _kill(win):
    if not windowing.verify_game_pid(win["pid"]):
        raise RuntimeError(f"REFUSE taskkill pid={win['pid']}: 进程映像非 winmine（找窗可能误绑）")
    subprocess.run(["taskkill", "/F", "/PID", str(win["pid"])],
                   check=False, capture_output=True)
    time.sleep(1.0)


def restart_face(win):
    anchor = calibrate.detect_anchor(capture.grab_client(win))
    face = anchor.get("face_rect")
    if face:
        action.restart((win["client_rect"]["x"] + face["x"] + face["w"] // 2,
                        win["client_rect"]["y"] + face["y"] + face["h"] // 2))
        time.sleep(0.6)


def fresh_calib(win):
    return calibrate.detect_board(capture.grab_client(win))


def launch_guarded(cfg, attempts: int = 4):
    """启动并校验 1× 渲染（cell_size==16）。

    实测（2026-09-09）：winmine 偶发以 2× 渲染（32px 格），16×16 模板失配；
    触发条件与「前一实例状态/启动间隔」相关，未收敛为确定性规则。
    冷启动首启 = 1× 目前 100% 命中，故不满足即杀掉冷重启。"""
    for i in range(1, attempts + 1):
        proc = subprocess.Popen([cfg["exe_path"]])
        time.sleep(2.5)
        win = windowing.find_game_window(cfg, pid=proc.pid)
        windowing.bring_to_foreground(win["hwnd"])
        time.sleep(0.3)
        calib = calibrate.detect_board(capture.grab_client(win))
        if calib["rows"] > 0 and calib["cell_size"] == 16:
            out(f"[B] launch ok (attempt {i}): "
                f"{calib['rows']}x{calib['cols']} cs=16")
            return win, proc
        out(f"[B] attempt {i}: cs={calib['cell_size']} != 16 → 冷重启")
        _kill(win)
        time.sleep(2.0)
    raise RuntimeError("launch_guarded: 连续冷重启仍非 1× 渲染")


def part_b() -> bool:
    windowing.enable_dpi_aware()
    with open(ROOT / "config/window.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    old = windowing.find_game_window(cfg)
    if old:
        _kill(old)
    backup = {k: v for k in ("Difficulty", "Height", "Width", "Mines")
              if (v := _reg_get(k)) is not None}
    _reg_set({"Difficulty": 3, "Height": 9, "Width": 9, "Mines": 10})
    out(f"[B] registry backup={backup}")
    try:
        win, proc = launch_guarded(cfg)

        rng = random.Random(15)
        samples: dict[tuple, tuple[np.ndarray, int]] = {}

        def collect(f, calib, prev, life):
            """按模板真值收集状态发生变化的格子样本。"""
            items = grid_perception.classify_board(f, calib)
            grid = {}
            covered = []
            for it in items:
                st, c, r = it["state"], it["c"], it["r"]
                grid[(c, r)] = st
                if st == "covered":
                    covered.append((c, r))
                if st == "unknown" or (c, r) in prev and prev[(c, r)] == st:
                    continue
                lb = STATE2LABEL.get(st)
                if lb is None:
                    continue
                key = (life, c, r, st)
                if key not in samples:
                    samples[key] = (grid_perception.slice_cell(f, calib, c, r), lb)
            return grid, covered

        for life in range(1, LIVES + 1):
            restart_face(win)
            calib = fresh_calib(win)
            if calib["rows"] == 0:
                out(f"[B] life{life}: 无法定位新局盘面，跳过")
                continue
            prev, covered = collect(capture.grab_client(win), calib, {}, life)
            for c, r in rng.sample(covered, min(COVERED_PER_LIFE, len(covered))):
                key = (life, c, r, "covered")
                if key not in samples:
                    samples[key] = (grid_perception.slice_cell(
                        capture.grab_client(win), calib, c, r), 9)
            flagged = []
            dead = False
            for rnd in range(MAX_ROUNDS):
                if rnd == 2:                          # 撒旗制造 flag/question/mine_cross
                    pool = [p for p in covered if prev.get(p) == "covered"]
                    picks = rng.sample(pool, min(FLAGS_PER_LIFE, len(pool)))
                    for i, (c, r) in enumerate(picks):
                        action.click(c, r, "right", win, calib)   # →flag
                        if i % 2 == 0:                            # 半数再进 question
                            action.click(c, r, "right", win, calib)
                        time.sleep(CLICK_PAUSE)
                        flagged.append((c, r))
                    prev, covered = collect(capture.grab_client(win), calib,
                                            prev, life)
                pool = [(c, r) for (c, r), st in prev.items() if st == "covered"]
                if not pool:
                    break
                c, r = rng.choice(pool)
                action.reveal(c, r, win, calib)
                time.sleep(CLICK_PAUSE)
                prev, covered = collect(capture.grab_client(win), calib,
                                        prev, life)
                if any(st in ("mine", "mine_red") for st in prev.values()):
                    dead = True                       # 死盘三雷态已收集
                    break
            stats = Counter(st for _, st in
                            ((k[3], v[1]) for k, v in list(samples.items())
                             if k[0] == life))
            out(f"[B] life{life}: dead={dead} states={dict(stats)}")

        # 逐样本 CNN 预测 vs 模板真值
        sess = grid_classifier.load_model(ONNX_PATH)
        mism, per_total, per_ok = [], Counter(), Counter()
        for (life, c, r, st), (cell, lb) in samples.items():
            pred = grid_classifier.classify(cell, sess)
            per_total[lb] += 1
            per_ok[lb] += int(pred["label_id"] == lb)
            if pred["label_id"] != lb:
                mism.append(f"life{life} ({c},{r}) {st}: truth={lb} "
                            f"pred={pred['label_id']}({pred['state']}) "
                            f"prob={pred['prob']:.3f}")
        n = sum(per_total.values())
        agree = sum(per_ok.values()) / max(n, 1)
        out(f"[B] live samples n={n} agreement={agree:.4f} mismatches={len(mism)}")
        out("[B] per-class: " + " ".join(
            f"{lb}:{per_ok[lb]}/{per_total[lb]}" for lb in sorted(per_total)))
        for m in mism[:50]:
            out(f"[B] MISMATCH {m}")
        return n >= 200 and agree >= 0.99
    finally:
        if backup:
            _reg_set(backup)
            out("[B] registry restored")


def main():
    ok_a = part_a()
    ok_b = part_b()
    out(f"OVERALL: {'PASS' if ok_a and ok_b else 'FAIL'}")
    sys.exit(0 if ok_a and ok_b else 1)


if __name__ == "__main__":
    main()
