"""截图坐标换算的跨平台测试。

这些测试不需要真的连接多块显示器，而是用 QRect 模拟 Windows / macOS
虚拟桌面布局，防止只在开发机主屏上正常、换到副屏就截偏。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QRect  # noqa: E402

from pinref.overlay import _qt_capture_rect  # noqa: E402


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
