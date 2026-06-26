from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

import aiohttp
from aiohttp import web

from .api import ApiError
from .bot import Bot
from .event import Event, MessageEvent, NoticeEvent, RequestEvent
from .message import MessageSegment

logger = logging.getLogger("onebot_adapter.server")

# 这些 type 的消息段含二进制/媒体资源, 用 content (base64) 表示.
_MEDIA_TYPES = frozenset({"image", "video", "audio", "file", "voice", "flash"})


async def _ob_segments_to_proto(
    segments: list[MessageSegment], session: aiohttp.ClientSession | None = None
) -> list[dict[str, Any]]:
    """OneBot 消息段 -> 协议消息段.

    - text: {"type":"text", "text":"..."}
    - 媒体: {"type":"image", "content":"<base64>"} (下载/读取/剥前缀)
    - 其他: 字段扁平透传 (at/reply 等)
    """
    result: list[dict[str, Any]] = []
    for seg in segments:
        if seg.type == "text":
            result.append({"type": "text", "text": seg.data.get("text", "")})
        elif seg.type in _MEDIA_TYPES:
            content = await _resolve_to_base64(seg.data.get("file", ""), session)
            result.append({"type": seg.type, "content": content})
        else:
            proto: dict[str, Any] = {"type": seg.type}
            proto.update(seg.data)
            result.append(proto)
    return result


async def _resolve_to_base64(
    file: str, session: aiohttp.ClientSession | None = None
) -> str:
    """把 OneBot file 字段统一转成 base64 字符串.

    支持: base64:// 前缀 / http(s) URL / 本地路径.
    """
    if not file:
        return ""
    if file.startswith("base64://"):
        return file[len("base64://") :]
    if file.startswith(("http://", "https://")):
        own_session = session is None
        sess = session or aiohttp.ClientSession()
        try:
            async with sess.get(file) as resp:
                resp.raise_for_status()
                data = await resp.read()
            return base64.b64encode(data).decode("ascii")
        finally:
            if own_session:
                await sess.close()
    # 本地路径
    if os.path.isfile(file):
        with open(file, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    # 无法识别, 原样返回 (调用方自行判断)
    return file


def _proto_segments_to_ob(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """协议消息段 -> OneBot 消息段 (嵌套 {type, data}).

    - text: data={"text":...}
    - 媒体 (有 content): data={"file":"base64://<content>", ...其余字段}
    - 其他: 扁平字段塞进 data
    """
    result: list[dict[str, Any]] = []
    for seg in segments:
        seg_type = seg.get("type", "")
        if seg_type == "text":
            result.append({"type": "text", "data": {"text": seg.get("text", "")}})
        elif "content" in seg:
            data: dict[str, Any] = {"file": f"base64://{seg['content']}"}
            data.update({k: v for k, v in seg.items() if k not in ("type", "content")})
            result.append({"type": seg_type, "data": data})
        else:
            result.append(
                {
                    "type": seg_type,
                    "data": {k: v for k, v in seg.items() if k != "type"},
                }
            )
    return result


class Server:
    """HTTP + SSE 桥, 把 OneBot 翻译成极简协议对外暴露.

    - 事件: OneBot 事件 -> 翻译 -> 通过 SSE (GET events_path) 推给客户端.
    - 指令: 客户端 POST action_path {action, params} -> 调 OneBot API -> 回 {ok, data/error}.

    协议消息段 (统一对象, 不含 CQ 码):
        text:  {"type":"text", "text":"hi"}
        媒体:  {"type":"image", "content":"<base64>"}   (image/video/audio/file/voice)
        其他:  {"type":"at", "qq":"1"}                   (at/reply 等扁平透传)

    协议事件 (SSE 的 event 字段 = OneBot post_type, data 为 JSON):
        message: {"kind","user_id","group_id","message","message_id","self_id"}
        notice:  {"notice_type","sub_type","user_id","group_id"}
        request: {"request_type","sub_type","user_id","group_id","comment"}

    协议指令 (POST body):
        {"action": "send_msg", "params": {"group_id": 1, "message": [{"type":"text","text":"hi"}]}}
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
        proto = await self._translate(event)
        for q in self._subscribers:
            q.put_nowait(proto)

    async def _translate(self, event: Event) -> dict[str, Any]:
        if isinstance(event, MessageEvent):
            return {
                "type": "message",
                "data": {
                    "kind": event.message_type,
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "message": await _ob_segments_to_proto(event.message.segments),
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
        # 协议消息段 -> OneBot 消息段 (嵌套); 只接受列表
        msg = params.get("message")
        if isinstance(msg, list):
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
