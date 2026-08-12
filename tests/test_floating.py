"""浮窗与调色面板的交互测试。

跑法：
    python tests/test_floating.py
    pytest tests/

这一块反复出过 bug，且都是「状态在两个窗口之间不同步」这一类，
所以每修一个就在这里留一条，防止改别的地方时又退回去。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QEventLoop, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import (
    QGuiApplication,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPixmap,
)
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from pinref.floating import FloatingImage
from pinref.imaging import pixmap_to_rgb

_app = QApplication.instance() or QApplication([])


# ---------- 小工具 ----------


def make_pin(color=(220, 60, 30), size=800, at=None) -> FloatingImage:
    at = at or QPoint(60, 60)
    arr = np.zeros((size, size, 4), dtype=np.uint8)
    arr[:, :, 0], arr[:, :, 1], arr[:, :, 2] = color[2], color[1], color[0]
    arr[:, :, 3] = 255
    image = QImage(arr.data, size, size, size * 4, QImage.Format_RGB32).copy()
    pin = FloatingImage(QPixmap.fromImage(image), at)
    pin.show()
    return pin


def pump(ms: int = 200):
    """转一会儿事件循环，让延后执行的东西有机会跑完。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def press(widget, pos, button=Qt.LeftButton, glob=None):
    widget.mousePressEvent(
        QMouseEvent(
            QEvent.MouseButtonPress,
            QPointF(pos),
            QPointF(glob or pos),
            button,
            button,
            Qt.NoModifier,
        )
    )


def release(widget, pos, button=Qt.LeftButton, glob=None):
    widget.mouseReleaseEvent(
        QMouseEvent(
            QEvent.MouseButtonRelease,
            QPointF(pos),
            QPointF(glob or pos),
            button,
            Qt.NoButton,
            Qt.NoModifier,
        )
    )


def size_of(pin: FloatingImage) -> tuple[int, int]:
    return pin._pixmap.width(), pin._pixmap.height()


# ---------- 画质：任何时候都必须是全分辨率 ----------


def test_dragging_never_downsamples():
    """拖滑块的整个过程都得是全分辨率。

    早期版本拖动时用降采样版保证跟手，松手才变清晰 —— 那个清晰度跳变很扎眼，
    已经改成分块多线程直接算全图。这条防止哪天又把两段式加回来。
    """
    pin = make_pin()
    pin.toggle_panel()
    panel = pin._panel
    seen = set()
    for row in (panel._gray, panel._hue):
        row.slider.setSliderDown(True)
        for value in range(0, 101, 10):
            row.slider.setValue(value if row is panel._gray else value * 3 - 150)
            seen.add(size_of(pin))
        row.slider.setSliderDown(False)
        row.slider.setValue(0)
    assert seen == {(800, 800)}, f"拖动过程中出现过非全分辨率尺寸: {seen}"
    pin.close()


def test_escape_while_slider_held_keeps_full_resolution():
    """按住滑块时按 Esc 收面板，图不能停在低清版本。

    旧版本会：拖动中是降采样，Esc 没走 sliderReleased，补全图的定时器
    就一直不触发，图永久停在降采样版，重开面板也不恢复。
    """
    for row_name in ("_gray", "_hue"):
        pin = make_pin()
        pin.toggle_panel()
        panel = pin._panel
        slider = getattr(panel, row_name).slider
        slider.setSliderDown(True)
        for value in (10, 25, 40, 55):
            slider.setValue(value if row_name == "_gray" else value * 2)
        panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        pump()
        assert pin._panel is None, "Esc 没有收起面板"
        assert size_of(pin) == (800, 800), (
            f"{row_name} 拖动中按 Esc 后停在 {size_of(pin)}"
        )
        pin.close()


