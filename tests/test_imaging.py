"""imaging.py 的测试。

跑法（两种都行）：
    python tests/test_imaging.py     # 不需要额外装东西
    pytest tests/                    # 装了 pytest 的话

覆盖：颜色往返、已知颜色、灰度边界、色相环绕、DPR、色彩空间。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtGui import QColorSpace, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from pinref.imaging import (  # noqa: E402
    LUMA,
    Adjuster,
    hsv_to_rgb,
    pixmap_to_rgb,
    rgb_to_hsv,
    rgb_to_pixmap,
)

# QPixmap 必须在 QApplication 之后才能建
_app = QApplication.instance() or QApplication([])


# ---------- 小工具 ----------


def make_pixmap(rgb: np.ndarray, ratio: float = 1.0, color_space=None) -> QPixmap:
    """用 (高,宽,3) 的 RGB 数组造一张 QPixmap。"""
    height, width = rgb.shape[:2]
    bgra = np.empty((height, width, 4), dtype=np.uint8)
    bgra[:, :, :3] = rgb[:, :, ::-1]
    bgra[:, :, 3] = 255
    image = QImage(bgra.data, width, height, width * 4, QImage.Format_RGB32).copy()
    if color_space is not None:
        image.setColorSpace(color_space)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


def solid(rgb: tuple[int, int, int], size: int = 4, **kwargs) -> QPixmap:
    """一张纯色小图。"""
    arr = np.zeros((size, size, 3), dtype=np.uint8)
    arr[:, :] = rgb
    return make_pixmap(arr, **kwargs)


def first_pixel(pixmap: QPixmap) -> tuple[int, int, int]:
    return tuple(int(v) for v in pixmap_to_rgb(pixmap)[0, 0])


# ---------- 1. 颜色往返 ----------


def test_pixmap_roundtrip_is_lossless():
    """QPixmap ↔ numpy 往返不能丢任何一个像素值。

    这条是所有调色运算的地基：地基有损，后面测什么都不作数。
    """
    rng = np.random.default_rng(0)
    src = rng.integers(0, 256, (37, 53, 3), dtype=np.uint8)  # 故意用非整倍宽度
    back = pixmap_to_rgb(rgb_to_pixmap(src))
    assert np.array_equal(back, src), "QPixmap 往返有损"


def test_roundtrip_handles_odd_widths():
    """宽度不是 4 的倍数时，QImage 每行会有 padding，裁切不能裁错。"""
    for width in (1, 2, 3, 5, 7, 13, 101):
        src = np.random.default_rng(width).integers(
            0, 256, (3, width, 3), dtype=np.uint8
        )
        assert np.array_equal(pixmap_to_rgb(rgb_to_pixmap(src)), src), (
            f"宽度 {width} 出错"
        )


def test_hsv_roundtrip_is_lossless():
    """RGB → HSV → RGB 必须还原。"""
    rng = np.random.default_rng(1)
    rgb01 = rng.random((64, 64, 3), dtype=np.float32)
    hue, saturation, value = rgb_to_hsv(rgb01)
    assert np.abs(hsv_to_rgb(hue, saturation, value) - rgb01).max() < 1e-4


def test_hsv_roundtrip_on_extremes():
    """纯黑、纯白、纯灰、三原色这些边界值最容易出问题（除零、分支）。"""
    edge = np.array(
        [
            [
                [0, 0, 0],
                [255, 255, 255],
                [128, 128, 128],
                [255, 0, 0],
                [0, 255, 0],
                [0, 0, 255],
                [255, 255, 0],
                [0, 255, 255],
                [255, 0, 255],
            ]
        ],
        dtype=np.uint8,
    )
    rgb01 = edge.astype(np.float32) / 255.0
    hue, saturation, value = rgb_to_hsv(rgb01)
    assert np.isfinite(hue).all(), "色相出现 NaN/Inf"
    assert np.abs(hsv_to_rgb(hue, saturation, value) - rgb01).max() < 1e-4


# ---------- 2. 已知颜色 ----------


def test_known_hue_rotations():
    """色环上间隔 120° 的三原色应该互相精确转换。"""
    cases = [
        ((255, 0, 0), 120, (0, 255, 0)),
        ((255, 0, 0), -120, (0, 0, 255)),
        ((0, 255, 0), 120, (0, 0, 255)),
        ((0, 255, 0), -120, (255, 0, 0)),
        ((0, 0, 255), 120, (255, 0, 0)),
        ((255, 0, 0), 180, (0, 255, 255)),
        ((0, 255, 255), 180, (255, 0, 0)),
    ]
    for source, shift, expected in cases:
        got = first_pixel(Adjuster(solid(source)).apply(hue_shift=shift))
        assert max(abs(a - b) for a, b in zip(got, expected)) <= 2, (
            f"{source} 转 {shift}° 得到 {got}，期望 {expected}"
        )


def test_neutral_colors_ignore_hue():
    """黑白灰没有色相，怎么转都不该变色。"""
    for neutral in ((0, 0, 0), (255, 255, 255), (128, 128, 128)):
        for shift in (30, 90, 180, -75):
            got = first_pixel(Adjuster(solid(neutral)).apply(hue_shift=shift))
            assert max(abs(a - b) for a, b in zip(got, neutral)) <= 1, (
                f"{neutral} 转 {shift}° 变成了 {got}"
            )


def test_zero_adjustment_returns_original_object():
    """两个滑块都没动时应当直接返回原图，不走一遍运算。"""
    pixmap = solid((123, 45, 67))
    adjuster = Adjuster(pixmap)
    assert adjuster.apply(gray=0.0, hue_shift=0.0) is pixmap


# ---------- 3. 灰度边界 ----------


def test_gray_zero_keeps_original_color():
    for color in ((200, 100, 50), (0, 0, 0), (255, 255, 255), (17, 200, 99)):
        assert (
            first_pixel(Adjuster(solid(color)).apply(gray=0.0, hue_shift=1e-9)) == color
        )


def test_gray_full_matches_luma_weights():
    """灰度 100% 时三通道必须相等，且等于 BT.601 加权亮度。"""
    for color in ((200, 100, 50), (255, 0, 0), (0, 255, 0), (0, 0, 255), (12, 200, 77)):
        got = first_pixel(Adjuster(solid(color)).apply(gray=1.0))
        assert got[0] == got[1] == got[2], f"{color} 灰度化后三通道不等: {got}"
        expected = (
            float(np.dot(np.array(color, dtype=np.float32) / 255.0, LUMA)) * 255.0
        )
        assert abs(got[0] - expected) <= 1.5, (
            f"{color} 亮度 {got[0]}，期望约 {expected:.1f}"
        )


def test_gray_half_is_midpoint():
    """50% 应当正好落在原色和纯灰的中间。"""
    color = (200, 100, 50)
    adjuster = Adjuster(solid(color))
    full = first_pixel(adjuster.apply(gray=1.0))[0]
    half = first_pixel(adjuster.apply(gray=0.5))
    for channel, original in zip(half, color):
        assert abs(channel - (original + full) / 2.0) <= 1.5, (
            f"50% 灰度不在中点: {half}"
        )


def test_gray_is_monotonic():
    """灰度从 0 拉到 1，颜色应当单调地往灰色靠，不能来回跳。"""
    adjuster = Adjuster(solid((220, 40, 90)))
    spreads = []
    for step in range(0, 11):
        pixel = first_pixel(adjuster.apply(gray=step / 10.0))
        spreads.append(max(pixel) - min(pixel))
    for earlier, later in zip(spreads, spreads[1:]):
        assert later <= earlier + 1, f"色差没有单调收窄: {spreads}"
    assert spreads[-1] <= 1, f"拉满后仍有色差: {spreads[-1]}"


def test_hue_cannot_change_brightness_at_full_gray():
    """灰度拉满时，转色相不能改变明暗 —— 这是这个工具存在的理由。

    看的是「原图」的素描关系。如果亮度跟着色相走，看到的就是一张
    并不存在的图的明暗，会误导判断。改之前纯红转纯绿明度会从 76 跳到 150。
    """
    for color in (
        (255, 0, 0),
        (0, 0, 255),
        (230, 120, 40),
        (60, 180, 190),
        (235, 180, 150),
    ):
        adjuster = Adjuster(solid(color))
        values = [
            first_pixel(adjuster.apply(gray=1.0, hue_shift=s))[0]
            for s in (0, 45, 60, 120, 180, -75, -120, -180)
        ]
        assert max(values) - min(values) <= 1, f"{color} 全灰下明度随色相波动: {values}"


def test_full_gray_always_equals_original_luma():
    """无论色相转到哪，灰度 100% 都必须等于原图亮度。"""
    for color in ((255, 0, 0), (12, 200, 77), (230, 120, 40)):
        expected = (
            float(np.dot(np.array(color, dtype=np.float32) / 255.0, LUMA)) * 255.0
        )
        for shift in (0, 90, 180, -120):
            got = first_pixel(Adjuster(solid(color)).apply(gray=1.0, hue_shift=shift))
            assert got[0] == got[1] == got[2]
            assert abs(got[0] - expected) <= 1.5, (
                f"{color} 转 {shift}° 后全灰得到 {got[0]}，原图亮度应为 {expected:.1f}"
            )


def test_hue_still_works_below_full_gray():
    """但灰度没拉满时，色相必须照常有效 —— 别修过头把功能改没了。"""
    adjuster = Adjuster(solid((255, 0, 0)))
    base = first_pixel(adjuster.apply(gray=0.5, hue_shift=0))
    turned = first_pixel(adjuster.apply(gray=0.5, hue_shift=120))
    assert base != turned, "半灰状态下色相失效了"
    assert first_pixel(adjuster.apply(gray=0.0, hue_shift=120))[1] >= 253


def test_gray_blends_toward_original_black_and_white():
    """中间值应当是「彩色版」和「原图黑白版」的交叉淡入。"""
    color = (255, 0, 0)
    adjuster = Adjuster(solid(color))
    target = float(np.dot(np.array(color, dtype=np.float32) / 255.0, LUMA)) * 255.0
    turned = np.array(first_pixel(adjuster.apply(gray=0.0, hue_shift=120)), dtype=float)
    half = first_pixel(adjuster.apply(gray=0.5, hue_shift=120))
    for channel, colored in zip(half, turned):
        assert abs(channel - (colored + target) / 2.0) <= 1.5, (
            f"中间值不在交叉淡入线上: {half}"
        )


def test_neutral_gray_unchanged_by_gray_slider():
    """本来就是灰的像素，灰度滑块不该改动它。"""
    adjuster = Adjuster(solid((128, 128, 128)))
    for amount in (0.0, 0.3, 1.0):
        got = first_pixel(adjuster.apply(gray=amount))
        assert max(abs(v - 128) for v in got) <= 1, (
            f"gray={amount} 把中性灰改成了 {got}"
        )


# ---------- 4. 色相环绕 ----------


def test_hue_wraps_full_circle():
    """转 360° 等于没转。"""
    color = (200, 100, 50)
    adjuster = Adjuster(solid(color))
    assert (
        max(
            abs(a - b)
            for a, b in zip(first_pixel(adjuster.apply(hue_shift=360.0)), color)
        )
        <= 2
    )


def test_hue_plus_minus_180_are_equal():
    """+180 和 -180 落在色环同一点。"""
    adjuster = Adjuster(solid((200, 100, 50)))
    plus = first_pixel(adjuster.apply(hue_shift=180.0))
    minus = first_pixel(adjuster.apply(hue_shift=-180.0))
    assert max(abs(a - b) for a, b in zip(plus, minus)) <= 2, f"{plus} != {minus}"


def test_hue_shifts_are_additive_around_the_circle():
    """转 90 再转 90，应当等于一次转 180。"""
    adjuster = Adjuster(solid((200, 100, 50)))
    once = first_pixel(adjuster.apply(hue_shift=180.0))
    twice = first_pixel(
        Adjuster(rgb_to_pixmap(pixmap_to_rgb(adjuster.apply(hue_shift=90.0)))).apply(
            hue_shift=90.0
        )
    )
    assert max(abs(a - b) for a, b in zip(once, twice)) <= 3, f"{once} vs {twice}"


def test_hue_out_of_range_wraps_not_clips():
    """超出 ±180 的值要环绕，不能被截断。"""
    adjuster = Adjuster(solid((255, 0, 0)))
    assert (
        max(
            abs(a - b)
            for a, b in zip(
                first_pixel(adjuster.apply(hue_shift=480.0)),
                first_pixel(adjuster.apply(hue_shift=120.0)),
            )
        )
        <= 2
    )
    assert (
        max(
            abs(a - b)
            for a, b in zip(
                first_pixel(adjuster.apply(hue_shift=-240.0)),
                first_pixel(adjuster.apply(hue_shift=120.0)),
            )
        )
        <= 2
    )


# ---------- 5. DPR（设备像素比）----------


def test_dpr_is_preserved():
    """Retina 上截的是 2 倍图，调色后必须还是 2 倍，否则窗口尺寸会变。"""
    # 1.25 / 1.5 / 1.75 是 Windows 最常见的混合 DPI 比例。
    for ratio in (1.0, 1.25, 1.5, 1.75, 2.0, 3.0):
        adjuster = Adjuster(solid((200, 100, 50), size=8, ratio=ratio))
        assert adjuster.apply(gray=0.5).devicePixelRatio() == ratio
        assert adjuster.apply(hue_shift=45).devicePixelRatio() == ratio


def test_logical_size_unchanged_by_adjustment():
    """调色前后「逻辑尺寸」必须一致 —— 变了浮窗就会忽大忽小。"""
    source = solid((200, 100, 50), size=64, ratio=2.0)
    adjuster = Adjuster(source)
    want = source.deviceIndependentSize()
    for pixmap in (
        adjuster.apply(gray=1.0),
        adjuster.apply(hue_shift=90),
        adjuster.apply(gray=0.5, hue_shift=30),
    ):
        got = pixmap.deviceIndependentSize()
        assert (
            abs(got.width() - want.width()) < 1.5
            and abs(got.height() - want.height()) < 1.5
        )


def test_chunked_result_matches_unchunked():
    """分块多线程算出来的，必须和不分块逐像素一致。

    分块是为了跟手（实测提速 7~8 倍），但只要边界处理错一行，
    图上就会出现横向色带 —— 这条专门盯这个。
    """
    from pinref import imaging

    rng = np.random.default_rng(7)
    src = rng.integers(0, 256, (601, 397, 3), dtype=np.uint8)  # 故意用质数高度
    adjuster = Adjuster(make_pixmap(src))

    original_workers = imaging._WORKERS
    try:
        for gray, shift in (
            (0.0, 90.0),
            (0.5, 90.0),
            (1.0, 90.0),
            (0.5, 0.0),
            (0.3, -120.0),
        ):
            imaging._WORKERS = 1
            single = pixmap_to_rgb(adjuster.apply(gray=gray, hue_shift=shift)).astype(
                int
            )
            for workers in (2, 3, 8, 16):
                imaging._WORKERS = workers
                chunked = pixmap_to_rgb(
                    adjuster.apply(gray=gray, hue_shift=shift)
                ).astype(int)
                assert np.abs(single - chunked).max() <= 1, (
                    f"{workers} 块时结果不一致 (gray={gray}, shift={shift})"
                )
    finally:
        imaging._WORKERS = original_workers


def test_no_seams_at_chunk_boundaries():
    """渐变图分块处理后，块与块交界处不能出现突变。"""
    from pinref import imaging

    height = 500
    ramp = np.linspace(0, 255, height, dtype=np.float32)
    src = np.repeat(ramp[:, None, None], 40, axis=1).repeat(3, axis=2).astype(np.uint8)
    adjuster = Adjuster(make_pixmap(src))

    original_workers = imaging._WORKERS
    try:
        imaging._WORKERS = 8
        out = pixmap_to_rgb(adjuster.apply(gray=0.4, hue_shift=60)).astype(int)
    finally:
        imaging._WORKERS = original_workers
    # 相邻行的差值应当处处平缓，接缝会表现为某一行突然跳变
    row_steps = np.abs(np.diff(out[:, 0, 0].astype(int)))
    assert row_steps.max() <= 3, f"块交界处有跳变，最大行间差 {row_steps.max()}"


def test_buffer_reuse_does_not_corrupt_earlier_results():
    """输出缓冲区是复用的，先前返回的 QPixmap 不能被后一次调用改掉。"""
    adjuster = Adjuster(solid((255, 0, 0), size=64))
    first = adjuster.apply(hue_shift=120)  # 应为绿
    before = first_pixel(first)
    adjuster.apply(hue_shift=-120)  # 再算一次，改写缓冲区
    assert first_pixel(first) == before, (
        f"先前的结果被改掉了: {before} -> {first_pixel(first)}"
    )


def test_tiny_image_skips_threading():
    """小图走单块路径，结果也要对。"""
    assert (
        first_pixel(Adjuster(solid((255, 0, 0), size=8)).apply(hue_shift=120))[1] >= 253
    )


# ---------- 6. 色彩空间 ----------


def test_color_space_is_preserved():
    """调色后必须带回原图的色彩空间，否则广色域屏上颜色会偏。"""
    for named in (QColorSpace.SRgb, QColorSpace.DisplayP3, QColorSpace.AdobeRgb):
        space = QColorSpace(named)
        adjuster = Adjuster(solid((200, 100, 50), color_space=space))
        got = adjuster.apply(gray=0.5, hue_shift=30).toImage().colorSpace()
        assert got.isValid(), f"{space.description()} 丢失了"
        assert got == space, f"色彩空间被换成了 {got.description()}"


def test_color_space_preserved_on_large_threaded_image():
    rng = np.random.default_rng(4)
    big = make_pixmap(
        rng.integers(0, 256, (800, 800, 3), dtype=np.uint8),
        color_space=QColorSpace(QColorSpace.DisplayP3),
    )
    got = Adjuster(big).apply(gray=0.5, hue_shift=30).toImage().colorSpace()
    assert got.isValid() and got == QColorSpace(QColorSpace.DisplayP3)


def test_missing_color_space_does_not_crash():
    """没有色彩空间标记的图也要能正常调色。"""
    plain = solid((200, 100, 50))
    assert not plain.toImage().colorSpace().isValid()
    assert first_pixel(Adjuster(plain).apply(gray=1.0))[0] > 0


def test_color_space_only_tags_never_converts():
    """打标记不能顺带改像素值 —— 那是转换，不是标记。"""
    color = (200, 100, 50)
    tagged = first_pixel(
        Adjuster(solid(color, color_space=QColorSpace(QColorSpace.DisplayP3))).apply(
            gray=1.0
        )
    )
    plain = first_pixel(Adjuster(solid(color)).apply(gray=1.0))
    assert tagged == plain, f"带标记 {tagged} 和不带 {plain} 的像素值不一致"


# ---------- 7. 其他边界 ----------


def test_single_pixel_image():
    assert (
        first_pixel(Adjuster(solid((255, 0, 0), size=1)).apply(hue_shift=120))[1] >= 253
    )


def test_non_square_image():
    rng = np.random.default_rng(5)
    src = rng.integers(0, 256, (17, 63, 3), dtype=np.uint8)
    out = Adjuster(make_pixmap(src)).apply(gray=1.0)
    assert (out.height(), out.width()) == src.shape[:2]


def test_output_stays_in_range():
    """不能出现负值或溢出 —— 那会变成花屏。"""
    rng = np.random.default_rng(6)
    src = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
    adjuster = Adjuster(make_pixmap(src))
    for gray in (0.0, 0.5, 1.0):
        for shift in (-180, -37, 0, 91, 180):
            out = pixmap_to_rgb(adjuster.apply(gray=gray, hue_shift=shift))
            assert out.min() >= 0 and out.max() <= 255


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
