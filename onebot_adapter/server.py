from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

from .api import ApiError
from .bot import Bot
from .event import Event, MessageEvent, NoticeEvent, RequestEvent
from .message import MessageSegment

logger = logging.getLogger("onebot_adapter.server")


def _ob_segments_to_proto(segments: list[MessageSegment]) -> list[dict[str, Any]]:
    """OneBot 消息段 (嵌套 {type, data}) -> 协议消息段 (扁平 {type, ...})."""
    result: list[dict[str, Any]] = []
    for seg in segments:
        proto: dict[str, Any] = {"type": seg.type}
        proto.update(seg.data)
        result.append(proto)
    return result


def _proto_segments_to_ob(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """协议消息段 (扁平 {type, ...}) -> OneBot 消息段 (嵌套 {type, data})."""
    result: list[dict[str, Any]] = []
    for seg in segments:
        ob: dict[str, Any] = {
            "type": seg["type"],
            "data": {k: v for k, v in seg.items() if k != "type"},
        }
        result.append(ob)
    return result


class Server:
    """HTTP + SSE 桥, 把 OneBot 翻译成极简协议对外暴露.

    - 事件: OneBot 事件 -> 翻译 -> 通过 SSE (GET events_path) 推给客户端.
    - 指令: 客户端 POST action_path {action, params} -> 调 OneBot API -> 回 {ok, data/error}.

    协议事件 (SSE 的 event 字段 = OneBot post_type, data 为 JSON):
        message: {"kind","user_id","group_id","message","message_id","self_id"}
        notice:  {"notice_type","sub_type","user_id","group_id"}
        request: {"request_type","sub_type","user_id","group_id","comment"}

    协议指令 (POST body):
        {"action": "send_msg", "params": {"group_id": 1, "message": "hi"}}
    响应:
        {"ok": true,  "data": {...}}
        {"ok": false, "error": {"retcode": 1000, "message": "..."}}
    """

    def __init__(
        self,
        bot: Bot,
        *,
        host: str = "127.0.0.1",
        port: int = 8080,
        events_path: str = "/events",
        action_path: str = "/action",
    ) -> None:
        self.bot = bot
        self.host = host
        self.port = port
        self.events_path = events_path
        self.action_path = action_path
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._runner: web.AppRunner | None = None

        # 挂到 dispatcher 的三类事件上, 统一走翻译后推给订阅者
        for post_type in ("message", "notice", "request"):
            bot.dispatcher.on(post_type)(self._on_event)

    # ---- 事件翻译 + 分发 ----

    async def _on_event(self, event: Event) -> None:
        proto = self._translate(event)
        for q in self._subscribers:
            q.put_nowait(proto)

    @staticmethod
    def _translate(event: Event) -> dict[str, Any]:
        if isinstance(event, MessageEvent):
            return {
                "type": "message",
                "data": {
                    "kind": event.message_type,
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "message": _ob_segments_to_proto(event.message.segments),
                    "message_id": event.raw.get("message_id"),
                    "self_id": event.self_id,
                },
            }
        if isinstance(event, NoticeEvent):
            return {
                "type": "notice",
                "data": {
                    "notice_type": event.notice_type,
                    "sub_type": event.sub_type,
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                },
            }
        if isinstance(event, RequestEvent):
            return {
                "type": "request",
                "data": {
                    "request_type": event.request_type,
                    "sub_type": event.sub_type,
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "comment": event.comment,
                },
            }
        return {"type": event.post_type, "data": event.raw}

    # ---- 指令处理 ----

    async def _handle_action(self, body: Any) -> tuple[int, dict[str, Any]]:
        """核心指令逻辑: 解析 -> 调 OneBot API -> 返回 (status, 响应体)."""
        if not isinstance(body, dict) or not body.get("action"):
            return 400, {
                "ok": False,
                "data": None,
                "error": {"retcode": -1, "message": "missing action"},
            }
        action = body["action"]
        params = body.get("params") or {}
        # 协议消息段 (扁平) -> OneBot 消息段 (嵌套); 字符串便利转 text 段
        msg = params.get("message")
        if isinstance(msg, str):
            params["message"] = [{"type": "text", "data": {"text": msg}}]
        elif isinstance(msg, list):
            params["message"] = _proto_segments_to_ob(msg)

        try:
            resp = await self.bot.api.call(action, **params)
            return 200, {"ok": True, "data": resp.get("data"), "error": None}
        except ApiError as e:
            return 200, {
                "ok": False,
                "data": None,
                "error": {"retcode": e.retcode, "message": e.message},
            }
        except Exception as e:
            logger.exception("action 调用失败")
            return 500, {
                "ok": False,
                "data": None,
                "error": {"retcode": -1, "message": str(e)},
            }

    # ---- HTTP handlers ----

    async def _sse_handler(self, request: web.Request) -> web.StreamResponse:
        resp = web.StreamResponse(
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )
        await resp.prepare(request)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            await resp.write(b"retry: 3000\n\n")
            while True:
                transport = request.transport
                if transport is None or transport.is_closing():
                    break
                try:
                    proto = await asyncio.wait_for(queue.get(), timeout=1.0)
                except TimeoutError:
                    continue
                payload = json.dumps(proto["data"], ensure_ascii=False)
                chunk = f"event: {proto['type']}\ndata: {payload}\n\n"
                try:
                    await resp.write(chunk.encode("utf-8"))
                    await resp.drain()
                except (ConnectionResetError, BrokenPipeError):
                    break
        finally:
            self._subscribers.discard(queue)
        return resp

    async def _action_handler(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {
                    "ok": False,
                    "data": None,
                    "error": {"retcode": -1, "message": "invalid json"},
                },
                status=400,
            )
        status, payload = await self._handle_action(body)
        return web.json_response(payload, status=status)

    # ---- 生命周期 ----

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(self.events_path, self._sse_handler)
        app.router.add_post(self.action_path, self._action_handler)
        return app

    async def _start_http(self) -> None:
        app = self._build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

    async def run(self) -> None:
        """启动 HTTP+SSE 服务, 并阻塞运行 OneBot WS 连接."""
        await self._start_http()
        logger.info("协议服务监听 http://%s:%d", self.host, self.port)
        await self.bot.run()

    async def close(self) -> None:
        await self.bot.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
