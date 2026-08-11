"""图像处理：灰度 / 色相运算。

这是整个项目最核心的一块 —— 调色本质上就是把图片变成一堆数字，
做数学运算，再变回图片。

性能要点：色相滑块每拖一下都要重算整张图。
RGB→HSV 只在图片载入时做一次并缓存下来，之后每帧只需要
「移动色相 → HSV→RGB」，省掉一半运算量。
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PySide6.QtGui import QColorSpace, QImage, QPixmap

# 人眼对绿色最敏感、蓝色最迟钝，所以转灰度不是简单三通道平均。
# 这组权重是 ITU-R BT.601 标准，也是各种软件「去色」用的那一组。
LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def pixmap_to_rgb(pixmap: QPixmap) -> np.ndarray:
    """QPixmap → (高, 宽, 3) 的 uint8 数组，通道顺序 RGB。

    坑：QImage.Format_RGB32 在内存里是 BGRA 排列（小端序），
    所以取到的前三通道是 BGR，要用 [..., ::-1] 翻成 RGB。
    """
    image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    width, height = image.width(), image.height()
    # bytesPerLine 可能比 宽×4 大（有行末padding），所以先按它 reshape 再裁掉多余部分
    raw = np.frombuffer(image.constBits(), dtype=np.uint8)
    raw = raw.reshape(height, image.bytesPerLine() // 4, 4)
    return raw[:, :width, :3][:, :, ::-1].copy()


def rgb_to_pixmap(
    rgb: np.ndarray, ratio: float = 1.0, color_space: QColorSpace | None = None
) -> QPixmap:
    """(高, 宽, 3) uint8 RGB 数组 → QPixmap。

    ratio 是这张图相对逻辑尺寸放大了几倍，Retina 上是 2.0。

    color_space 必须显式传进来：新建的 QImage 是「无色彩空间标记」的，
    不把原图的带上，广色域屏幕上颜色会偏。
    """
    height, width = rgb.shape[:2]
    # 反过来拼回 BGRA 给 Qt
    bgra = np.empty((height, width, 4), dtype=np.uint8)
    bgra[:, :, :3] = rgb[:, :, ::-1]
    bgra[:, :, 3] = 255
    # copy() 必须有：QImage 不持有这块内存，函数返回后 bgra 就被回收了
    image = QImage(bgra.data, width, height, width * 4, QImage.Format_RGB32).copy()
    if color_space is not None and color_space.isValid():
        # 只是打标记，不做转换 —— 像素本来就在这个空间里
        image.setColorSpace(color_space)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(ratio)
    return pixmap


# ---------- RGB / HSV 互转（向量化，整张图一次算完） ----------


def rgb_to_hsv(rgb01: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """RGB（0~1 浮点）→ H, S, V 三个独立数组，H 也是 0~1。

    色相 H 本质是「颜色在色环上的角度」。算法是看红绿蓝哪个最大，
    以它为基准算出偏移量，再折算成 0~1 的环形坐标。
    """
    red, green, blue = rgb01[..., 0], rgb01[..., 1], rgb01[..., 2]
    high = rgb01.max(axis=-1)
    low = rgb01.min(axis=-1)
    span = high - low

    hue = np.zeros_like(high)
    # span 为 0 表示这个像素是纯灰色，没有色相可言，保持 0
    colored = span > 1e-6
    is_red = colored & (high == red)
    is_green = colored & (high == green) & ~is_red
    is_blue = colored & ~is_red & ~is_green

    hue[is_red] = ((green - blue)[is_red] / span[is_red]) % 6.0
    hue[is_green] = (blue - red)[is_green] / span[is_green] + 2.0
    hue[is_blue] = (red - green)[is_blue] / span[is_blue] + 4.0
    hue /= 6.0

    saturation = np.where(high > 1e-6, span / np.maximum(high, 1e-6), 0.0)
    return hue.astype(np.float32), saturation.astype(np.float32), high


def hsv_to_rgb(
    hue: np.ndarray, saturation: np.ndarray, value: np.ndarray
) -> np.ndarray:
    """H, S, V（都是 0~1）→ (高, 宽, 3) 的 RGB 浮点数组。

    用的是无分支写法：每个通道 c = v - v*s*clamp(min(k, 4-k), 0, 1)，
    其中 k = (n + h*6) mod 6，红绿蓝分别取 n = 5, 3, 1。

    为什么不用课本上那套「分 6 段、每段取 v/p/q/t」的写法：那要靠
    np.select 做分支，每个通道会生成 6 个全尺寸临时数组，三个通道就是 18 个。
    整张图这么算，1600×1200 要 100ms 以上，滑块会明显卡。
    这个写法每通道只有 4 步运算，快一个数量级，结果完全等价。
    """
    chroma = value * saturation
    hue6 = hue * 6.0

    def channel(n: float) -> np.ndarray:
        k = (n + hue6) % 6.0
        k = np.minimum(k, 4.0 - k)
        np.clip(k, 0.0, 1.0, out=k)
        return value - chroma * k

    return np.stack([channel(5.0), channel(3.0), channel(1.0)], axis=-1)


# 分块并行用的线程池。numpy 的 ufunc 会释放 GIL，所以多线程是真并行。
# 实测提速 7~8 倍：3024×1964 整块 Retina 屏从 129ms 降到 16.5ms。
_WORKERS = min(8, (os.cpu_count() or 4))
_POOL = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="pinref-imaging")

# 小于这个像素数就不分块 —— 图太小时，调度开销比算本身还贵
_MIN_PIXELS_FOR_THREADS = 200_000


class Adjuster:
    """持有一张图的原始数据和 HSV 缓存，按参数产出调整后的 QPixmap。

    每个浮窗一个实例。图片只在创建时解析一次。

    **不做降采样预览**：早期版本拖动滑块时用降采样版保证跟手，
    松手再出全分辨率。但那样拖动过程中图是糊的、松手才变清晰，
    这个清晰度跳变很扎眼。改成分块多线程后常见尺寸的调节已经足够流畅，
    预览那套整个不需要了。
    """

    def __init__(self, pixmap: QPixmap):
        self.ratio = pixmap.devicePixelRatio()
        self.source = pixmap
        # 记下原图的色彩空间，调色后要原样带回去，否则广色域屏上颜色会偏
        self.color_space = pixmap.toImage().colorSpace()

        rgb01 = pixmap_to_rgb(pixmap).astype(np.float32) / 255.0
        self._height, self._width = rgb01.shape[:2]

        # 下面这些都只跟原图有关，一次算好反复用
        hue, saturation, value = rgb_to_hsv(rgb01)
        self._hue = hue
        self._value = value
        # chroma 与色相无关，提出来省一趟乘法
        self._chroma = (value * saturation).astype(np.float32)
        # 原图亮度。注意是从**原图**算的，不是转过色相之后的图，详见 apply
        self._luma = (rgb01 @ LUMA).astype(np.float32)

        # 输出缓冲区复用，避免每帧重新分配几十 MB
        self._bgra = np.empty((self._height, self._width, 4), dtype=np.uint8)
        self._bgra[:, :, 3] = 255

    def apply(self, gray: float = 0.0, hue_shift: float = 0.0) -> QPixmap:
        """gray: 0~1，0 是原图 1 是纯灰。hue_shift: -180~180 度。

        **灰度用的是原图亮度，不是转过色相之后的亮度。** 这是有意的：
        灰度滑块的用途是「看原图的明暗关系」，如果亮度跟着色相走，
        看到的就是一张并不存在的图的素描关系，会误导判断 ——
        实测纯红转到纯绿，明度会从 76 跳到 150，饱和色最多能波动 197 级。
        所以这里是「彩色版」和「原图黑白版」之间做交叉淡入，
        灰度拉满时永远是原图真实的明暗，此时色相滑块不起作用（符合直觉：
        已经在看纯明暗了，颜色本来就该不参与）。
        """
        # 都没调就直接还原图，省掉整趟运算
        if gray <= 0.0 and abs(hue_shift) < 1e-6:
            return self.source

        pixels = self._height * self._width
        chunks = _WORKERS if pixels >= _MIN_PIXELS_FOR_THREADS else 1
        step = (self._height + chunks - 1) // chunks
        bounds = [
            (y, min(y + step, self._height)) for y in range(0, self._height, step)
        ]

        if len(bounds) == 1:
            self._render_rows(bounds[0][0], bounds[0][1], gray, hue_shift)
        else:
            # 各块写的是互不重叠的行，不用加锁
            list(
                _POOL.map(
                    lambda b: self._render_rows(b[0], b[1], gray, hue_shift), bounds
                )
            )

        image = QImage(
            self._bgra.data,
            self._width,
            self._height,
            self._width * 4,
            QImage.Format_RGB32,
        ).copy()  # copy 必须有：缓冲区下一帧还要复用
        if self.color_space.isValid():
            image.setColorSpace(self.color_space)
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(self.ratio)
        return pixmap

    def _render_rows(self, y0: int, y1: int, gray: float, hue_shift: float):
        """算 [y0, y1) 这几行，直接写进 BGRA 输出缓冲区。

        全程用预分配的临时数组做原地运算，不生成 (高,宽,3) 的浮点中间量 ——
        大图上那个中间量光分配就要几十毫秒。
        """
        out = self._bgra[y0:y1]
        scratch = np.empty((y1 - y0, self._width), dtype=np.float32)
        spare = np.empty_like(scratch)

        if gray >= 1.0:
            # 全灰时结果只由原图亮度决定，色相怎么转都一样，跳过整套 HSV 运算
            np.multiply(self._luma[y0:y1], 255.0, out=scratch)
            np.add(scratch, 0.5, out=scratch)
            np.clip(scratch, 0.0, 255.0, out=scratch)
            out[:, :, 0] = out[:, :, 1] = out[:, :, 2] = scratch
            return

        hue6 = ((self._hue[y0:y1] + hue_shift / 360.0) % 1.0) * 6.0
        chroma = self._chroma[y0:y1]
        value = self._value[y0:y1]
        luma = self._luma[y0:y1]

        # BGRA 的通道下标 0/1/2 对应 蓝/绿/红，色环偏移分别是 1/3/5
        for index, offset in ((2, 5.0), (1, 3.0), (0, 1.0)):
            # c = v - v*s*clamp(min(k, 4-k), 0, 1)，k = (offset + h*6) mod 6
            np.add(hue6, offset, out=scratch)
            np.mod(scratch, 6.0, out=scratch)
            np.subtract(4.0, scratch, out=spare)
            np.minimum(scratch, spare, out=scratch)
            np.clip(scratch, 0.0, 1.0, out=scratch)
            np.multiply(scratch, chroma, out=scratch)
            np.subtract(value, scratch, out=scratch)

            if gray > 0.0:
                # 往「原图黑白版」这个固定目标插值
                np.multiply(scratch, 1.0 - gray, out=scratch)
                np.multiply(luma, gray, out=spare)
                np.add(scratch, spare, out=scratch)

            np.multiply(scratch, 255.0, out=scratch)
            np.add(scratch, 0.5, out=scratch)
            np.clip(scratch, 0.0, 255.0, out=scratch)
            out[:, :, index] = scratch
