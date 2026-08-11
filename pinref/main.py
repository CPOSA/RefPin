"""程序入口。

运行：python -m pinref.main
流程：起来就进框选状态 → 拖一块区域 → 截图变成置顶浮窗。
关掉所有浮窗，程序退出；框选时按 Esc 直接退出。
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pinref.floating import FloatingImage
from pinref.overlay import ScreenSelector


def _use_accessory_policy() -> bool:
    """macOS：把 app 切成 accessory 类型（无程序坞图标）。

    这是让遮罩和浮窗能出现在**别的 app 的全屏 Space** 上的唯一开关。
    macOS 的全屏 app 独占一个 Space，普通 `.regular` 类型的 app 无论怎么设
    窗口层级（连 CGShieldingWindowLevel 都试过）和 collectionBehavior，
    窗口都不会被合成到那块屏上 —— 实测四种组合全灭。切成 accessory 后，
    什么额外属性都不用设就正常了。

    代价：程序坞里没有图标，Cmd+Tab 也切不到。对这个工具反而合适，
    但意味着阶段 3 的全局快捷键是必需品 —— 否则没法再次唤起截图。

    没装 pyobjc 也能跑，只是遇到有全屏 app 的屏幕会截不了。
    """
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    except ImportError:
        return False
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
    return True


def main() -> int:
    app = QApplication(sys.argv)
    # 要在 QApplication 之后调：NSApplication 实例由它创建
    _use_accessory_policy()
    # 遮罩关掉、浮窗还没建出来的那一瞬间，窗口数是 0。
    # 不关掉这个默认行为，程序会在那一刻自己退出。
    app.setQuitOnLastWindowClosed(False)

    pins: list[FloatingImage] = []

    def on_captured(pixmap: QPixmap, top_left: QPoint):
        pin = FloatingImage(pixmap, top_left)
        pin.closed.connect(lambda: on_pin_closed(pin))
        pins.append(pin)
        pin.show()
        pin.raise_()
        pin.activateWindow()

    def on_pin_closed(pin: FloatingImage):
        if pin in pins:
            pins.remove(pin)
        if not pins:
            app.quit()

    selector = ScreenSelector()
    selector.captured.connect(on_captured)
    selector.cancelled.connect(app.quit)
    selector.start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
