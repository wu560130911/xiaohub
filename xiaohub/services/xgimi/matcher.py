"""口令匹配：中文里动词和宾语顺序不固定，这里用模式而不是死字符串。"""

from __future__ import annotations

import re

_PUNCT = re.compile(r"[\s，。！？、,.!?；;：:“”\"'（）()]")

BUILTIN_ON = (
    r"(打开|开启|启动|开)(一下)?(投影|投影仪)",
    r"(投影|投影仪)(开机|打开|开启)",
    r"(把|给)(投影|投影仪)开",
)
BUILTIN_OFF = (
    r"(关闭|关掉|关上|关)(一下)?(投影|投影仪)",
    r"(投影|投影仪)(关机|关闭|关掉|关了)",
    r"(把|给)(投影|投影仪)关",
)

DEFAULT_ON_KEYS = ("打开投影", "开投影")
DEFAULT_OFF_KEYS = ("关闭投影", "关投影")


def normalize(text: str) -> str:
    return _PUNCT.sub("", text or "")


def detect_intent(
    text: str, on_keys: list[str] | tuple[str, ...], off_keys: list[str] | tuple[str, ...]
) -> str | None:
    """返回 ``"on"`` / ``"off"`` / ``None``。配置的关键词优先于内置模式。"""
    normalized = normalize(text)
    if not normalized:
        return None
    for key in on_keys:
        if normalize(key) and normalize(key) in normalized:
            return "on"
    for key in off_keys:
        if normalize(key) and normalize(key) in normalized:
            return "off"
    for pattern in BUILTIN_OFF:
        if re.search(pattern, normalized):
            return "off"
    for pattern in BUILTIN_ON:
        if re.search(pattern, normalized):
            return "on"
    return None