def test_closing_pin_mid_drag_does_not_crash():
    """拖动中直接关掉浮窗，不能崩。"""
    pin = make_pin()
    pin.toggle_panel()
    pin._panel._hue.slider.setSliderDown(True)
    pin._panel._hue.slider.setValue(90)
    pin.close()
    pump(100)


# ---------- 面板状态在收起／重开之间必须保住 ----------


def test_panel_reopen_restores_values():
    """收起再打开，滑块要还是原来的值，不能归零。"""
    pin = make_pin()
    pin.toggle_panel()
    pin._panel._gray.slider.setValue(40)
    pin._panel._hue.slider.setValue(60)
    pin._panel._opacity.slider.setValue(55)
    pin.toggle_panel()
    pin.toggle_panel()
    panel = pin._panel
    assert (
        panel._gray.slider.value(),
        panel._hue.slider.value(),
        panel._opacity.slider.value(),
    ) == (40, 60, 55)
    assert (panel._gray.value.text(), panel._hue.value.text()) == ("40%", "60°")
    pin.close()


def test_double_click_resets_slider_to_default():
    """双击滑块回到默认值。

    色相的默认值 0 在滑槽正中，靠拖几乎停不准（TEST.md D-005），
    双击是可靠的那条路，三个滑块都得支持。
    """
    pin = make_pin(size=200)
    pin.toggle_panel()
    panel = pin._panel
    for row, moved, default in (
        (panel._gray, 63, 0),
        (panel._hue, -117, 0),
        (panel._opacity, 45, 100),
    ):
        row.slider.setValue(moved)
        assert row.slider.value() == moved
        row.slider.mouseDoubleClickEvent(
            QMouseEvent(
                QEvent.MouseButtonDblClick,
                QPointF(5, 5),
                QPointF(5, 5),
                Qt.LeftButton,
                Qt.LeftButton,
                Qt.NoModifier,
            )
        )
        assert row.slider.value() == default, f"{row.label.text()} 双击没回到默认值"
    pin.close()


def test_hue_snaps_to_zero_only_while_dragging():
    """色相拖到 0 附近吸附，但方向键不能被吸住。

    吸附挂在 sliderMoved（只有鼠标拖动会发），不挂 valueChanged。
    挂后者的话从 0 按一下方向键到 1 会被吸回 0，方向键在默认值附近就废了。
    """
    pin = make_pin(size=200)
    pin.toggle_panel()
    hue = pin._panel._hue.slider

    # 拖动：落在 ±3 内被吸到 0，超出则原样保留
    for moved, expected in ((2, 0), (-3, 0), (3, 0), (4, 4), (-9, -9)):
        hue.setValue(moved)
        hue.sliderMoved.emit(moved)
        assert hue.value() == expected, f"拖到 {moved} 应得 {expected}"

    # 键盘：逐度步进不受吸附影响，否则 0 附近就调不动了
    hue.setValue(0)
    for _ in range(3):
        QTest.keyClick(hue, Qt.Key_Right)
    assert hue.value() == 3, f"方向键被吸附卡住了，停在 {hue.value()}"
    pin.close()


def test_panel_defaults_and_ranges():
    """面板公开给用户的范围和初始值必须与 README 一致。"""
    pin = make_pin(size=200)
    pin.toggle_panel()
    panel = pin._panel
    assert (panel._gray.slider.minimum(), panel._gray.slider.maximum()) == (0, 100)
    assert (panel._hue.slider.minimum(), panel._hue.slider.maximum()) == (-180, 180)
    assert (panel._opacity.slider.minimum(), panel._opacity.slider.maximum()) == (
        20,
        100,
    )
    assert (panel.gray(), panel.hue_shift(), panel.opacity()) == (0.0, 0.0, 1.0)
    assert (
        panel._gray.value.text(),
        panel._hue.value.text(),
        panel._opacity.value.text(),
    ) == ("0%", "0°", "100%")
    pin.close()


