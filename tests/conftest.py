# -*- coding: utf-8 -*-
"""确保项目根目录在 sys.path（任意 cwd 下可 from decision.solver import ...）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
