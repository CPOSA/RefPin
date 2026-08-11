"""跨平台自检：一条命令跑完环境、屏幕、坐标、抓屏、性能和自动化测试。

    python tools/platform_check.py

Mac 和 Windows 上各跑一次，把两边输出对比着看，差异就是平台问题。
过程中会在每块屏幕上短暂闪出一个小色块（约 1.5 秒），那是在验证
窗口会不会自己跑到别的屏幕上，属正常。

看不懂的地方直接把整段输出发出来即可。
"""

from __future__ import annotations

import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Windows 终端默认不是 UTF-8，中文会乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

results: list[tuple[str, str, str]] = []  # (状态, 项目, 说明)


def record(status: str, item: str, detail: str = ""):
    results.append((status, item, detail))


def title(text: str):
    print(f"\n{'=' * 62}\n{text}\n{'=' * 62}")


# ---------------------------------------------------------------- 1. 环境


def check_environment():
    title("1. 环境")
    print(f"  操作系统      {platform.platform()}")
    print(f"  架构          {platform.machine()}")
    print(f"  Python        {sys.version.split()[0]}  ({sys.executable})")
    print(f"  CPU 逻辑核心  {__import__('os').cpu_count()}")

    missing = []
    for name in ("PySide6", "numpy", "PIL", "mss"):
        try:
            module = __import__(name)
            version = getattr(module, "__version__", "?")
            print(f"  {name:<13} {version}")
        except ImportError as exc:
            print(f"  {name:<13} 缺失！{exc}")
            missing.append(name)

    try:
        import AppKit  # noqa: F401

        on_mac = sys.platform == "darwin"
        print(f"  pyobjc        已安装{'' if on_mac else '（Windows 上本不该装）'}")
        record("OK" if on_mac else "注意", "pyobjc 安装情况",
               "" if on_mac else "非 macOS 却装上了，检查 requirements 的平台标记")
    except ImportError:
        if sys.platform == "darwin":
            print("  pyobjc        缺失！macOS 上需要它才能浮在全屏 app 之上")
            record("失败", "pyobjc 安装情况", "macOS 上缺失，全屏 app 场景会失效")
        else:
            print("  pyobjc        未安装（正确，平台标记生效了）")
            record("OK", "pyobjc 安装情况", "非 macOS 已正确跳过")

    record("失败" if missing else "OK", "依赖完整性",
           f"缺少 {missing}" if missing else "")


# ---------------------------------------------------------------- 2. 屏幕


def check_screens(app):
    title("2. 屏幕拓扑")
    from PySide6.QtGui import QGuiApplication
    import mss

    print("  Qt 看到的（逻辑坐标）：")
    for screen in QGuiApplication.screens():
        geo, avail = screen.geometry(), screen.availableGeometry()
        print(f"    {screen.name()}")
        print(f"      geometry  {geo.width()}x{geo.height()} @({geo.x()},{geo.y()})"
              f"   dpr={screen.devicePixelRatio()}"
              f"   {'← 主屏' if screen is QGuiApplication.primaryScreen() else ''}")
        print(f"      available {avail.width()}x{avail.height()} @({avail.x()},{avail.y()})"
              f"   （减掉的是任务栏/菜单栏/程序坞）")

    with mss.MSS() as sct:
        monitors = sct.monitors
    print("\n  mss 看到的：")
    for i, mon in enumerate(monitors):
        tag = "虚拟桌面" if i == 0 else f"#{i}"
        print(f"    {tag:<10} {mon['width']}x{mon['height']} @({mon['left']},{mon['top']})")

    print("\n  两者配对（缩放比 = mss 尺寸 ÷ Qt 尺寸，运行时实测，不依赖平台假设）：")
    from pinref.overlay import _match_monitor

    bad = []
    for screen in QGuiApplication.screens():
        mon, scale = _match_monitor(monitors, screen.geometry())
        fell_back = mon is monitors[0] and len(monitors) > 2
        note = "配对失败，退回虚拟桌面" if fell_back else ""
        print(f"    {screen.name():<26} Qt_dpr={screen.devicePixelRatio():<4} "
              f"实测scale={scale:<6.3f} {note}")
        if fell_back:
            bad.append(screen.name())
    record("失败" if bad else "OK", "屏幕配对", f"配对失败: {bad}" if bad else "")


# ---------------------------------------------------------------- 3. 坐标换算


def check_coordinate_mapping(app):
    title("3. grabWindow 坐标换算")
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from pinref.overlay import _qt_capture_rect

    print("  macOS 用虚拟桌面全局坐标，Windows / X11 用屏幕局部坐标。")
    print("  主屏从 (0,0) 开始会掩盖这个差异，所以副屏才是关键。\n")
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        selection = QRect(geo.x() + 100, geo.y() + 80, 300, 200)
        mapped = _qt_capture_rect(selection, geo)
        print(f"    {screen.name():<26} 全局选区({selection.x()},{selection.y()}) "
              f"-> 传给 grabWindow 的是 ({mapped.x()},{mapped.y()})")
    record("需人工确认", "坐标换算", "看第 4 节的截图内容是否落在预期位置")


