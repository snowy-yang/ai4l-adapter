from __future__ import annotations

import logging
from typing import Any

from .api import ApiCaller
from .connection import Connection
from .event import Dispatcher, Event
from .message import Message, MessageSegment

logger = logging.getLogger("onebot_adapter")


class Bot:
    """OneBot 11 正向 WebSocket 机器人客户端。

    组合 Connection(收发)+ ApiCaller(API 调用)+ Dispatcher(事件分发)。
    """

    def __init__(
        self,
        ws_url: str,
        *,
        access_token: str | None = None,
        reconnect_interval: float = 3.0,
    ) -> None:
        self.api = ApiCaller()
        self.connection = Connection(
            ws_url,
            access_token=access_token,
            reconnect_interval=reconnect_interval,
        )
        self.dispatcher = Dispatcher()

        # 连接层收到消息 → 区分事件/响应 → 分发或喂给 ApiCaller
        self.connection.on_message(self._on_message)
        self.api.bind_send(self._send)

    async def _send(self, payload: dict[str, Any]) -> None:
        await self.connection.send(payload)

    async def _on_message(self, data: dict[str, Any]) -> None:
        if "post_type" in data:
            event = Event.from_raw(data)
            await self.dispatcher.dispatch(event)
        else:
            self.api.feed_response(data)

    # ---- 便捷 API 封装 ----

    async def send_msg(
        self,
        *,
        user_id: int | None = None,
        group_id: int | None = None,
        message: str | Message | list[MessageSegment],
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if group_id is not None:
            params["group_id"] = group_id
        elif user_id is not None:
            params["user_id"] = user_id
        params["message"] = _normalize_message(message)
        return await self.api.call("send_msg", **params)

    async def send_private_msg(
        self, user_id: int, message: str | Message | list[MessageSegment]
    ) -> dict[str, Any]:
        return await self.api.call(
            "send_private_msg", user_id=user_id, message=_normalize_message(message)
        )

    async def send_group_msg(
        self, group_id: int, message: str | Message | list[MessageSegment]
    ) -> dict[str, Any]:
        return await self.api.call(
            "send_group_msg", group_id=group_id, message=_normalize_message(message)
        )

    async def get_login_info(self) -> dict[str, Any]:
        return await self.api.call("get_login_info")

    # ---- 生命周期 ----

    def on_message(self):
        """装饰器:注册消息事件处理器。"""

        return self.dispatcher.on("message")

    def on_notice(self):
        return self.dispatcher.on("notice")

    def on_request(self):
        return self.dispatcher.on("request")

    async def run(self) -> None:
        await self.connection.run()

    async def close(self) -> None:
        self.api.cancel_all()
        await self.connection.close()


def _normalize_message(
    message: str | Message | list[MessageSegment],
) -> list[dict[str, Any]]:
    if isinstance(message, str):
        return [MessageSegment.text(message).to_dict()]
    if isinstance(message, Message):
        return message.to_dict()
    return [seg.to_dict() for seg in message]
