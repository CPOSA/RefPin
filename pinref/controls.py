"""调色控制面板：灰度 / 色相 / 透明度三个滑块。

在浮窗上双击唤出或收起。面板自己也是个无边框窗口，可以拖着走。

任何滑块变动都发出 changed，浮窗直接按新参数重算整张图。
全分辨率重算靠 imaging.py 的分块多线程保持流畅，不需要降采样预览。
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSlider,
    QWidget,
)

PANEL_WIDTH = 268
CORNER_RADIUS = 10


class _ResettableSlider(QSlider):
    """双击复位到默认值的滑块，默认值附近还带一点吸附。

    色相的默认值 0 卡在滑槽正中间，而灰度和不透明度的默认值都在两端，
    随手一甩就到。面板宽 268，扣掉边距和标签后滑槽只有 150 上下，
    色相却有 361 个取值，约 2.4°/像素 —— 想拖回正 0 得有像素级精度，
    实测基本靠运气（见 TEST.md D-005）。

    吸附只挂在 sliderMoved 上，不挂 valueChanged。valueChanged 对键盘也会发，
    挂上去的话从 0 按一下方向键到 1，又被吸回 0，方向键在默认值附近就废了。
    sliderMoved 只在鼠标拖动时发，正好只管住"拖不准"这件事。
    """

    def __init__(self, low: int, high: int, default: int, snap: int = 0):
        super().__init__(Qt.Horizontal)
        self.setRange(low, high)
        self.setValue(default)
        self._default = default
        self._snap = snap
        if snap:
            self.sliderMoved.connect(self._snap_to_default)

    def _snap_to_default(self, value: int):
        if value != self._default and abs(value - self._default) <= self._snap:
            self.setValue(self._default)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setValue(self._default)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class _Row:
    """一行滑块：名称、滑块本体、右侧的数值。"""

    def __init__(
        self, name: str, low: int, high: int, initial: int, suffix: str, snap: int = 0
    ):
        self.label = QLabel(name)
        self.slider = _ResettableSlider(low, high, initial, snap)
        self.value = QLabel()
        self.value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.value.setMinimumWidth(46)
        self._suffix = suffix
        self.refresh()

    def refresh(self):
        self.value.setText(f"{self.slider.value()}{self._suffix}")


class ControlPanel(QWidget):
    """三个滑块的调色面板。"""

    changed = Signal()
    closed = Signal()

    def __init__(self):
        super().__init__()
        # 和浮窗同样的理由：不能用 Qt.Tool，macOS 上会被挪到别的屏幕去
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("PinRef 调色")
        self._drag_offset: QPoint | None = None

        self._gray = _Row("灰度", 0, 100, 0, "%")
        # 只有色相需要吸附：默认值在滑槽正中，两端的那两个随手就能拖到
        self._hue = _Row("色相", -180, 180, 0, "°", snap=3)
        self._opacity = _Row("不透明度", 20, 100, 100, "%")
        self._rows = (self._gray, self._hue, self._opacity)

        grid = QGridLayout(self)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(12)
        for i, row in enumerate(self._rows):
            grid.addWidget(row.label, i, 0)
            grid.addWidget(row.slider, i, 1)
            grid.addWidget(row.value, i, 2)
            row.slider.valueChanged.connect(self._on_value_changed)

        self.setFixedWidth(PANEL_WIDTH)
        self.setStyleSheet(_STYLE)

    # ---------- 当前值 ----------

    def gray(self) -> float:
        """0.0 ~ 1.0"""
        return self._gray.slider.value() / 100.0

    def hue_shift(self) -> float:
        """-180 ~ 180 度"""
        return float(self._hue.slider.value())

    def opacity(self) -> float:
        """0.2 ~ 1.0"""
        return self._opacity.slider.value() / 100.0

    def set_values(self, gray: float, hue_shift: float, opacity: float):
        """把已有的调节值填回滑块。

        面板收起再打开时用 —— 不填的话滑块会回到默认值，
        但图片还保持着旧效果，界面和实际就对不上了。

        必须屏蔽信号：否则每设一个值就触发一次重算，
        而且中途的半套参数会让图片闪一下。
        """
        for row, value in (
            (self._gray, round(gray * 100)),
            (self._hue, round(hue_shift)),
            (self._opacity, round(opacity * 100)),
        ):
            row.slider.blockSignals(True)
            row.slider.setValue(int(value))
            row.slider.blockSignals(False)
            row.refresh()

    # ---------- 事件 ----------

    def _on_value_changed(self):
        for row in self._rows:
            row.refresh()
        self.changed.emit()

    def paintEvent(self, _event):
        """自己画圆角背景 —— 无边框窗口没有系统外框。"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(32, 32, 34, 235))
        painter.drawRoundedRect(self.rect(), CORNER_RADIUS, CORNER_RADIUS)

    # 无边框窗口没有标题栏，拖动要自己实现（和浮窗同一套思路）
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

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


_STYLE = """
QLabel {
    color: #e8e8ea;
    font-size: 12px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #4a4a4f;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    height: 4px;
    background: #78bcff;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 13px;
    height: 13px;
    margin: -5px 0;
    border-radius: 7px;
    background: #f2f2f4;
}
QSlider::handle:horizontal:pressed {
    background: #78bcff;
}
"""
