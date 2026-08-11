# PinRef

PinRef 是一个基于 Python 和 Qt 的跨平台桌面参考图工具。它允许用户截取屏幕区域，将截图固定为无边框、置顶、可拖动的悬浮窗口，并实时调整灰度、色相和透明度。

项目面向绘画、设计和视觉对照等需要持续查看局部参考内容的工作流。当前已完成基础截图悬浮能力和第二阶段图像调整功能，第三阶段功能正在规划中。

## 当前能力

- 在多显示器环境中为每块屏幕创建选区遮罩
- 拖拽选取屏幕区域并生成悬浮参考图
- 悬浮窗口无边框、始终置顶且支持拖动
- 控制面板支持灰度、色相和窗口透明度调整
- 保留截图的设备像素比和颜色空间信息
- 默认使用 Qt 截图，必要时可切换到 `mss` 后端
- 大图调整使用 NumPy 分块并行计算，界面线程只负责 Qt 对象更新

当前目标平台为 macOS 和 Windows。自动化测试覆盖了平台坐标转换、设备像素比和核心交互逻辑；Windows 版本仍应按照 [TEST.md](TEST.md) 在真实设备上完成验收。

## 环境要求

- Python 3.10 或更高版本
- macOS 或 Windows
- macOS 首次运行时需要授予终端或 Python 进程“屏幕与系统音频录制”权限

项目依赖定义在 [requirements.txt](requirements.txt)：

- `PySide6`：窗口、事件和默认截图后端
- `numpy`：图像调整计算
- `mss`：备用截图后端
- `pyobjc-framework-Cocoa`：macOS 应用激活策略
- `Pillow`：图像格式相关能力的预留依赖

## 快速开始

### macOS / Linux shell

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pinref.main
```

### Windows PowerShell

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pinref.main
```

Linux 目前不是正式支持的平台；上述命令仅表示项目可在常见 shell 环境中安装，平台行为未纳入当前验收范围。

## 使用方式

| 场景 | 操作 |
| --- | --- |
| 创建参考图 | 启动后按住鼠标左键拖出选区 |
| 取消选择 | 在选区界面按 `Esc` 或右键 |
| 移动悬浮图 | 按住悬浮图拖动 |
| 打开或关闭控制面板 | 双击悬浮图 |
| 调整图像 | 使用控制面板中的灰度、色相和透明度滑块 |
| 关闭控制面板 | 控制面板获得焦点时按 `Esc` |
| 关闭参考图 | 在悬浮图上右键或按 `Esc` |

选区必须位于同一块显示器内。当前进程只创建一次选区会话；关闭最后一个悬浮图后应用退出。

## 项目结构

```text
pinref/
├── __init__.py
├── main.py       # 应用入口、窗口生命周期和 macOS 激活策略
├── overlay.py    # 多屏遮罩、选区交互和截图后端
├── floating.py   # 悬浮图窗口、拖动和控制面板生命周期
├── controls.py   # 图像调整控制面板
└── imaging.py    # 灰度/色相计算、缓存和并行处理
tests/
├── test_imaging.py
├── test_floating.py
└── test_overlay.py
TEST.md           # 第二阶段测试清单和验收记录
requirements.txt
```

核心调用关系如下：

```text
main
└── ScreenSelector
    └── _ScreenOverlay × 显示器数量
        └── grab_screen_rect()
            └── FloatingImage
                ├── ControlPanel
                └── Adjuster
```

## 模块说明

### `pinref.main`

创建 `QApplication` 和 `ScreenSelector`，接收截图结果并管理悬浮窗口生命周期。在 macOS 上通过 AppKit 将应用设置为 accessory 模式，因此应用不会显示在 Dock 或 `Cmd+Tab` 列表中。

### `pinref.overlay`

为每块 `QScreen` 创建全屏遮罩，并处理选区绘制、最小选区校验和截图。默认 Qt 后端会根据平台选择正确的坐标语义：

- macOS 使用虚拟桌面全局坐标
- Windows 和 Linux 使用相对于目标屏幕的局部坐标

这一区分用于避免 Windows 副屏存在负坐标或非零原点时截取错误区域。

### `pinref.floating`

显示无边框、置顶的参考图，处理拖动、关闭和控制面板定位。窗口使用 `Qt.Window`，而不是 `Qt.Tool`；这是为了避免 macOS 多桌面环境中窗口无法正常移动的问题。

### `pinref.controls`

提供灰度、色相和透明度滑块。面板会根据悬浮图与当前屏幕的相对位置选择合适的显示方向，尽量避免超出屏幕可用区域。

### `pinref.imaging`

`Adjuster` 保存原始像素数据及派生缓存，并根据控制参数生成新的 `QImage`：

1. 将截图转换为 RGB 数组
2. 缓存 HSV 分量和原图亮度
3. 按行分块执行 NumPy 计算
4. 生成 BGRA 输出并更新 `QImage`
5. 恢复原截图的颜色空间元数据

