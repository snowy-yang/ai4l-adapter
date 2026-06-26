from __future__ import annotations

import asyncio
import uuid
from typing import Any


class ApiCaller:
    """OneBot 11 API 调用层。

    通过 echo 字段将 action 请求与响应 future 配对。
    需要由连接层在收到响应时调用 feed_response。
    """

    def __init__(self) -> None:
        self._futures: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._send: Any = None  # 由连接层注入的发送协程

    def bind_send(self, send: Any) -> None:
        """注入发送函数:async def send(text: str) -> None"""
        self._send = send

    def feed_response(self, data: dict[str, Any]) -> bool:
        """连接层收到非 event 消息时调用。返回是否匹配到等待中的请求。"""
        echo = data.get("echo")
        if echo is None:
            return False
        fut = self._futures.pop(echo, None)
        if fut is None or fut.done():
            return False
        if data.get("retcode", 0) == 0:
            fut.set_result(data)
        else:
            fut.set_exception(
                ApiError(
                    retcode=data.get("retcode", -1),
                    message=data.get("msg") or data.get("wording") or "unknown",
                )
            )
        return True

    async def call(self, action: str, **params: Any) -> dict[str, Any]:
        """调用 OneBot API 并等待响应。"""
        if self._send is None:
            raise RuntimeError("ApiCaller 未绑定发送函数")

        echo = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._futures[echo] = fut

        await self._send({"action": action, "params": params, "echo": echo})
        return await fut

    def cancel_all(self) -> None:
        """连接断开时取消所有等待中的请求。"""
        for fut in self._futures.values():
            if not fut.done():
                fut.set_exception(ConnectionError("连接已断开"))
        self._futures.clear()


class ApiError(Exception):
    def __init__(self, retcode: int, message: str) -> None:
        self.retcode = retcode
        self.message = message
        super().__init__(f"[{retcode}] {message}")
