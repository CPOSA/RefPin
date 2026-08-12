"""截图坐标换算的跨平台测试。

这些测试不需要真的连接多块显示器，而是用 QRect 模拟 Windows / macOS
虚拟桌面布局，防止只在开发机主屏上正常、换到副屏就截偏。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, Qt
from PySide6.QtGui import QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QApplication

from pinref.overlay import (
    _qt_capture_rect,
    _ScreenOverlay,
    _to_physical,
)

_app = QApplication.instance() or QApplication([])


def test_primary_screen_coordinates_are_unchanged():
    screen = QRect(0, 0, 1920, 1080)
    selection = QRect(120, 80, 640, 480)
    for platform in ("darwin", "win32", "linux"):
        assert _qt_capture_rect(selection, screen, platform) == selection


def test_windows_right_hand_screen_uses_local_coordinates():
    screen = QRect(1920, 0, 2560, 1440)
    selection = QRect(2000, 120, 800, 600)
    assert _qt_capture_rect(selection, screen, "win32") == QRect(80, 120, 800, 600)


def test_windows_left_hand_screen_handles_negative_global_coordinates():
    screen = QRect(-2560, -200, 2560, 1440)
    selection = QRect(-2480, -80, 900, 700)
    assert _qt_capture_rect(selection, screen, "win32") == QRect(80, 120, 900, 700)


def test_windows_screen_above_primary_uses_local_coordinates():
    screen = QRect(300, -1440, 2560, 1440)
    selection = QRect(450, -1320, 700, 500)
    assert _qt_capture_rect(selection, screen, "win32") == QRect(150, 120, 700, 500)


def test_macos_secondary_screen_keeps_global_coordinates():
    for screen, selection in (
        (QRect(1920, 0, 2560, 1440), QRect(2000, 120, 800, 600)),
        (QRect(-2560, -200, 2560, 1440), QRect(-2480, -80, 900, 700)),
    ):
        assert _qt_capture_rect(selection, screen, "darwin") == selection


# ---------- 混合 DPI 下的屏幕配对 ----------
#
# 这些用例直接构造 mss 的 monitors 数据，不需要真的接多块不同缩放的显示器。
# Windows 上主屏 150%、副屏 100% 是极常见的组合，而这正是老算法出错的场景。


def _monitors(*boxes):
    """按 mss 的格式造 monitors 列表，第 0 个是整个虚拟桌面。"""
    left = min(b[0] for b in boxes)
    top = min(b[1] for b in boxes)
    right = max(b[0] + b[2] for b in boxes)
    bottom = max(b[1] + b[3] for b in boxes)
    virtual = {"left": left, "top": top, "width": right - left, "height": bottom - top}
    return [virtual] + [
        {"left": x, "top": y, "width": w, "height": h} for x, y, w, h in boxes
    ]


def test_mixed_dpi_secondary_screen_matches_correctly():
    """主屏 150% + 副屏 100%，副屏必须配到正确的 monitor 和缩放比。

    老算法在这里会失败：它用「Qt 全局坐标 × 本屏缩放比」推算物理位置，
    副屏 Qt 逻辑 x=1280、本屏缩放比 1.0，推出 left=1280，实际是 1920。
    """
    from pinref.overlay import _match_monitor

    # 物理：主屏 1920x1080 @(0,0)，副屏 1920x1080 @(1920,0)
    monitors = _monitors((0, 0, 1920, 1080), (1920, 0, 1920, 1080))

    # 主屏缩放 150% -> Qt 逻辑 1280x720 @(0,0)
    screens = [QRect(0, 0, 1280, 720), QRect(1280, 0, 1920, 1080)]

    primary, primary_scale = _match_monitor(monitors, screens[0], screens)
    assert primary["left"] == 0, f"主屏配错了: {primary}"
    assert abs(primary_scale - 1.5) < 0.01, f"主屏缩放比应为 1.5，得到 {primary_scale}"

    # 副屏缩放 100% -> Qt 逻辑 1920x1080 @(1280,0)
    second, second_scale = _match_monitor(monitors, screens[1], screens)
    assert second["left"] == 1920, f"副屏配错了: {second}"
    assert abs(second_scale - 1.0) < 0.01, f"副屏缩放比应为 1.0，得到 {second_scale}"


def test_mixed_dpi_does_not_fall_back_to_virtual_desktop():
    """配对失败会退回整个虚拟桌面，那样截出来的位置全错，必须避免。"""
    from pinref.overlay import _match_monitor

    monitors = _monitors((0, 0, 3840, 2160), (3840, 0, 1920, 1080))
    screens = [QRect(0, 0, 1920, 1080), QRect(1920, 0, 1920, 1080)]
    virtual = monitors[0]
    for geo in screens:
        matched, _ = _match_monitor(monitors, geo, screens)
        assert matched is not virtual, f"{geo} 退回了虚拟桌面兜底"


def test_scale_is_derived_per_screen_not_shared():
    """每块屏各算各的缩放比，不能共用一个。"""
    from pinref.overlay import _match_monitor

    monitors = _monitors((0, 0, 2560, 1440), (2560, 0, 1920, 1080))
    screens = [QRect(0, 0, 1280, 720), QRect(1280, 0, 1920, 1080)]
    _, high = _match_monitor(monitors, screens[0], screens)  # 200%
    _, low = _match_monitor(monitors, screens[1], screens)  # 100%
    assert abs(high - 2.0) < 0.01, f"200% 那块屏得到 {high}"
    assert abs(low - 1.0) < 0.01, f"100% 那块屏得到 {low}"


def test_macos_style_logical_monitors_still_match():
    """macOS 上 mss 报的是逻辑点，缩放比恒为 1，不能被新逻辑改坏。"""
    from pinref.overlay import _match_monitor

    monitors = _monitors((0, 0, 1512, 982), (-2560, 0, 2560, 1440))
    screens = [QRect(0, 0, 1512, 982), QRect(-2560, 0, 2560, 1440)]
    built_in, scale_a = _match_monitor(monitors, screens[0], screens)
    external, scale_b = _match_monitor(monitors, screens[1], screens)
    assert built_in["width"] == 1512 and abs(scale_a - 1.0) < 0.01
    assert external["width"] == 2560 and abs(scale_b - 1.0) < 0.01


def test_no_candidate_falls_back_safely():
    """尺寸完全对不上时退回虚拟桌面，至少不能崩。"""
    from pinref.overlay import _match_monitor

    monitors = _monitors((0, 0, 1920, 1080))
    geo = QRect(0, 0, 800, 137)
    matched, scale = _match_monitor(monitors, geo, [geo])
    assert matched is monitors[0] and scale == 1.0


def test_size_hint_reports_physical_pixels():
    """选框标签报的是物理像素，也就是截图真正拿到的像素数。

    150% 的 4K 屏框满全屏，逻辑只有 2560x1440，看着像没截到 4K，
    实际截到 3840x2160（TEST.md D-006）。
    """
    assert (_to_physical(2560, 1.5), _to_physical(1440, 1.5)) == (3840, 2160)
    assert (_to_physical(320, 1.0), _to_physical(240, 1.0)) == (320, 240)
    assert (_to_physical(1280, 2.0), _to_physical(800, 2.0)) == (2560, 1600)


def test_size_hint_rounds_half_up_like_qt():
    """.5 要向上进位，跟 Qt 的 qRound 一致，不能用 Python 的银行家舍入。

    副屏 2560x1600 在 150% 下逻辑是 1707x1067，×1.5 正好落在 .5 上。
    内置 round(2560.5) 得 2560，而实测该屏整屏截图是 2561x1601，
    用 round 会让标签比实际少 1 像素。
    """
    assert 1707 * 1.5 == 2560.5, "前提变了：这个用例依赖 1707×1.5 正好落在 .5"
    assert round(1707 * 1.5) == 2560, "Python 的银行家舍入行为变了"
    assert _to_physical(1707, 1.5) == 2561
    assert _to_physical(1067, 1.5) == 1601


def test_mss_origin_rounds_like_qt():
    """mss 后端算抓屏起点的舍入必须和 Qt 一致。

    两个后端各自算起点，若舍入方式不同，同一块区域会落在相差 1 物理像素
    的位置上（TEST.md D-010）。150% 下逻辑 568 处 ×1.5 正好落在 .5 上，
    是最容易暴露差异的坐标。
    """
    assert 568 * 1.5 == 852.0
    assert 569 * 1.5 == 853.5
    assert round(853.5) == 854, "Python 的银行家舍入行为变了"
    # .5 一律进位，和 _draw_size_hint 用的是同一个函数
    assert _to_physical(569, 1.5) == 854
    assert _to_physical(1707, 1.5) == 2561
    # mss 实测出来的 scale 通常不是整齐的 1.5，换算同样不能出现半像素
    assert _to_physical(569, 1.4996192424489492) == 853


def _right_button(kind, overlay, pos=None):
    pos = pos or QPoint(30, 30)
    buttons = Qt.RightButton if kind == QEvent.MouseButtonPress else Qt.NoButton
    return QMouseEvent(
        kind, QPointF(pos), QPointF(pos), Qt.RightButton, buttons, Qt.NoModifier
    )


def test_right_click_cancels_on_release_not_press():
    """右键取消必须等松开才动作。

    按下就取消的话遮罩立刻销毁，右键的「松开」落到下面那个窗口上，
    而多数程序的右键菜单正是在松开时弹出——取消框选会顺手在背后的
    页面点出一个菜单（TEST.md D-011）。整个点击要由遮罩吃掉。
    """
    overlay = _ScreenOverlay(QGuiApplication.primaryScreen())
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(1))

    overlay.mousePressEvent(_right_button(QEvent.MouseButtonPress, overlay))
    assert not cancelled, "右键刚按下就取消了，松开会漏给背后的窗口"

    overlay.mouseReleaseEvent(_right_button(QEvent.MouseButtonRelease, overlay))
    assert len(cancelled) == 1, "松开后应该取消"
    overlay.close()


def test_stray_right_release_does_not_cancel():
    """没在遮罩上按下过的右键松开，不该触发取消。

    例如遮罩弹出前用户已按住右键，松开时遮罩才刚显示——这一下不该算数。
    """
    overlay = _ScreenOverlay(QGuiApplication.primaryScreen())
    cancelled = []
    overlay.cancelled.connect(lambda: cancelled.append(1))

    overlay.mouseReleaseEvent(_right_button(QEvent.MouseButtonRelease, overlay))
    assert not cancelled, "没按下过就取消了"
    overlay.close()


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
