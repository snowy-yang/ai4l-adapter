from __future__ import annotations

import shutil
import sys
from typing import Any

from loguru import logger


def setup(level: str) -> None:
    """配置 loguru, 替换默认 handler."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<level>{message}</level>"
        ),
    )


def terminal_width() -> int:
    return shutil.get_terminal_size((80, 20)).columns


def event_summary(proto: dict[str, Any]) -> None:
    """按 INFO 级别输出事件摘要, 截断到终端宽度."""
    etype = proto["type"]
    data = proto["data"]
    budget = terminal_width() or 80
    scope = f"[群{data.get('group_id')}]" if data.get("group_id") else "[私聊]"
    if etype == "message":
        msg = data.get("message", [])
        parts: list[str] = []
        for seg in msg:
            if seg.get("type") == "text":
                parts.append(seg.get("text", ""))
            else:
                parts.append(f"[{seg.get('type')}]")
        text = "".join(parts).replace("\n", " ")
        line = f"{scope} {text}"
    elif etype == "notice":
        detail = data.get("detail", "")
        sub = data.get("sub", "")
        desc = detail if not sub else f"{detail} {sub}"
        if data.get("comment"):
            desc = f"{desc}: {data['comment']}"
        line = f"{scope} {desc}"
    else:
        line = f"[{etype}] {data}"
    if len(line) > budget:
        line = line[:budget] + "..."
    logger.info(line)