def test_panel_reopen_does_not_reset_image():
    """重开面板后动透明度，画面不能跳回原色。

    旧版本的表现：重开时滑块回到默认值，一动透明度就用默认参数重算，
    图片突然恢复成原图。
    """
    pin = make_pin()
    pin.toggle_panel()
    pin._panel._gray.slider.setValue(40)
    pin._panel._hue.slider.setValue(60)
    adjusted = tuple(int(v) for v in pixmap_to_rgb(pin._pixmap)[0, 0])
    pin.toggle_panel()
    pin.toggle_panel()
    pin._panel._opacity.slider.setValue(70)
    assert tuple(int(v) for v in pixmap_to_rgb(pin._pixmap)[0, 0]) == adjusted
    pin.close()


def test_reopening_panel_does_not_trigger_recompute():
    """重开面板只是把值填回去，不该顺带重算一次图。"""
    pin = make_pin()
    pin.toggle_panel()
    pin._panel._hue.slider.setValue(90)
    before = pin._pixmap
    pin.toggle_panel()
    pin.toggle_panel()
    assert pin._pixmap is before, "重开面板白算了一次"
    pin.close()


# ---------- 透明度不该碰像素 ----------


def test_opacity_only_does_not_touch_pixels():
    """只拖透明度时，图像对象应当原封不动。"""
    pin = make_pin()
    pin.toggle_panel()
    panel = pin._panel
    panel._gray.slider.setValue(30)
    before = pin._pixmap
    panel._opacity.slider.setSliderDown(True)
    for value in (90, 80, 70, 60):
        panel._opacity.slider.setValue(value)
    panel._opacity.slider.setSliderDown(False)
    assert pin._pixmap is before, "拖透明度重算了像素"
    assert abs(pin.windowOpacity() - 0.60) < 0.01
    pin.close()


def test_opacity_applies_without_panel_reopen():
    pin = make_pin()
    pin.toggle_panel()
    pin._panel._opacity.slider.setValue(35)
    assert abs(pin.windowOpacity() - 0.35) < 0.01
    pin.close()


def test_combined_adjustment_matches_adjuster():
    """面板连续修改灰度和色相后，浮窗必须显示最后一组完整参数。"""
    pin = make_pin(size=240)
    pin.toggle_panel()
    pin._panel._hue.slider.setValue(120)
    pin._panel._gray.slider.setValue(37)
    expected = pixmap_to_rgb(pin._adjuster.apply(gray=0.37, hue_shift=120))
    actual = pixmap_to_rgb(pin._pixmap)
    assert np.array_equal(actual, expected)
    pin.close()


# ---------- 面板位置必须留在屏幕内 ----------


def test_panel_stays_on_screen_in_every_corner():
    """浮窗贴边或接近满屏时，面板不能跑到屏幕外面去。"""
    area = QGuiApplication.primaryScreen().availableGeometry()
    cases = {
        "左上": (QPoint(area.left() + 5, area.top() + 5), 300, 200),
        "右上": (QPoint(area.right() - 310, area.top() + 5), 300, 200),
        "左下": (QPoint(area.left() + 5, area.bottom() - 210), 300, 200),
        "右下": (QPoint(area.right() - 310, area.bottom() - 210), 300, 200),
        "近满屏": (
            QPoint(area.left() + 5, area.top() + 5),
            area.width() - 15,
            area.height() - 15,
        ),
    }
    for name, (position, width, height) in cases.items():
        pin = make_pin(size=200, at=position)
        pin.resize(width, height)
        pin.toggle_panel()
        rect = pin._panel.geometry()
        assert (
            rect.left() >= area.left()
            and rect.right() <= area.right()
            and rect.top() >= area.top()
            and rect.bottom() <= area.bottom()
        ), f"{name}: 面板 {rect} 超出可用区域 {area}"
        pin.close()


def test_panel_follows_pin():
    pin = make_pin(size=200)
    pin.toggle_panel()
    before = pin._panel.pos()
    pin.move(pin.x() + 180, pin.y() + 160)
    assert pin._panel.pos() != before, "面板没跟着浮窗走"
    pin.close()