灰度使用原图亮度计算，因此灰度为 `100%` 时再调整色相不会改变亮度。透明度由窗口级 `windowOpacity` 实现，不参与像素重建。

## 截图后端

后端由 `pinref.overlay.CAPTURE_BACKEND` 控制：

| 值 | 行为 |
| --- | --- |
| `"qt"` | 默认。使用 `QScreen.grabWindow()`，保留 Qt 的设备像素比处理 |
| `"mss"` | 使用虚拟桌面物理像素坐标截图，作为兼容性回退 |

两种后端的坐标单位不同。修改截图代码时，不应直接复用同一套全局坐标计算；应同时验证普通缩放、Retina/高 DPI、负坐标副屏和不同缩放比例的多屏组合。

## 关键配置

以下参数当前以模块常量形式维护：

| 位置 | 参数 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `pinref.overlay` | `CAPTURE_BACKEND` | `"qt"` | 截图后端 |
| `pinref.overlay` | `MIN_SELECTION` | `4` | 选区最小边长，单位为逻辑像素 |
| `pinref.overlay` | `DIM_ALPHA` | `110` | 选区遮罩透明度 |
| `pinref.overlay` | `HIDE_DELAY_MS` | `120` | 遮罩隐藏后开始截图的等待时间 |
| `pinref.floating` | `PANEL_GAP` | `12` | 控制面板与悬浮图的间距 |

如需将这些设置暴露给用户，建议先集中为配置对象，避免业务模块之间产生新的全局状态。

## 测试

测试文件可以直接运行，不依赖额外的测试框架：

```bash
python tests/test_imaging.py
python tests/test_floating.py
python tests/test_overlay.py
```

GUI 测试应串行执行，避免多个 `QApplication` 同时争用显示环境。在无图形会话的 CI 中，可使用：

```bash
QT_QPA_PLATFORM=offscreen python tests/test_floating.py
```

PowerShell 中可先执行 `$env:QT_QPA_PLATFORM = "offscreen"`，再运行同一测试命令。

当前共有 56 项自动化检查：

- 图像处理：33 项
- 悬浮窗口与控制面板：18 项
- 选区与跨平台坐标：5 项

提交前建议同时执行：

```bash
python -m compileall pinref tests
python -m ruff check pinref tests
python -m ruff format --check pinref tests
git diff --check
```

`ruff` 是可选的开发工具，未包含在运行时依赖中。完整的手工验收矩阵、性能检查和阶段二测试记录见 [TEST.md](TEST.md)。

## 跨平台开发约束

- `QPixmap`、窗口和其他 GUI 对象必须只在 Qt 主线程中创建或修改。
- 工作线程只处理 NumPy 数组；计算完成后由主线程更新图像。
- 修改像素格式时必须同时保留 `devicePixelRatio` 和 `QColorSpace`。
- Qt 截图的矩形参数在各平台并非相同坐标空间，相关修改必须补充平台回归测试。
- macOS 上不要直接将悬浮窗口改回 `Qt.Tool`，除非已重新验证多桌面和多显示器行为。
- 选区遮罩隐藏后需要留出短暂延迟，否则截图可能包含正在消失的遮罩窗口。
- 性能优化不能改变“所有调整都以原始截图为输入”的语义，避免连续调整造成累计失真。

## 已知限制

- 不支持跨越两块显示器的单次选区。
- 每次启动只进行一次截图选择，尚无全局快捷键再次触发截图。
- 尚未支持滚轮缩放、镜像、取色器、文件导入和参考图保存。
- 全分辨率大图会保留多份像素缓存，超高分辨率截图的内存占用仍需继续优化。
- macOS accessory 模式下应用没有 Dock 图标；如果界面不可见，可在终端使用 `pkill -f "pinref.main"` 结束残留进程。
- 受系统保护的窗口、登录界面或 DRM 内容可能无法被正常截取。

## 开发路线

### 已完成

- 阶段一：选区截图、悬浮显示、拖动、置顶和关闭
- 阶段二：控制面板、灰度、色相、透明度和大图实时处理

### 下一阶段

- 滚轮缩放
- 水平/垂直镜像
- 全局截图快捷键
- 取色器

后续候选能力包括参考线网格、放大镜、多参考图管理、标签分组、色温调整和状态保存/重置。路线中的功能在完成实现与测试前不视为已支持。

## 故障排查

### macOS 截图为空或内容不正确

在“系统设置 → 隐私与安全性 → 屏幕与系统音频录制”中授权当前终端或 Python 解释器，然后完全退出并重新运行应用。

### 控制台出现 `IMKClient` 或 `IMKInputSession` 日志

这是 macOS 输入法框架的系统日志，通常不代表应用异常。

### Windows 副屏截图位置错误

先确认正在运行最新代码，并使用默认 `qt` 后端复测。若问题仍存在，请记录主副屏排列、每块屏幕的缩放比例、选区逻辑坐标和实际截图内容，并按 [TEST.md](TEST.md) 中的跨屏用例提交复现信息。
