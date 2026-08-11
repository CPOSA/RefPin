"""框选截图的全屏遮罩。

用法：建一个 ScreenSelector，连上 captured 信号，调 start()。
按住左键拖出一块区域，松开就截图；Esc 或右键取消。

多显示器：每块屏幕单独建一个遮罩窗口。macOS 的「显示器各自独立空间」
不允许一个窗口横跨两块屏，所以不能用一个大窗口盖住整个虚拟桌面。
"""

from __future__ import annotations

import math
import sys

from PySide6.QtCore import QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

# 小于这个尺寸（逻辑点）当成误点，不截图
MIN_SELECTION = 4

# 遮罩变暗的程度，0 全透明 255 全黑
DIM_ALPHA = 110

# 隐藏遮罩后等多久再抓屏（毫秒）。
# 不等的话会把遮罩自己截进去 —— 要给系统时间真的把它擦掉。
HIDE_DELAY_MS = 120

# macOS 的窗口层级：菜单栏在 24、程序坞在 20，而 Qt 的置顶窗口只有 8，
# 所以遮罩默认盖不住这两条。抬到 25（NSStatusWindowLevel）刚好压过它们。
# 实测：level=8 时菜单栏亮度纹丝不动（123.8→123.8），抬到 25 后降到 70.3。
# 不用 CGShieldingWindowLevel（21 亿那个）：效果一样，但它连系统弹窗和屏保
# 都盖，对一个截图遮罩来说太霸道。
_MACOS_ABOVE_MENU_BAR = 25


def _raise_above_menu_bar(widget) -> bool:
    """macOS 上把窗口抬到菜单栏和程序坞之上。其他平台什么都不做。

    没装 pyobjc 也能跑，只是遮罩盖不住那两条，不影响框选本身。
    """
    if sys.platform != "darwin":
        return False
    try:
        import objc
    except ImportError:
        return False
    try:
        native = objc.objc_object(c_void_p=int(widget.winId())).window()
        native.setLevel_(_MACOS_ABOVE_MENU_BAR)
    except Exception:  # noqa: BLE001 —— 抬不动就算了，不能让截图整个挂掉
        return False
    return True


class _ScreenOverlay(QWidget):
    """盖在单块屏幕上的半透明遮罩，负责画框。"""

    selected = Signal(QRect)  # 选好的区域，全局逻辑坐标
    cancelled = Signal()

    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        # setScreen 要在 show 之前调，否则窗口可能跑到主屏上去
        self.setScreen(screen)
        # 不能用 Qt.Tool：macOS 上 Tool 窗口会跟着 app 的活动 Space 跑，
        # 显示约 0.3 秒后会被系统挪到当前显示器上，多屏时遮罩会叠在同一块屏上。
        # Qt.Window 才会老实待在指定屏幕。
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.CrossCursor)
        self.setGeometry(screen.geometry())

        self._origin: QPoint | None = None
        self._cursor: QPoint | None = None

    # ---------- 画面 ----------

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, DIM_ALPHA))

        box = self._box()
        if box is None or box.isEmpty():
            return

        # 把选中区域「擦」成全透明，露出底下的真实画面
        painter.setCompositionMode(QPainter.CompositionMode_Clear)
        painter.fillRect(box, Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)

        painter.setPen(QPen(QColor(120, 190, 255), 1))
        painter.drawRect(box.adjusted(0, 0, -1, -1))
        self._draw_size_hint(painter, box)

    def _draw_size_hint(self, painter: QPainter, box: QRect):
        """在选框旁边标出尺寸，方便截固定大小的参考图。

        标的是**物理像素**，也就是截图真正拿到的像素数。box 本身是逻辑点，
        在 150% 缩放的 4K 屏上框满全屏只有 2560x1440，看着像没截到 4K，
        实际截到的是 3840x2160（见 TEST.md D-006）。参考图关心的是细节有多少，
        所以按物理像素报。
        """
        label = f"{self._physical(box.width())} × {self._physical(box.height())}"
        metrics = painter.fontMetrics()
        pad = 4
        text_w = metrics.horizontalAdvance(label) + pad * 2
        text_h = metrics.height() + pad

        # 默认标在选框上方，顶到屏幕边了就翻到框内
        x = box.left()
        y = box.top() - text_h - 2
        if y < 0:
            y = box.top() + 2

        painter.fillRect(QRect(x, y, text_w, text_h), QColor(0, 0, 0, 180))
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(QRect(x, y, text_w, text_h), Qt.AlignCenter, label)

    def _physical(self, logical: int) -> int:
        return _to_physical(logical, self._screen.devicePixelRatio())

    def _box(self) -> QRect | None:
        """当前选框，本地坐标。

        不用 QRect(起点, 终点)：那个构造函数的坐标是闭区间，
        正着拖和反着拖算出来的宽度会差 2 像素。这里直接用「左上角 + 宽高」，
        两个方向结果一致。
        """
        if self._origin is None or self._cursor is None:
            return None
        x1, y1 = self._origin.x(), self._origin.y()
        x2, y2 = self._cursor.x(), self._cursor.y()
        return QRect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    # ---------- 鼠标键盘 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._origin = event.position().toPoint()
            self._cursor = self._origin
            self.update()
        elif event.button() == Qt.RightButton:
            self.cancelled.emit()

    def mouseMoveEvent(self, event):
        if self._origin is None:
            return
        # 夹在本屏范围内：跨屏拖选暂不支持
        pos = event.position().toPoint()
        pos.setX(max(0, min(pos.x(), self.width() - 1)))
        pos.setY(max(0, min(pos.y(), self.height() - 1)))
        self._cursor = pos
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.LeftButton or self._origin is None:
            return
        box = self._box()
        self._origin = self._cursor = None

        if box is None or box.width() < MIN_SELECTION or box.height() < MIN_SELECTION:
            self.cancelled.emit()
            return

        # 本地坐标 → 全局逻辑坐标
        self.selected.emit(box.translated(self.geometry().topLeft()))

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.cancelled.emit()


