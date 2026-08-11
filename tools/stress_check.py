"""TEST.md 第 9 节的压力测试：连续调色、反复开关面板、反复建关浮窗。

    python tools/stress_check.py           # 每种分辨率连续调色 30 秒
    python tools/stress_check.py 5         # 改成 5 秒，先跑通流程用

手点 100 次面板既折磨人又不可靠，而"内存有没有持续增长"靠肉眼更是看不出来，
所以这几项交给脚本：驱动的是真实的浮窗和面板对象，同时按固定间隔采样进程内存。

过程中会有浮窗在屏幕上闪现，属正常。
"""

from __future__ import annotations

import gc
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

results: list[tuple[str, str, str]] = []


def record(status: str, item: str, detail: str = ""):
    results.append((status, item, detail))


def title(text: str):
    print(f"\n{'=' * 62}\n{text}\n{'=' * 62}")


# ---------------------------------------------------------------- 内存


def _rss_mb() -> float:
    """当前进程占用的物理内存（MB）。

    刻意不引入 psutil：项目运行时依赖里没有它，为一个自检脚本加一条依赖不值当。
    Windows 走 PSAPI，其余平台退回 resource——注意后者给的是峰值而非当前值，
    只能看出"涨到过多少"，看不出回落，判断泄漏时要留意这个差别。
    """
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        # 必须显式声明类型：GetCurrentProcess 返回的是句柄，ctypes 默认按
        # c_int 处理，64 位下会被截断，调用直接失败返回 0
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        get_info = ctypes.windll.psapi.GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        get_info.restype = wintypes.BOOL

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not get_info(get_process(), ctypes.byref(counters), counters.cb):
            raise OSError("GetProcessMemoryInfo 调用失败")
        return counters.WorkingSetSize / 1024 / 1024

    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS 给字节，Linux 给 KB
    return peak / 1024 / 1024 if sys.platform == "darwin" else peak / 1024


def _rss_is_current() -> bool:
    """当前平台的 _rss_mb 报的是实时值还是历史峰值。"""
    return sys.platform == "win32"


# ---------------------------------------------------------------- 工具


def pump(app, ms: int = 0):
    """把积压的事件处理掉，让 deleteLater 之类真的执行。"""
    from PySide6.QtCore import QEventLoop, QTimer

    if ms:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()
    else:
        app.processEvents(QEventLoop.AllEvents, 50)


def make_pixmap(width: int, height: int):
    """造一张噪声图。

    用随机像素而不是纯色：纯色图的 HSV 缓存和调色结果都过于规整，
    压不出真实负载，测出来的耗时会偏乐观。
    """
    import numpy as np
    from PySide6.QtGui import QImage, QPixmap

    rng = np.random.default_rng(0)
    arr = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    arr[:, :, 3] = 255
    image = QImage(arr.data, width, height, width * 4, QImage.Format_RGB32).copy()
    return QPixmap.fromImage(image)


# ---------------------------------------------------- 1. 连续调色


# 首段/末段各要够这么多帧，比值才有意义
MIN_QUARTER = 8

SIZES = [
    (800, 600),
    (1920, 1080),
    (2560, 1440),
    (3024, 1964),  # Retina 尺寸
    (3840, 2160),
]


def check_sustained_adjust(app, seconds: float):
    title(f"1. 连续调色 {seconds:g} 秒")
    from PySide6.QtCore import QPoint

    from pinref.floating import FloatingImage

    print(
        f"  {'尺寸':<14}{'帧数':>7}{'平均':>10}{'最慢':>10}{'末段/首段':>12}{'内存增量':>12}"
    )
    slow: list[str] = []
    thin: list[str] = []
    for width, height in SIZES:
        pin = FloatingImage(make_pixmap(width, height), QPoint(40, 40))
        pin.show()
        pin.toggle_panel()
        panel = pin._panel
        pump(app)

        gc.collect()
        rss_before = _rss_mb()

        frames: list[float] = []
        deadline = time.perf_counter() + seconds
        step = 0
        while time.perf_counter() < deadline:
            # 每帧都换一个色相，避免命中"值没变就不重算"的捷径
            step += 1
            start = time.perf_counter()
            panel._hue.slider.setValue(-180 + (step * 7) % 361)
            frames.append((time.perf_counter() - start) * 1000)
            pump(app)

        rss_after = _rss_mb()
        pin.close()
        del pin, panel
        gc.collect()
        pump(app, 30)

        if not frames:
            continue

        # 首段/末段各取四分之一。样本太少时这个比值全是噪声——大图跑 2 秒
        # 只有十来帧，首末各 2 帧，比值能飘到 1.5 以上，不能据此判劣化。
        quarter = len(frames) // 4
        if quarter >= MIN_QUARTER:
            head = sum(frames[:quarter]) / quarter
            tail = sum(frames[-quarter:]) / quarter
            drift = tail / head if head else 1.0
            drift_text = f"{drift:.2f}x"
        else:
            drift = None
            drift_text = "样本不足"

        print(
            f"  {f'{width}x{height}':<14}{len(frames):>7}"
            f"{sum(frames) / len(frames):>9.1f}ms{max(frames):>9.1f}ms"
            f"{drift_text:>12}{rss_after - rss_before:>10.1f}MB"
        )
        # 末段明显慢于首段，说明有东西在累积
        if drift is not None and drift > 1.5:
            slow.append(f"{width}x{height} 末段慢 {drift:.2f}x")
        elif drift is None:
            thin.append(f"{width}x{height} 仅 {len(frames)} 帧")

    if slow:
        record("失败", "连续调色不劣化", f"{slow}")
    elif thin:
        record("注意", "连续调色不劣化", f"部分尺寸样本不足，未判定: {thin}")
    else:
        record("OK", "连续调色不劣化")


