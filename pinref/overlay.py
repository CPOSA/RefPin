"""框选截图的全屏遮罩。

用法：建一个 ScreenSelector，连上 captured 信号，调 start()。
按住左键拖出一块区域，松开就截图；Esc 或右键取消。

多显示器：每块屏幕单独建一个遮罩窗口。macOS 的「显示器各自独立空间」
不允许一个窗口横跨两块屏，所以不能用一个大窗口盖住整个虚拟桌面。
"""

from __future__ import annotations

import mss
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
        """在选框旁边标出尺寸，方便截固定大小的参考图。"""
        label = f"{box.width()} × {box.height()}"
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
        painter.drawText(
            QRect(x, y, text_w, text_h), Qt.AlignCenter, label
        )

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
    """Qt 自带抓屏。坐标直接用逻辑坐标，Retina 下自动给原生分辨率。"""
    screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
    return screen.grabWindow(
        0, rect.x(), rect.y(), rect.width(), rect.height()
    )


def _grab_with_mss(rect: QRect) -> QPixmap:
    """mss 抓屏。

    坑：mss 和 Qt 的坐标单位不一定一样。
    - macOS：mss 用的就是逻辑点，和 Qt 一致，缩放比 = 1。
    - Windows：mss 用物理像素，系统缩放 150% 时缩放比 = 1.5。
    所以缩放比不能写死成 devicePixelRatio，得在运行时用
    「mss 报的屏幕尺寸 ÷ Qt 报的屏幕尺寸」实测出来。
    """
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


def _match_monitor(monitors, geo: QRect) -> tuple[dict, float]:
    """找出 Qt 的这块屏幕对应 mss 的哪个 monitor，顺带算出缩放比。

    mss 和 Qt 各自编号屏幕，顺序不保证一致，所以按「长宽比 + 位置」配对：
    同一块屏幕，mss 尺寸 ÷ Qt 尺寸在横竖两个方向上应该得到同一个缩放比。
    配不上就退回 monitors[0]（整个虚拟桌面），至少不会崩。
    """
    virtual = monitors[0]
    origin = _qt_virtual_origin()
    if geo.width() <= 0 or geo.height() <= 0:
        return virtual, 1.0

    best, best_error = None, None
    for monitor in monitors[1:]:
        scale_x = monitor["width"] / geo.width()
        scale_y = monitor["height"] / geo.height()
        if abs(scale_x - scale_y) > 0.02:
            continue  # 长宽比对不上，不是同一块屏
        scale = (scale_x + scale_y) / 2
        want_left = virtual["left"] + (geo.x() - origin.x()) * scale
        want_top = virtual["top"] + (geo.y() - origin.y()) * scale
        error = abs(monitor["left"] - want_left) + abs(monitor["top"] - want_top)
        if best_error is None or error < best_error:
            best, best_error = (monitor, scale), error

    return best if best is not None else (virtual, 1.0)


def _qt_virtual_origin() -> QPoint:
    """所有屏幕拼起来的左上角，逻辑坐标。副屏摆在主屏左边时会是负数。"""
    screens = QGuiApplication.screens()
    if not screens:
        return QPoint(0, 0)
    return QPoint(
        min(s.geometry().x() for s in screens),
        min(s.geometry().y() for s in screens),
    )
