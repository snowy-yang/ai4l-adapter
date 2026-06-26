from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .message import Message

logger = logging.getLogger("onebot_adapter.event")

EventHandler = Callable[["Event"], Awaitable[Any]]


@dataclass
class Event:
    """OneBot 11 事件基类。原始数据保留在 raw 中。"""

    post_type: str
    raw: dict[str, Any]

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> Event:
        post_type = data.get("post_type", "")
        match post_type:
            case "message":
                return MessageEvent.from_raw(data)
            case "notice":
                return NoticeEvent.from_raw(data)
            case "request":
                return RequestEvent.from_raw(data)
            case _:
                return cls(post_type=post_type, raw=data)


@dataclass
class MessageEvent(Event):
    message_type: str = ""
    sub_type: str = ""
    user_id: int = 0
    group_id: int | None = None
    message: Message = None  # type: ignore[assignment]
    raw_message: str = ""
    self_id: int = 0

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> MessageEvent:
        return cls(
            post_type="message",
            raw=data,
            message_type=data.get("message_type", ""),
            sub_type=data.get("sub_type", ""),
            user_id=data.get("user_id", 0),
            group_id=data.get("group_id"),
            message=Message.from_raw(data.get("message")),
            raw_message=data.get("raw_message", ""),
            self_id=data.get("self_id", 0),
        )

    @property
    def is_private(self) -> bool:
        return self.message_type == "private"

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"


@dataclass
class NoticeEvent(Event):
    notice_type: str = ""
    sub_type: str = ""
    user_id: int = 0
    group_id: int | None = None

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> NoticeEvent:
        return cls(
            post_type="notice",
            raw=data,
            notice_type=data.get("notice_type", ""),
            sub_type=data.get("sub_type", ""),
            user_id=data.get("user_id", 0),
            group_id=data.get("group_id"),
        )


@dataclass
class RequestEvent(Event):
    request_type: str = ""
    sub_type: str = ""
    user_id: int = 0
    group_id: int | None = None
    comment: str = ""

    @classmethod
    def from_raw(cls, data: dict[str, Any]) -> RequestEvent:
        return cls(
            post_type="request",
            raw=data,
            request_type=data.get("request_type", ""),
            sub_type=data.get("sub_type", ""),
            user_id=data.get("user_id", 0),
            group_id=data.get("group_id"),
            comment=data.get("comment", ""),
        )


class Dispatcher:
    """简单事件分发器:按 post_type 分流到注册的 handler。"""

    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = {}

    def on(self, post_type: str) -> Callable[[EventHandler], EventHandler]:
        def decorator(func: EventHandler) -> EventHandler:
            self._handlers.setdefault(post_type, []).append(func)
            return func

        return decorator

    async def dispatch(self, event: Event) -> None:
        for handler in self._handlers.get(event.post_type, []):
            try:
                await handler(event)
            except Exception:
                logger.exception("handler error for %s", event.post_type)