# ---------------------------------------------------------------- 4. 抓屏


def check_capture(app):
    title("4. 抓屏（两个后端对比）")
    import numpy as np
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from pinref import overlay
    from pinref.imaging import pixmap_to_rgb

    original = overlay.CAPTURE_BACKEND
    failures = []
    try:
        for screen in QGuiApplication.screens():
            geo = screen.geometry()
            rect = QRect(geo.x() + geo.width() // 3, geo.y() + geo.height() // 3, 320, 240)
            print(f"\n    {screen.name()}  取全局区域 320x240 @({rect.x()},{rect.y()})")

            shots = {}
            for backend in ("qt", "mss"):
                overlay.CAPTURE_BACKEND = backend
                try:
                    pixmap = overlay.grab_screen_rect(rect)
                except Exception as exc:  # noqa: BLE001
                    print(f"      {backend:<4} 抓屏抛异常: {exc}")
                    failures.append(f"{screen.name()}/{backend}")
                    continue
                logical = pixmap.deviceIndependentSize()
                array = pixmap_to_rgb(pixmap)
                colours = len(np.unique(array.reshape(-1, 3), axis=0))
                shots[backend] = array
                size_ok = abs(logical.width() - 320) < 2 and abs(logical.height() - 240) < 2
                print(f"      {backend:<4} 像素 {pixmap.width()}x{pixmap.height()}"
                      f"  dpr={pixmap.devicePixelRatio()}"
                      f"  逻辑 {logical.width():.0f}x{logical.height():.0f}"
                      f"  {'尺寸OK' if size_ok else '尺寸不对!'}"
                      f"  取样到 {colours} 种颜色"
                      f"  {'' if colours > 3 else '← 画面接近纯色，可能没抓到真实内容'}")
                if not size_ok:
                    failures.append(f"{screen.name()}/{backend} 尺寸")

            if len(shots) == 2:
                a, b = shots["qt"], shots["mss"]
                side = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
                diff = np.abs(a[: side[0], : side[1]].astype(int)
                              - b[: side[0], : side[1]].astype(int)).mean()
                same = diff < 25
                print(f"      两个后端内容差异 {diff:.1f}/255  "
                      f"{'一致，说明截的是同一块区域' if same else '差异大！很可能有一个截偏了'}")
                if not same:
                    failures.append(f"{screen.name()} 两后端不一致")
    finally:
        overlay.CAPTURE_BACKEND = original

    record("失败" if failures else "OK", "抓屏", f"{failures}" if failures else "")


# ---------------------------------------------------------------- 5. 窗口行为


def check_windows(app):
    title("5. 窗口不会自己跑到别的屏幕")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtGui import QGuiApplication, QImage, QPixmap
    from PySide6.QtWidgets import QApplication
    import numpy as np
    from pinref.floating import FloatingImage

    print("  每块屏上放一个小色块，等 1.6 秒后看它还在不在原地。")
    print("  （macOS 上 Qt.Tool 窗口会在约 0.4 秒后被系统挪走，所以必须等够时间）\n")

    arr = np.zeros((150, 200, 4), dtype=np.uint8)
    arr[:, :, 2] = 240
    arr[:, :, 3] = 255
    image = QImage(arr.data, 200, 150, 200 * 4, QImage.Format_RGB32).copy()

    pins = []
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        target = QPoint(geo.x() + 120, geo.y() + 120)
        pin = FloatingImage(QPixmap.fromImage(image), target)
        pin.show()
        pins.append((screen, pin, target))

    deadline = time.time() + 1.6
    while time.time() < deadline:
        QApplication.processEvents()
        time.sleep(0.02)

    drifted = []
    for screen, pin, target in pins:
        actual = pin.pos()
        landed = QGuiApplication.screenAt(pin.geometry().center())
        ok = actual == target and landed is not None and landed.name() == screen.name()
        print(f"    {screen.name():<26} 期望({target.x()},{target.y()}) "
              f"实际({actual.x()},{actual.y()}) 落在 {landed.name() if landed else '屏幕外'}"
              f"  {'OK' if ok else '← 跑偏了'}")
        if not ok:
            drifted.append(screen.name())
        flags = pin.windowFlags()
        pin.close()

    kind = flags & Qt.WindowType_Mask
    print(f"\n    窗口类型 {kind!r}   置顶 {bool(flags & Qt.WindowStaysOnTopHint)}"
          f"   无边框 {bool(flags & Qt.FramelessWindowHint)}")
    if sys.platform.startswith("win"):
        print("    注意：Windows 上 Qt.Window 会在任务栏和 Alt+Tab 里各占一个位置。")
        print("          这是为修 macOS 的 Space 问题引入的，Windows 并不需要。")
        print("          请人工判断这个困扰程度，可改成 Qt.Tool。")
        record("需人工确认", "任务栏 / Alt+Tab", "看浮窗和面板是否造成困扰")

    record("失败" if drifted else "OK", "窗口留在指定屏幕",
           f"跑偏: {drifted}" if drifted else "")


# ---------------------------------------------------------------- 6. 性能


def check_performance(app):
    title("6. 调色性能")
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QGuiApplication
    from pinref import imaging
    from pinref.overlay import grab_screen_rect

    print(f"  线程池大小 {imaging._WORKERS}（分块并行，numpy 的 ufunc 会释放 GIL）\n")

    def verdict(ms: float) -> str:
        if ms < 16.7:
            return "60fps+ 完全跟手"
        if ms < 33.3:
            return "30~60fps 正常拖动看不出"
        if ms < 50:
            return "20~30fps 快速拖有拖影"
        return "20fps 以下，明显滞后"

    slow = []
    for screen in QGuiApplication.screens():
        geo = screen.geometry()
        pixmap = grab_screen_rect(QRect(geo.x(), geo.y(), geo.width(), geo.height()))
        pixels = pixmap.width() * pixmap.height()

        start = time.perf_counter()
        adjuster = imaging.Adjuster(pixmap)
        init_ms = (time.perf_counter() - start) * 1000

        def measure(gray, hue, rounds=12):
            adjuster.apply(gray=gray, hue_shift=hue)
            start = time.perf_counter()
            for i in range(rounds):
                adjuster.apply(gray=gray, hue_shift=hue + i)
            return (time.perf_counter() - start) * 1000 / rounds

        hue_ms = measure(0.0, 30.0)
        both_ms = measure(0.5, 30.0)
        gray_ms = measure(1.0, 30.0)

        print(f"    {screen.name()}  整屏截图 {pixmap.width()}x{pixmap.height()} "
              f"({pixels / 1e6:.1f}M 像素)")
        print(f"      首次载入        {init_ms:7.1f} ms  （钉图时一次性开销）")
        print(f"      只拖色相        {hue_ms:7.1f} ms/帧  {verdict(hue_ms)}")
        print(f"      色相+灰度       {both_ms:7.1f} ms/帧  {verdict(both_ms)}")
        print(f"      灰度拉满(捷径)  {gray_ms:7.1f} ms/帧  {verdict(gray_ms)}")

        # 单线程对照，看多线程在这台机器上到底提速多少
        saved = imaging._WORKERS
        try:
            imaging._WORKERS = 1
            single_ms = measure(0.5, 30.0)
        finally:
            imaging._WORKERS = saved
        print(f"      单线程对照      {single_ms:7.1f} ms/帧  "
              f"→ 多线程提速 {single_ms / both_ms:.1f}x")

        if both_ms > 33.3:
            slow.append(f"{screen.name()} {both_ms:.0f}ms")

    record("注意" if slow else "OK", "调色性能",
           f"偏慢: {slow}" if slow else "")


# ---------------------------------------------------------------- 7. 自动化测试


def run_test_suites():
    title("7. 自动化测试")
    failed = []
    for name in ("test_imaging.py", "test_floating.py", "test_overlay.py"):
        path = ROOT / "tests" / name
        if not path.exists():
            print(f"  {name:<20} 文件不存在，跳过")
            continue
        proc = subprocess.run(
            [sys.executable, str(path)],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        tail = [line for line in proc.stdout.splitlines() if "通过" in line]
        summary = tail[-1].strip() if tail else "（无输出）"
        status = "OK" if proc.returncode == 0 else "失败"
        print(f"  {name:<20} {status}  {summary}")
        if proc.returncode != 0:
            failed.append(name)
            for line in proc.stdout.splitlines():
                if "FAIL" in line:
                    print(f"      {line.strip()}")
            if proc.stderr.strip():
                print(f"      stderr: {proc.stderr.strip()[:400]}")
    record("失败" if failed else "OK", "自动化测试",
           f"失败: {failed}" if failed else "")


# ---------------------------------------------------------------- 汇总


def summarise():
    title("汇总")
    width = max(len(item) for _, item, _ in results) + 2
    for status, item, detail in results:
        mark = {"OK": "  OK  ", "失败": " 失败 ", "注意": " 注意 ", "需人工确认": "人工确认"}[status]
        print(f"  [{mark}] {item:<{width}} {detail}")

    failures = [i for s, i, _ in results if s == "失败"]
    print()
    if failures:
        print(f"  有 {len(failures)} 项失败：{failures}")
        print("  把上面完整输出发出来即可。")
    else:
        print("  自动可测的部分全部通过。")
        print("  剩下要人工确认的（脚本测不了）：")
        print("    - 遮罩能否盖住任务栏 / 菜单栏")
        print("    - 置顶是否压得住绘画软件的最大化窗口")
        print("    - 面板圆角透明背景显示是否正常")
        print("    - 拖滑块的实际手感")
        print("    - 详细清单见 TEST.md")
    return 1 if failures else 0


def main() -> int:
    print(f"PinRef 平台自检   {time.strftime('%Y-%m-%d %H:%M:%S')}")
    check_environment()

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    # macOS 上切成 accessory，行为才和真实运行一致
    try:
        from pinref.main import _use_accessory_policy

        _use_accessory_policy()
    except Exception:  # noqa: BLE001
        pass

    check_screens(app)
    check_coordinate_mapping(app)
    check_capture(app)
    check_windows(app)
    check_performance(app)
    run_test_suites()
    return summarise()


if __name__ == "__main__":
    sys.exit(main())