class ScreenSelector(QObject):
    """管理所有屏幕上的遮罩，选完负责抓屏。"""

    captured = Signal(QPixmap, QPoint)  # 截好的图 + 它原本在屏幕上的左上角
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self._overlays: list[_ScreenOverlay] = []

    def start(self):
        for screen in QGuiApplication.screens():
            overlay = _ScreenOverlay(screen)
            overlay.selected.connect(self._on_selected)
            overlay.cancelled.connect(self._on_cancelled)
            self._overlays.append(overlay)
            overlay.show()
            # 要在 show 之后抬：窗口得先存在，才拿得到背后的 NSWindow
            _raise_above_menu_bar(overlay)
            overlay.raise_()

        if self._overlays:
            # 只有拿到焦点的那个窗口收得到 Esc
            self._overlays[0].activateWindow()
            self._overlays[0].setFocus()

    def _close_overlays(self):
        for overlay in self._overlays:
            overlay.hide()
            overlay.deleteLater()
        self._overlays.clear()

    def _on_selected(self, rect: QRect):
        self._close_overlays()
        # 先让遮罩从屏幕上消失，再抓屏，否则会把遮罩自己截进去
        QTimer.singleShot(HIDE_DELAY_MS, lambda: self._grab(rect))

    def _on_cancelled(self):
        self._close_overlays()
        self.cancelled.emit()

    def _grab(self, rect: QRect):
        pixmap = grab_screen_rect(rect)
        self.captured.emit(pixmap, rect.topLeft())


# ---------- 抓屏 ----------


# 抓屏用哪个后端。阶段 1 实测对比后选定 "qt"：
#   "qt"  —— Qt 自带的 QScreen.grabWindow。Retina 屏能拿到原生分辨率
#            （整块内置屏 3024×1964），参考图清晰，画画看细节不糊。
#   "mss" —— README 最初选的方案，跨平台、快，但 macOS 上只按逻辑点抓，
#            同样一块屏只有 1512×982，细节丢一半。
# 两者截的是同一块区域（实测内容差异 0.04/255），可随时对调。
CAPTURE_BACKEND = "qt"


def grab_screen_rect(rect: QRect) -> QPixmap:
    """把一块「全局逻辑坐标」区域抓成 QPixmap。"""
    if CAPTURE_BACKEND == "qt":
        return _grab_with_qt(rect)
    return _grab_with_mss(rect)


def _grab_with_qt(rect: QRect) -> QPixmap:
    """Qt 自带抓屏。Retina / 高 DPI 下自动返回原生分辨率。"""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    grab_rect = _qt_capture_rect(rect, screen.geometry())
    return screen.grabWindow(
        0,
        grab_rect.x(),
        grab_rect.y(),
        grab_rect.width(),
        grab_rect.height(),
    )


def _to_physical(logical: int, ratio: float) -> int:
    """逻辑点 → 物理像素，舍入方式对齐 Qt。

    用「加 0.5 再向下取整」而不是内置 round：Python 的 round 是银行家舍入，
    round(2560.5) 得 2560，而 Qt 的 qRound 是四舍五入得 2561。副屏在 150% 下
    逻辑宽 1707，×1.5 正好落在 .5 上，两种舍入会差 1 像素，选框标签就和实际
    截到的尺寸对不上了 —— 实测该屏整屏截图正是 2561x1601。
    """
    return math.floor(logical * ratio + 0.5)


def _qt_capture_rect(
    rect: QRect, screen_geometry: QRect, platform: str | None = None
) -> QRect:
    """把全局逻辑坐标转换成 ``QScreen.grabWindow`` 需要的坐标。

    ``grabWindow(0, x, y, ...)`` 的坐标语义是平台相关的：macOS 使用
    虚拟桌面全局坐标，Windows / X11 使用相对当前屏幕左上角的局部坐标。
    主屏通常从 (0, 0) 开始，所以这个问题只会在副屏暴露出来。

    ``platform`` 主要供跨平台单元测试使用；正常运行时使用 ``sys.platform``。
    """
    current_platform = sys.platform if platform is None else platform
    if current_platform == "darwin":
        return QRect(rect)
    return rect.translated(-screen_geometry.topLeft())


