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


_LOG_HEADER_WIDTH = 29  # "2026-06-26 21:01:31 INFO     " = 19+1+8+1


def event_summary(proto: dict[str, Any]) -> None:
    """按 INFO 级别输出事件摘要, 截断到终端宽度."""
    etype = proto["type"]
    data = proto["data"]
    budget = (terminal_width() or 80) - _LOG_HEADER_WIDTH
    group_id = data.get("group_id")
    user_id = data.get("user_id")
    scope = f"[群{group_id}]" if group_id else f"[私{user_id}]"
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
        # 去掉 group_/friend_ 等前缀, 如 group_recall -> recall
        detail = detail.split("_", 1)[-1] if "_" in detail else detail
        sub = sub.split("_", 1)[-1] if "_" in sub else sub
        # notify 是包装层, 实际动作在 sub 里
        desc = sub if detail == "notify" else (detail if not sub else f"{detail} {sub}")
        if data.get("comment"):
            line = f"{scope} [{desc}] {data['comment']}"
        else:
            line = f"{scope} [{desc}]"
    else:
        line = f"[{etype}] {data}"
    if len(line) > budget:
        line = line[:budget] + "..."
    logger.info(line)
