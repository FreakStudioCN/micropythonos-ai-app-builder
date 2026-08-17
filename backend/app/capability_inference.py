"""Infer abstract hardware capabilities from a natural-language App request.

Split out of ``runner_services`` because these rules are data rather than
service behaviour, and because a wrong guess is expensive in both directions:
a capability read out of a topic word can stop generation outright, while a
missed one lets the generator write code for hardware the App never declared.

Two rules do the work.

ASCII terms match on word boundaries. Plain substring matching read "mic" out
of "dynamic", "imu" out of "simulator", "lora" out of "flora" and "touch" out
of "retouch". Those were not cosmetic: LoRa has no portable MicroPythonOS API,
so "flora encyclopedia browser" failed with a non-retryable
``MPOS_CAPABILITY_API_MISSING`` and could not be generated at all.

A capability with no portable API is accepted only on strong evidence, because
that is the guess which blocks generation. CJK terms have no word boundary to
lean on, and "定位" describes widget layout far more often than it describes
GNSS, so "CSS 定位练习" was blocked the same way.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

# capability id -> terms that imply it, in both product locales.
CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "camera": ("camera", "摄像", "相机", "拍照", "二维码"),
    "audio.input": ("microphone", "mic", "麦克风", "录音", "语音输入"),
    "audio.output": ("speaker", "audio", "扬声器", "播放声音", "蜂鸣"),
    "sensor.imu": ("imu", "accelerometer", "gyroscope", "陀螺仪", "加速度"),
    "sensor.environmental": ("temperature sensor", "humidity", "温湿度", "气压"),
    "lights.rgb": ("rgb", "neopixel", "彩灯", "灯带"),
    "battery": ("battery", "电池", "电量"),
    "storage.sdcard": ("sd card", "sdcard", "存储卡", "sd 卡"),
    "network": ("wifi", "network", "联网", "网络"),
    "gps": ("gps", "定位", "经纬度"),
    "infrared": ("infrared", "ir remote", "红外"),
    "lora": ("lora", "远距离无线"),
    "input.pointer": ("touch", "pointer", "触摸", "鼠标"),
    "input.encoder": ("encoder", "旋钮", "编码器"),
    "input.keypad": ("keypad", "键盘", "按键矩阵"),
}

# Context proving a term does not mean hardware here.
CAPABILITY_VETOES: dict[str, tuple[str, ...]] = {
    "input.keypad": (
        "虚拟键盘", "软键盘", "屏幕键盘", "on-screen keyboard", "virtual keyboard",
    ),
    "sensor.environmental": (
        "天气", "预报", "接口", "在线", "weather", "forecast", "api", "http",
    ),
}

# Terms that are unambiguous hardware nouns on their own. Anything else is a
# topic word that also needs a qualifier before it counts as real evidence.
STRONG_HARDWARE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "camera": ("camera", "摄像", "相机", "拍照"),
    "audio.input": ("microphone", "mic", "麦克风", "录音"),
    "sensor.imu": ("imu", "accelerometer", "gyroscope", "陀螺仪", "加速度"),
    "sensor.environmental": ("temperature sensor", "温湿度", "气压"),
    "storage.sdcard": ("sd card", "sdcard", "存储卡", "sd 卡"),
    "gps": ("gps", "经纬度"),
    "infrared": ("infrared", "ir remote", "红外"),
    "lora": ("lora", "远距离无线"),
    "input.encoder": ("encoder", "旋钮", "编码器"),
}

# Wiring and measurement verbs. Board nouns are deliberately absent: "开发板"
# also appears in "模拟开发板上的红绿灯", which is a UI mock.
HARDWARE_QUALIFIERS: tuple[str, ...] = (
    "传感器", "读取", "检测", "测量", "采集", "板载", "模块", "探测", "感应",
    "硬件", "接在", "接到", "焊接", "杜邦线",
    "sensor", "measure", "detect", "onboard", "module", "probe", "hardware",
    "solder", "wired to",
)


@lru_cache(maxsize=512)
def _pattern(term: str) -> re.Pattern[str]:
    return re.compile(r"\b" + re.escape(term) + r"\b")


def _contains(haystack: str, term: str) -> bool:
    # CJK has no word boundaries, so those stay substring matches.
    if term.isascii():
        return _pattern(term).search(haystack) is not None
    return term in haystack


def _matches(haystack: str, terms: Iterable[str]) -> bool:
    return any(_contains(haystack, term) for term in terms)


def analyze_capabilities(
    prompt: str, non_portable: frozenset[str] = frozenset()
) -> dict[str, str]:
    """Capabilities implied by ``prompt``, mapped to how strong the evidence is.

    ``strong`` matched an unambiguous hardware noun, ``qualified`` matched a
    topic word next to wiring or measurement language, and ``inferred`` is a
    topic word on its own. Callers must not let ``inferred`` demand anything the
    user cannot undo, and capabilities named in ``non_portable`` are dropped
    entirely at that level because they stop generation.
    """
    haystack = re.sub(r"\s+", " ", prompt or "").strip().casefold()
    qualified = _matches(haystack, HARDWARE_QUALIFIERS)
    sources: dict[str, str] = {}
    for name, terms in CAPABILITY_KEYWORDS.items():
        if not _matches(haystack, terms):
            continue
        if _matches(haystack, CAPABILITY_VETOES.get(name, ())):
            continue
        if _matches(haystack, STRONG_HARDWARE_KEYWORDS.get(name, ())):
            sources[name] = "strong"
        elif qualified:
            sources[name] = "qualified"
        elif name not in non_portable:
            sources[name] = "inferred"
    return sources