def _grab_with_mss(rect: QRect) -> QPixmap:
    """mss 抓屏。

    坑：mss 和 Qt 的坐标单位不一定一样。
    - macOS：mss 用的就是逻辑点，和 Qt 一致，缩放比 = 1。
    - Windows：mss 用物理像素，系统缩放 150% 时缩放比 = 1.5。
    所以缩放比不能写死成 devicePixelRatio，得在运行时用
    「mss 报的屏幕尺寸 ÷ Qt 报的屏幕尺寸」实测出来。

    import 放在函数里而不是模块顶部：默认后端是 qt，绝大多数运行根本走不到
    这个分支，而 import mss 要 60 ms 上下，摊在启动路径上是白等（见 TEST.md D-007）。
    """
    import mss

    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    geo = screen.geometry()

    with mss.MSS() as sct:  # 注意是 MSS 大写，mss.mss() 在 10.x 已废弃
        monitor, scale = _match_monitor(sct.monitors, geo)
        region = {
            "left": monitor["left"] + round((rect.x() - geo.x()) * scale),
            "top": monitor["top"] + round((rect.y() - geo.y()) * scale),
            "width": max(1, round(rect.width() * scale)),
            "height": max(1, round(rect.height() * scale)),
        }
        shot = sct.grab(region)

    # mss 给的是 BGRA；QImage.Format_RGB32 在内存里正好也是 BGRA 排列，
    # 所以这里可以直接喂，不用翻通道。（阶段 2 拿 numpy 算像素时才要翻。）
    # copy() 是必须的：shot 的缓冲区出了作用域就没了。
    image = QImage(shot.bgra, shot.width, shot.height, QImage.Format_RGB32).copy()

    pixmap = QPixmap.fromImage(image)
    # 告诉 Qt 这张图相对逻辑尺寸放大了几倍，显示时才会缩回原始大小
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def _match_monitor(
    monitors, geo: QRect, screens: list[QRect] | None = None
) -> tuple[dict, float]:
    """找出 Qt 的这块屏幕对应 mss 的哪个 monitor，顺带算出缩放比。

    ``screens`` 是所有 Qt 屏幕的 geometry 列表，默认取当前实际的屏幕。
    显式传入主要供测试模拟混合 DPI 布局用 —— 那种场景没法靠真机复现。

    mss 和 Qt 各自编号屏幕，顺序不保证一致，所以按「长宽比 + 位置」配对：
    同一块屏幕，mss 尺寸 ÷ Qt 尺寸在横竖两个方向上应该得到同一个缩放比。
    配不上就退回 monitors[0]（整个虚拟桌面），至少不会崩。
    """
    virtual = monitors[0]
    physical = monitors[1:]
    if geo.width() <= 0 or geo.height() <= 0 or not physical:
        return virtual, 1.0

    if screens is None:
        screens = [screen.geometry() for screen in QGuiApplication.screens()]

    # 主路径：屏幕数一致时，两边都按「左上 → 右下」排序，取相同名次配对。
    #
    # 依据是两个坐标系描述的是同一套物理排布，相对顺序必然一致。
    # 全程不做任何跨屏坐标换算，所以混合 DPI 也不会错。
    #
    # 反面教材（本项目一度用过的写法）：
    #     want_left = 虚拟桌面left + (本屏Qt_x - 虚拟原点x) * 本屏缩放比
    # 它假设从原点走到本屏这一路的缩放比是统一的。混合 DPI 下不成立 ——
    # 主屏 1920×1080 缩放 150%（Qt 逻辑 1280×720）、副屏 1920×1080 缩放
    # 100% 摆右边时，副屏 Qt 逻辑 x=1280、本屏缩放比 1.0，推算出物理
    # left=1280，实际是 1920，配对直接失败。
    if len(screens) == len(physical):
        ranked_screens = sorted(screens, key=lambda g: (g.x(), g.y()))
        ranked_monitors = sorted(physical, key=lambda m: (m["left"], m["top"]))
        for rank, candidate in enumerate(ranked_screens):
            if candidate != geo:
                continue
            monitor = ranked_monitors[rank]
            scale_x = monitor["width"] / geo.width()
            scale_y = monitor["height"] / geo.height()
            # 长宽比对不上说明这个配对不可信（屏幕数虽然一样，但对应关系乱了），
            # 别硬用，交给下面按尺寸挑
            if abs(scale_x - scale_y) <= 0.02:
                return monitor, (scale_x + scale_y) / 2
            break

    # 兜底：屏幕数对不上（热插拔、镜像等）时，按长宽比挑最接近的一块
    best, best_error = None, None
    for monitor in physical:
        scale_x = monitor["width"] / geo.width()
        scale_y = monitor["height"] / geo.height()
        error = abs(scale_x - scale_y)
        if error > 0.02:
            continue
        if best_error is None or error < best_error:
            best, best_error = (monitor, (scale_x + scale_y) / 2), error

    return best if best is not None else (virtual, 1.0)
