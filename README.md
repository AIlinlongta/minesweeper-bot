# minesweeper-bot

Windows 经典扫雷（winmine）端到端自动化 bot：屏幕感知 → 自校准 → 策略求解 → 点击注入，全程无游戏内后门，真值只来自游戏自身渲染与胜负结算。

当前版本：**v1.0**（阶段0 扫雷轻量预演完成）

## 功能特性

- **同源模板感知**：精灵条带按像素规则切分（`perception/templates/`），逐格分类与实盘渲染 diff=0.0
- **轻量 CNN 兜底**：13 类 16×16 单格分类器（PyTorch 训练 → ONNX 推理），合成 5200 样本 val_acc=1.0，实盘交叉验证一致率 100%
- **精确求解器**：约束传播 + 分量回溯枚举（前沿上限回退单约束近似）+ 全局雷数守恒，可解局 100% 全解（500 局合成盘验收）
- **鲁棒校准**：低置信度锚点辅助 → 重抓 → 前台化 → 重启自愈链（`calibrate_robust`），遮挡/丢窗/分辨率漂移自动恢复
- **LED 计数器对账**：读数与盘面旗数交叉校验，两轮自愈，感知漂移兜底
- **主循环编排**：角落首击策略、周期异常隔离、点击回环验证、FrameState JSONL 落盘
- **可选 LLM 决策**（m4 实验分支）：`--brain llm` 问答式解题，stub 离线联调 + OpenAI 兼容端点

## 项目结构

```
main.py                  # 主循环入口（boot / play_one_game / run_m3）
perception/              # 截图、模板匹配、CNN 分类、状态聚合、窗口管理、校准
  templates/             # 精灵模板（数字/旗/雷/问号/LED 计数器）
decision/solver.py       # 纯矩阵求解器（propagate/probability/solve，可脱离图像单测）
action/action.py         # pydirectinput 点击注入 + 反馈回灌
labeling/                # 合成数据、CNN 训练、ONNX 导出
models/                  # cnn_cell.onnx / cnn_cell.pt
scripts/                 # 各里程碑验收与探针脚本（eval_m2/m3/m15、probe_*）
tests/                   # pytest 单测（合成棋盘模拟）
docs/                    # 设计文档（v1.1）、收尾总结
apps/winmine.exe         # 韩文版 winmine（Korean port）
config/                  # window.yaml（窗口/盘型）、llm.yaml（LLM 实验，密钥走环境变量）
```

## 环境要求

- Windows（依赖 GDI 截图 + SendInput 注入）
- Python 3.13（本项目使用 `.venv` 虚拟环境）

关键依赖：`opencv-python`、`numpy`、`mss`、`PyDirectInput`、`pywin32`、`onnxruntime`；训练侧另需 `torch`（CPU 版即可）。

```powershell
python -m venv .venv
.venv\Scripts\pip install opencv-python numpy mss PyDirectInput pywin32 onnxruntime pytest
```

## 快速开始

```powershell
# 20 局初级 9x9/10 实盘自动对局
.venv\Scripts\python.exe main.py --runs 20

# 自定义盘型（高级）
.venv\Scripts\python.exe main.py --runs 5 --height 16 --width 30 --mines 99

# LLM 问答式决策（实验，需配置 config/llm.yaml 并设置 LLM_API_KEY）
.venv\Scripts\python.exe main.py --runs 5 --brain llm
```

### 验收脚本

| 脚本 | 说明 |
|---|---|
| `scripts/eval_m3.py --runs 20` | m3 端到端验收（A 正常结算 / B 胜率≥80% / C 可解局全解 / D 对账零失败） |
| `scripts/eval_m2.py` | 求解器 500 局合成盘批量验收 |
| `scripts/eval_m15.py` | CNN 分类合成 + 实盘交叉验证 |
| `scripts/eval_templates.py` | 模板识别准确率 |
| `scripts/test_calibrate_robust.py` | 校准鲁棒链 T1-T4 |

```powershell
.venv\Scripts\python.exe -m pytest tests/    # 单元测试
```

## v1.0 验收结果

- **m2 求解器**：500 局 9×9/10 合成盘，可解局 442/442 = 100% 全解，零缺陷
- **m3 端到端**：20/20 正常结算、胜率 ≥80%、可解局 8/8 全解、LED 对账 reconcile_fail=0
- **感知层**：模板 15 类实盘 acc=1.000；6 精灵实盘 diff=0.0；CNN 交叉验证一致率 100%
- **高级盘 16×30/99**：20 局全部正常结算，胜 4/20（符合 99 雷精确求解理论预期）

## 运行注意

- 运行期间**勿移动鼠标、勿用其他窗口遮挡游戏窗口**，否则干扰截图/注入（bot 具备自愈，但会影响验收统计）
- 高 DPI/RDP 环境下 winmine 存在 1×/2× 渲染漂移问题，项目已通过 Per-Monitor DPI Aware 兼容标记固定 16px 格；异常时 bot 会冷重启自愈
- 中文输入法悬浮窗会污染截图，bot 前台化时会自动切英文输入法并隐藏 IME 窗口

## 设计文档

详见 [docs/阶段0-扫雷轻量预演-开发设计-v1.md](docs/阶段0-扫雷轻量预演-开发设计-v1.md)（v1.1：模板主通道、计数器对账 m3 化、低置信度兜底两层拆分）与 [docs/阶段0-收尾总结-v1.md](docs/阶段0-收尾总结-v1.md)。

## 许可与用途

仅供学习研究窗口自动化、感知与决策系统工程的实践用途，请勿用于任何竞技/排行场景。