# ---------- 生命周期 ----------


def test_closing_pin_closes_panel():
    pin = make_pin(size=200)
    pin.toggle_panel()
    panel = pin._panel
    closed = []
    pin.closed.connect(lambda: closed.append(1))
    pin.close()
    assert pin._panel is None and not panel.isVisible()
    assert len(closed) == 1


def test_right_click_closes_pin():
    pin = make_pin(size=200)
    pin.toggle_panel()
    closed = []
    pin.closed.connect(lambda: closed.append(1))
    press(pin, QPoint(10, 10), Qt.RightButton)
    release(pin, QPoint(10, 10), Qt.RightButton)
    assert len(closed) == 1 and pin._panel is None


def test_right_click_closes_pin_on_release_not_press():
    """按下时不能关，必须等松开。

    按下就关的话窗口立刻消失，右键的「松开」会落到背后的窗口上，
    而多数程序的右键菜单正是在松开时弹出——关掉参考图会顺手在背后
    点出一个菜单（TEST.md D-011）。整个点击要由本窗口吃掉。
    """
    pin = make_pin(size=200)
    closed = []
    pin.closed.connect(lambda: closed.append(1))

    press(pin, QPoint(10, 10), Qt.RightButton)
    assert not closed, "右键刚按下就关了，松开会漏给背后的窗口"

    release(pin, QPoint(10, 10), Qt.RightButton)
    assert len(closed) == 1


def test_double_click_toggles_panel():
    pin = make_pin(size=200)
    assert pin._panel is None
    pin.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(QPoint(10, 10)),
            QPointF(QPoint(10, 10)),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    assert pin._panel is not None
    pin.mouseDoubleClickEvent(
        QMouseEvent(
            QEvent.MouseButtonDblClick,
            QPointF(QPoint(10, 10)),
            QPointF(QPoint(10, 10)),
            Qt.LeftButton,
            Qt.LeftButton,
            Qt.NoModifier,
        )
    )
    assert pin._panel is None
    pin.close()


def test_escape_from_focused_slider_closes_only_panel():
    """真实键盘事件先到滑块；Esc 必须继续传给面板，且不能关掉浮窗。"""
    pin = make_pin(size=200)
    pin.toggle_panel()
    slider = pin._panel._hue.slider
    slider.setFocus()
    QTest.keyClick(slider, Qt.Key_Escape)
    QApplication.processEvents()
    assert pin._panel is None
    assert pin.isVisible()
    pin.close()


def test_windows_are_not_qt_tool():
    """浮窗和面板都不能是 Qt.Tool —— macOS 上会自己飞到别的屏幕。"""
    pin = make_pin(size=200)
    pin.toggle_panel()
    for name, widget in (("浮窗", pin), ("面板", pin._panel)):
        kind = widget.windowFlags() & Qt.WindowType_Mask
        assert kind == Qt.Window, f"{name} 的窗口类型是 {kind!r}，应为 Qt.Window"
        assert widget.windowFlags() & Qt.WindowStaysOnTopHint, f"{name} 没置顶"
    pin.close()


def test_pin_keeps_logical_size_after_adjustment():
    """调色不能改变浮窗大小。"""
    pin = make_pin(size=400)
    before = (pin.width(), pin.height())
    pin.toggle_panel()
    pin._panel._gray.slider.setValue(70)
    pin._panel._hue.slider.setValue(-120)
    assert (pin.width(), pin.height()) == before
    pin.close()


if __name__ == "__main__":
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            print(f"  FAIL  {name}: {exc}")
            failed.append(name)
    print(
        f"\n  {len(tests) - len(failed)}/{len(tests)} 通过"
        + (f"，失败: {failed}" if failed else "")
    )
    sys.exit(1 if failed else 0)
