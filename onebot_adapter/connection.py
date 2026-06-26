from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from loguru import logger

OnMessage = Callable[[dict[str, Any]], Awaitable[None]]


class Connection:
    """正向 WebSocket 连接管理。

    负责:连接 OneBot 实现的 WS 服务、收发原始 JSON、断线自动重连。
    收到的每条消息(事件或 API 响应)通过 on_message 回调上抛。
    """

    def __init__(
        self,
        ws_url: str,
        *,
        access_token: str | None = None,
        reconnect_interval: float = 3.0,
        max_retries: int = 0,
    ) -> None:
        self.ws_url = ws_url
        self.access_token = access_token
        self.reconnect_interval = reconnect_interval
        self.max_retries = max_retries  # 0 = 无限重试

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._on_message: OnMessage | None = None
        self._closed = False

    def on_message(self, callback: OnMessage) -> None:
        self._on_message = callback

    async def send(self, payload: dict[str, Any]) -> None:
        if self._ws is None or self._ws.closed:
            raise ConnectionError("WebSocket 未连接")
        await self._ws.send_str(json.dumps(payload, ensure_ascii=False))

    async def run(self) -> None:
        """主循环:连接 → 收消息 → 断开 → 重连。"""
        retries = 0
        self._session = aiohttp.ClientSession()
        try:
            while not self._closed:
                try:
                    await self._connect_and_listen()
                    retries = 0
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("连接异常")
                    retries += 1
                    if self.max_retries and retries >= self.max_retries:
                        logger.error("达到最大重试次数,停止")
                        break
                    await asyncio.sleep(self.reconnect_interval)
        finally:
            if self._session and not self._session.closed:
                await self._session.close()

    async def _connect_and_listen(self) -> None:
        assert self._session is not None
        headers = {}
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"

        logger.info("正在连接 {}", self.ws_url)
        async with self._session.ws_connect(self.ws_url, headers=headers) as ws:
            self._ws = ws
            logger.info("WebSocket 已连接")
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        logger.warning("无法解析的消息: {}", msg.data)
                        continue
                    if self._on_message:
                        await self._on_message(data)
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                ):
                    logger.warning("连接关闭")
                    break
            self._ws = None

    async def close(self) -> None:
        self._closed = True
        if self._ws and not self._ws.closed:
            await self._ws.close()
