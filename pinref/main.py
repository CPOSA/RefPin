"""程序入口。

运行：python -m pinref.main
流程：起来就进框选状态 → 拖一块区域 → 截图变成置顶浮窗。
关掉所有浮窗，程序退出；框选时按 Esc 直接退出。
"""

from __future__ import annotations

import contextlib
import sys
import threading

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from pinref.overlay import ScreenSelector


def _preload_imaging():
    """在后台把 pinref.floating 那条链导进来。

    floating → imaging → numpy 要 150 ms 上下，但框选阶段一个都用不到。
    放在模块顶部导，等的是启动；挪到截图那一刻导，等的是松开鼠标之后——
    两头都难受。所以遮罩一显示就在后台线程里预热，用户拖框那几百毫秒
    足够导完，等真要建浮窗时通常已经就绪（见 TEST.md D-007）。

    线程里只做 import，不碰任何 Qt 对象，符合"GUI 对象只在主线程动"这条约束。
    import 本身有锁，和主线程里的 import 撞上也只是等它导完，不会重复执行。
    """
    # 预热失败不该影响主流程：真正要用时会在主线程再导一次，那时该报的错照报
    with contextlib.suppress(Exception):
        import pinref.floating  # noqa: F401


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

    pins: list = []

    def on_captured(pixmap: QPixmap, top_left: QPoint):
        # 通常预热线程已经导完，这里直接拿到缓存好的模块
        from pinref.floating import FloatingImage

        pin = FloatingImage(pixmap, top_left)
        pin.closed.connect(lambda: on_pin_closed(pin))
        pins.append(pin)
        pin.show()
        pin.raise_()
        pin.activateWindow()

    def on_pin_closed(pin):
        if pin in pins:
            pins.remove(pin)
        if not pins:
            app.quit()

    selector = ScreenSelector()
    selector.captured.connect(on_captured)
    selector.cancelled.connect(app.quit)
    selector.start()

    # 遮罩已经在屏幕上了，趁用户拖框的这段时间把 numpy 那条链导进来
    threading.Thread(target=_preload_imaging, daemon=True).start()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