# ---------------------------------------------------- 2. 开关面板


def check_panel_cycling(app, rounds: int = 100):
    title(f"2. 连续开关控制面板 {rounds} 次")
    from PySide6.QtCore import QPoint

    from pinref.floating import FloatingImage

    pin = FloatingImage(make_pixmap(1920, 1080), QPoint(40, 40))
    pin.show()
    pump(app)

    gc.collect()
    baseline = _rss_mb()
    samples = []

    for i in range(1, rounds + 1):
        pin.toggle_panel()  # 开
        pump(app)
        pin.toggle_panel()  # 关
        pump(app)
        if i % 20 == 0:
            gc.collect()
            samples.append((i, _rss_mb()))

    print(f"  起始内存 {baseline:.1f} MB")
    for i, rss in samples:
        print(f"    第 {i:>3} 次后  {rss:8.1f} MB  ({rss - baseline:+.1f})")

    # 开关面板不该留下任何东西：结束时面板必须是关着的
    leftover = pin._panel is not None
    growth = samples[-1][1] - baseline if samples else 0.0
    pin.close()
    del pin
    gc.collect()
    pump(app, 30)

    if leftover:
        record("失败", f"开关面板 {rounds} 次", "结束时面板对象没有释放")
    elif growth > 30:
        record("注意", f"开关面板 {rounds} 次", f"内存增长 {growth:.1f} MB")
    else:
        record("OK", f"开关面板 {rounds} 次", f"内存增长 {growth:.1f} MB")


# ---------------------------------------------------- 3. 建关浮窗


def check_pin_cycling(app, rounds: int = 100):
    title(f"3. 连续创建并关闭浮窗 {rounds} 次")
    from PySide6.QtCore import QPoint

    from pinref.floating import FloatingImage

    # 固定一张 pixmap 反复用：要测的是浮窗和 Adjuster 的生命周期，
    # 每次重造图会把造图本身的内存波动混进来，读数就不干净了
    pixmap = make_pixmap(1920, 1080)

    # 先跑几次让缓存和线程池就位，否则前几次的增长会被误当成泄漏
    for _ in range(3):
        warmup = FloatingImage(pixmap, QPoint(40, 40))
        warmup.show()
        warmup.toggle_panel()
        pump(app)
        warmup.close()
        del warmup
    gc.collect()
    pump(app, 50)

    baseline = _rss_mb()
    samples = []

    for i in range(1, rounds + 1):
        pin = FloatingImage(pixmap, QPoint(40, 40))
        pin.show()
        pin.toggle_panel()
        pin._panel._gray.slider.setValue(50)
        pin._panel._hue.slider.setValue(90)
        pump(app)
        pin.close()
        del pin
        if i % 20 == 0:
            gc.collect()
            pump(app, 20)
            samples.append((i, _rss_mb()))

    print(
        f"  起始内存 {baseline:.1f} MB" + ("" if _rss_is_current() else "（峰值口径）")
    )
    for i, rss in samples:
        print(f"    第 {i:>3} 次后  {rss:8.1f} MB  ({rss - baseline:+.1f})")

    if not samples:
        record("失败", f"建关浮窗 {rounds} 次", "没有采到内存样本")
        return

    growth = samples[-1][1] - baseline
    # 只看总增长会把预热残留算进去，所以同时看后半程还在不在涨
    latter = samples[-1][1] - samples[len(samples) // 2][1]
    print(f"\n  总增长 {growth:+.1f} MB，后半程增长 {latter:+.1f} MB")
    if latter > 20:
        record(
            "失败", f"建关浮窗 {rounds} 次", f"后半程仍在涨 {latter:+.1f} MB，疑似泄漏"
        )
    elif growth > 50:
        record(
            "注意", f"建关浮窗 {rounds} 次", f"总增长 {growth:+.1f} MB，后半程已趋平"
        )
    else:
        record("OK", f"建关浮窗 {rounds} 次", f"总增长 {growth:+.1f} MB")


# ---------------------------------------------------------------- main


def main() -> int:
    seconds = 30.0
    if len(sys.argv) > 1:
        seconds = float(sys.argv[1])

    print(f"PinRef 压力测试   {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  {platform.platform()}   Python {sys.version.split()[0]}")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    check_sustained_adjust(app, seconds)
    check_panel_cycling(app)
    check_pin_cycling(app)

    title("汇总")
    failed = [item for status, item, _ in results if status == "失败"]
    for status, item, detail in results:
        print(f"  [{status:^4}] {item:<24}{detail}")
    if failed:
        print(f"\n  有 {len(failed)} 项失败：{failed}")
    else:
        print("\n  全部通过。")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
