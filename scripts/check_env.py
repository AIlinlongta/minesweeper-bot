# m0 前环境自检：Python 依赖可用性
# 用法: python scripts/check_env.py
import sys

def check_deps():
    ok = True
    for m in ["mss", "pydirectinput", "win32gui", "cv2", "numpy", "yaml"]:
        try:
            __import__(m)
            print(f"[OK]   {m}")
        except Exception as e:
            ok = False
            print(f"[FAIL] {m}: {e}")
    return ok

if __name__ == "__main__":
    d = check_deps()
    print("=" * 40)
    print("环境就绪" if d else "环境有问题，见上方 FAIL 项")
    sys.exit(0 if d else 1)
