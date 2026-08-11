"""置顶浮动图窗口。

一张钉在所有窗口最上层的无边框图片：左键拖动，右键或 Esc 关闭。

置顶靠 Qt.WindowStaysOnTopHint 这一个标志实现 —— 整个项目的核心就是这行。
无边框（Qt.FramelessWindowHint）去掉了系统标题栏，代价是拖动得自己写。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget


class FloatingImage(QWidget):
    """一张置顶的参考图。"""

    closed = Signal()

    def __init__(self, pixmap: QPixmap, top_left: QPoint):
        super().__init__()
        self._pixmap = pixmap
        self._drag_offset: QPoint | None = None

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

    # ---------- 拖动与关闭 ----------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 记下鼠标相对窗口左上角的偏移，拖动时保持这个偏移不变
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
        elif event.button() == Qt.RightButton:
            self.close()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
