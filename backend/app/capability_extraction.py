"""Turn a natural-language App request into abstract capability requirements.

This is deliberately advisory. Requirement text is fuzzy, so the keyword pass
below is only a first guess that the model may extend. Hardware *correctness*
is never decided here — it is enforced by the portability contracts in
``capabilities`` and by the AST policy gate in ``capability_policy``.

Every capability id referenced by a keyword rule is checked against the pinned
snapshot at import time, so a typo fails loudly instead of silently matching
nothing forever.
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any, Iterable

from .capabilities import CapabilityContractError, capability_index

# capability id -> substrings that imply it, in both product locales.
CAPABILITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "camera": (
        "摄像头", "相机", "拍照", "拍摄", "camera", "photo", "selfie", "snapshot",
    ),
    "audio.output": (
        "播放", "音频", "声音", "喇叭", "扬声器", "铃声", "音乐",
        "audio", "sound", "speaker", "play music", "beep", "tone",
    ),
    "audio.input": (
        "麦克风", "录音", "收音", "语音输入",
        "microphone", "mic", "record audio", "voice input",
    ),
    "sensor.imu": (
        "陀螺仪", "加速度", "姿态", "倾斜", "摇一摇", "计步",
        "imu", "gyro", "accelerometer", "tilt", "shake", "step counter",
    ),
    "sensor.environmental": (
        "温度", "湿度", "光照", "环境光", "气压",
        "temperature", "humidity", "ambient light", "barometer",
    ),
    "lights.rgb": (
        "灯", "led", "彩灯", "氛围灯", "呼吸灯", "rgb",
        "light", "lamp", "neopixel",
    ),
    "battery": (
        "电池", "电量", "续航", "充电",
        "battery", "charge level", "power level",
    ),
    "storage.sdcard": (
        "sd卡", "sd 卡", "tf卡", "存储卡", "内存卡",
        "sd card", "sdcard", "tf card", "memory card",
    ),
    "network": (
        "联网", "网络", "wifi", "wi-fi", "上网", "接口请求", "天气",
        "network", "internet", "http", "api request", "weather",
    ),
    # Keywords for non-portable capabilities stop generation outright, so they
    # stay narrow: bare "location"/"coordinates" also describe widget layout.
    "gps": (
        "定位", "gps", "gnss", "北斗", "经纬度", "latitude", "longitude",
    ),
    "infrared": ("红外", "遥控器", "infrared", "ir remote"),
    "lora": ("lora", "远距离无线", "长距离通信"),
    "input.keypad": (
        "按键", "实体键", "physical button", "keypad", "hardware key",
    ),
    "input.encoder": ("旋钮", "编码器", "encoder", "rotary", "knob"),
}

# Context that proves a capability word does NOT mean hardware. A keyword guess
# can hard-block generation when the capability is non-portable, so a qualified
# mention ("weather temperature from an API", "on-screen buttons", "lighting
# effect") must not be read as a hardware requirement.
CAPABILITY_VETOES: dict[str, tuple[str, ...]] = {
    "sensor.environmental": (
        "天气", "预报", "接口", "网络", "联网", "api", "http", "在线", "爬取",
        "weather", "forecast", "from the internet", "web service",
    ),
    "input.keypad": (
        "屏幕上", "界面上", "屏幕按键", "虚拟按键", "触摸按钮", "画面",
        "on-screen", "on screen", "virtual button", "ui button", "touch button",
    ),
    "lights.rgb": (
        "灯光效果", "光影", "渐变", "高亮", "动画效果",
        "lighting effect", "glow effect", "highlight",
    ),
    "gps": ("模拟定位", "假定位", "mock location"),
}

# A non-portable capability aborts generation before the model is even called,
# so a bare topic word is not enough evidence that real hardware is wanted.
# "温度换算器" is a calculator; "虚拟遥控器界面" is a UI mock. Both were fatal.
# Wiring verbs are listed here but board nouns deliberately are not: "开发板"
# also appears in "模拟开发板上的红绿灯", which is a UI mock, and flagging that
# gives the session a completion blocker nothing can clear. Saying where to
# solder something has no such second reading.
HARDWARE_QUALIFIERS: tuple[str, ...] = (
    "传感器", "传感", "读取", "检测", "测量", "采集", "板载", "模块", "探测",
    "感应", "实时", "硬件", "接在", "接到", "接上", "焊接",
    "sensor", "read", "measure", "detect", "onboard", "module", "probe",
    "hardware", "live", "solder", "wired to",
)

# Keywords that are themselves unambiguous hardware nouns. Matching one is
# sufficient evidence on its own; every other keyword for a non-portable
# capability is a topic word ("温度", "遥控器") that also needs a qualifier.
STRONG_HARDWARE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gps": ("gps", "gnss", "北斗", "经纬度", "latitude", "longitude"),
    "infrared": ("红外", "infrared", "ir remote"),
    "lora": ("lora", "远距离无线", "长距离通信"),
    "sensor.environmental": ("气压", "barometer", "环境光"),
    "camera": ("摄像头", "相机", "拍照", "拍摄", "camera", "selfie"),
    "audio.input": ("麦克风", "录音", "收音", "microphone", "mic"),
    "sensor.imu": ("陀螺仪", "加速度", "计步", "imu", "gyro", "accelerometer"),
    "storage.sdcard": ("sd卡", "sd 卡", "tf卡", "sd card", "sdcard", "tf card"),
    "input.keypad": ("实体键", "实体按键", "physical button", "keypad", "hardware key"),
    "input.encoder": ("旋钮", "编码器", "encoder", "rotary", "knob"),
    "battery": ("电池", "电量", "battery"),
}

# Phrases showing the user means a *separate* part they will wire up. Only these
# may lead to an external-accessory workflow; onboard capabilities never do.
ACCESSORY_MARKERS: tuple[str, ...] = (
    "外接", "外部", "自己接", "接一个", "另外接", "扩展模块", "传感器模块",
    "焊接", "杜邦线", "面包板",
    "external", "add-on", "breakout", "wire up", "hook up", "plug in",
    "i2c module", "spi module",
)

# Fallback wording per capability, phrased so the App stays useful without it.
_FALLBACK_ZH = "没有{label}时保留其他功能，并显示清楚的不可用状态。"
_FALLBACK_EN = (
    "Keep the remaining features working without {label} and show a clear "
    "unavailable state."
)

CAPABILITY_LABELS: dict[str, tuple[str, str]] = {
    "camera": ("摄像头", "a camera"),
    "audio.output": ("扬声器", "audio output"),
    "audio.input": ("麦克风", "a microphone"),
    "sensor.imu": ("姿态传感器", "an IMU"),
    "sensor.environmental": ("环境传感器", "an environmental sensor"),
    "lights.rgb": ("RGB 灯", "RGB lights"),
    "battery": ("电池信息", "battery info"),
    "storage.sdcard": ("SD 卡", "an SD card"),
    "network": ("网络", "network access"),
    "gps": ("定位模块", "GPS"),
    "infrared": ("红外模块", "an IR module"),
    "lora": ("LoRa 模块", "a LoRa radio"),
    "input.keypad": ("实体按键", "physical keys"),
    "input.encoder": ("旋钮", "a rotary encoder"),
    "input.pointer": ("触摸屏", "a touchscreen"),
}


def _validate_keyword_targets() -> None:
    index = capability_index()
    unknown = sorted(
        {name for name in CAPABILITY_KEYWORDS if not index.has(name)}
        | {name for name in CAPABILITY_LABELS if not index.has(name)}
    )
    if unknown:
        raise CapabilityContractError(
            "Capability keyword rules reference ids absent from "
            f"board_capabilities.json: {', '.join(unknown)}"
        )


def capability_label(name: str, locale: str = "zh-CN") -> str:
    zh, en = CAPABILITY_LABELS.get(name, (name, name))
    return zh if locale == "zh-CN" else en


def _normalize(text: str) -> str:
    # Collapse whitespace so "sd 卡" and "sd卡" both match, and lowercase for
    # the ASCII keywords. Chinese keywords are unaffected by casefolding.
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _is_ascii_keyword(keyword: str) -> bool:
    return keyword.isascii()


@lru_cache(maxsize=512)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    # ASCII keywords need word boundaries: a bare substring makes "led" match
    # "ledger" and "light" match "highlight", which for a non-portable
    # capability would wrongly stop generation. CJK has no word boundaries, so
    # those stay as substring matches.
    return re.compile(r"\b" + re.escape(keyword) + r"\b")


def _contains(haystack: str, keyword: str) -> bool:
    if _is_ascii_keyword(keyword):
        return _keyword_pattern(keyword).search(haystack) is not None
    return keyword in haystack


def extract_capabilities(prompt: str, *, extra: Iterable[str] = ()) -> list[str]:
    """Best-effort capability ids implied by the request text.

    ``extra`` carries ids the model proposed; unknown ids are dropped here and
    surfaced separately by :func:`analyze_requirements` so a hallucinated
    capability never reaches the generator as if the snapshot backed it.
    """
    _validate_keyword_targets()
    index = capability_index()
    haystack = _normalize(prompt)
    qualified = any(_contains(haystack, word) for word in HARDWARE_QUALIFIERS)
    found = set()
    for name, keywords in CAPABILITY_KEYWORDS.items():
        if not any(_contains(haystack, keyword) for keyword in keywords):
            continue
        if any(_contains(haystack, veto) for veto in CAPABILITY_VETOES.get(name, ())):
            continue
        contract = index.get(name)
        # Non-portable capabilities stop generation outright, so a topic word
        # alone is not enough: it needs either an unambiguous hardware noun or
        # a qualifier implying real hardware.
        if contract is not None and not contract.portable_api:
            strong = any(
                _contains(haystack, word)
                for word in STRONG_HARDWARE_KEYWORDS.get(name, ())
            )
            if not strong and not qualified:
                continue
        found.add(name)
    # An explicitly declared capability always wins over a context veto: the
    # caller stated the requirement rather than having it guessed.
    found.update(name for name in extra if index.has(name))
    return sorted(found)


def extract_accessories(prompt: str) -> list[str]:
    """Explicit external-accessory phrases, which alone may allow a driver hunt.

    Onboard capabilities must never be converted into a driver search task, so
    this returns the raw user phrases rather than capability ids.
    """
    haystack = _normalize(prompt)
    return sorted(
        {marker for marker in ACCESSORY_MARKERS if _contains(haystack, marker)}
    )


def build_runtime_fallbacks(
    capabilities: Iterable[str], *, locale: str = "zh-CN"
) -> dict[str, str]:
    """One fallback sentence per generatable capability."""
    index = capability_index()
    template = _FALLBACK_ZH if locale == "zh-CN" else _FALLBACK_EN
    fallbacks: dict[str, str] = {}
    for name in capabilities:
        contract = index.get(name)
        if contract is None or not contract.generatable:
            continue
        fallbacks[name] = template.format(label=capability_label(name, locale))
    return fallbacks


def analyze_requirements(
    prompt: str,
    *,
    locale: str = "zh-CN",
    model_capabilities: Iterable[str] = (),
) -> dict[str, Any]:
    """Full capability analysis attached to sessions, checkpoints and artifacts.

    Returns the four spec fields plus the diagnostics the frontend needs to
    label each capability without ever claiming support the snapshot lacks.
    """
    index = capability_index()
    proposed = list(model_capabilities)
    capabilities = extract_capabilities(prompt, extra=proposed)
    accessories = extract_accessories(prompt)
    blocking = index.blocking(capabilities)
    preview_unsupported = index.preview_unsupported(capabilities)
    declared = {name for name in proposed if index.has(name)}
    haystack = _normalize(prompt)
    # Where a requirement came from decides how much weight it carries.
    # "declared" was stated outright, "strong" matched an unambiguous hardware
    # noun, "inferred" is only a topic-word guess — and a guess must never set
    # a completion blocker the user cannot clear.
    sources = {}
    for name in capabilities:
        if name in declared:
            sources[name] = "declared"
        elif any(
            _contains(haystack, word)
            for word in STRONG_HARDWARE_KEYWORDS.get(name, ())
        ):
            sources[name] = "strong"
        elif any(_contains(haystack, word) for word in HARDWARE_QUALIFIERS):
            # "接在开发板的 RGB 灯上" says where to wire real hardware. Without
            # this tier the qualifier list only ever gated non-portable
            # capabilities, so a portable one stayed a mere guess no matter how
            # explicitly the user described the wiring.
            sources[name] = "qualified"
        else:
            sources[name] = "inferred"
    evidenced = [n for n, src in sources.items() if src != "inferred"]
    return {
        "capability_sources": sources,
        "required_capabilities": capabilities,
        "required_accessories": accessories,
        "runtime_fallbacks": build_runtime_fallbacks(capabilities, locale=locale),
        # Real-device validation is only demanded for capabilities the user
        # actually asked for. A traffic-light *simulator* mentions "灯"; that
        # must not lock the session out of completing.
        "physical_validation_required": index.physical_validation_required(
            evidenced
        ),
        "evidenced_capabilities": evidenced,
        "capability_contracts": index.public_contracts(capabilities),
        "blocking_capabilities": [
            {
                "capability": contract.name,
                "code": contract.blocking_error_code(),
                "reason": contract.reason,
                "source": sources.get(contract.name, "inferred"),
            }
            for contract in blocking
        ],
        "partial_capabilities": [
            {"capability": c.name, "limitations": list(c.limitations)}
            for c in index.partial(capabilities)
        ],
        "web_preview_unsupported": [c.name for c in preview_unsupported],
        "destructive_capabilities": [
            {"capability": c.name, "operations": list(c.destructive_operations)}
            for c in index.destructive(capabilities)
        ],
        "unrecognized_capabilities": index.unknown_names(proposed),
    }
