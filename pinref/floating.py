"""置顶浮动图窗口。

一张钉在所有窗口最上层的无边框图片：
左键拖动，双击唤出调色面板，右键或 Esc 关闭。

置顶靠 Qt.WindowStaysOnTopHint 这一个标志实现 —— 整个项目的核心就是这行。
无边框（Qt.FramelessWindowHint）去掉了系统标题栏，代价是拖动得自己写。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from pinref.controls import ControlPanel
from pinref.imaging import Adjuster

# 调色面板放在浮窗右边，留这么点缝
PANEL_GAP = 12


class FloatingImage(QWidget):
    """一张置顶的参考图。"""

    closed = Signal()

    def __init__(self, pixmap: QPixmap, top_left: QPoint):
        super().__init__()
        self._pixmap = pixmap
        self._adjuster = Adjuster(pixmap)
        self._panel: ControlPanel | None = None
        self._drag_offset: QPoint | None = None
        self._right_pressed = False

        # 调节值存在浮窗上，不存在面板上 —— 面板可以随时收起重开，
        # 状态得活得比它久，否则重开后滑块归零、图片却还是旧效果
        self._gray = 0.0
        self._hue_shift = 0.0
        self._opacity = 1.0

        # 不能用 Qt.Tool（虽然它能不占程序坞位置）：macOS 上 Tool 窗口会跟着
        # app 的活动 Space 跑，钉在副屏上的图过一会儿会自己飞到主屏去。
        # 对「把图钉住」这件事是致命的，所以用 Qt.Window。
        self.setWindowFlags(
            Qt.FramelessWindowHint  # 去掉标题栏和边框
            | Qt.WindowStaysOnTopHint  # 永远浮在最上层 ← 核心
            | Qt.Window
        )
        self.setWindowTitle("PinRef")
        self.setFocusPolicy(Qt.StrongFocus)

        # 图是 2 倍图时，窗口要按「逻辑尺寸」开，才和原区域一样大
        self.resize(pixmap.deviceIndependentSize().toSize())
        self.move(top_left)

    # ---------- 画面 ----------

    def paintEvent(self, _event):
        painter = QPainter(self)
        # 铺满整个窗口：将来阶段 3 加缩放，改窗口大小就够了
        painter.drawPixmap(self.rect(), self._pixmap)

    # ---------- 调色面板 ----------

    def toggle_panel(self):
        if self._panel is not None:
            self._panel.close()
            return
        panel = ControlPanel()
        # 先把已有的调节值填回去，再接信号 —— 顺序反了会白重算一次
        panel.set_values(self._gray, self._hue_shift, self._opacity)
        panel.changed.connect(self._on_adjust)
        panel.closed.connect(self._on_panel_closed)
        self._panel = panel
        self._place_panel()
        panel.show()
        panel.raise_()

    def _place_panel(self):
        """摆在浮窗右边；右边放不下摆左边；再放不下就贴着屏幕边。

        最后那道兜底不能省：截图接近满屏时左右都塞不下，
        不夹住的话面板会跑到屏幕外面，根本点不到。
        """
        panel = self._panel
        if panel is None:
            return
        # 已显示就用真实尺寸，没显示时 height() 还不准，用 sizeHint
        size = panel.size() if panel.isVisible() else panel.sizeHint()
        screen = self.screen() or QGuiApplication.primaryScreen()
        # 用 availableGeometry 而不是 geometry：躲开菜单栏和程序坞
        area = screen.availableGeometry()

        x = self.x() + self.width() + PANEL_GAP
        if x + size.width() > area.right():
            x = self.x() - size.width() - PANEL_GAP
        # 夹回屏幕内。宁可盖住浮窗一角，也不能让面板跑出屏幕
        x = max(area.left(), min(x, area.right() - size.width()))
        y = max(area.top(), min(self.y(), area.bottom() - size.height()))
        panel.move(x, y)

    def _on_adjust(self):
        panel = self._panel
        if panel is None:
            return

        # 透明度只是窗口属性，不碰像素，直接应用
        self._opacity = panel.opacity()
        self.setWindowOpacity(self._opacity)

        gray, hue_shift = panel.gray(), panel.hue_shift()
        if (gray, hue_shift) == (self._gray, self._hue_shift):
            return  # 只动了透明度，像素不用重算
        self._gray, self._hue_shift = gray, hue_shift

        self._pixmap = self._adjuster.apply(gray=gray, hue_shift=hue_shift)
        self.update()

    def _on_panel_closed(self):
        self._panel = None

    def moveEvent(self, event):
        # 面板跟着浮窗走，免得拖远了控件留在原地
        super().moveEvent(event)
        if self._panel is not None and not self._panel.isHidden():
            self._place_panel()

    # ---------- 拖动与关闭 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 记下鼠标相对窗口左上角的偏移，拖动时保持这个偏移不变
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        elif event.button() == Qt.RightButton:
            # 等松开再关，理由同遮罩：按下就关的话窗口没了，
            # 右键的「松开」会落到背后的窗口上，顺手点出一个右键菜单
            self._right_pressed = True

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
        elif event.button() == Qt.RightButton and self._right_pressed:
            self._right_pressed = False
            self.close()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_panel()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, event):
        # 面板是独立窗口，不会跟着自动关，得手动收掉
        if self._panel is not None:
            self._panel.close()
            self._panel = None
        self.closed.emit()
        super().closeEvent(event)
