from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shutil as _shutil
from typing import Any, cast

import aiohttp
import msgpack
from aiohttp import web
from loguru import logger

from .api import ApiError
from .bot import Bot
from .event import Event, MessageEvent, NoticeEvent, RequestEvent
from .message import MessageSegment

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


def _packb(obj: Any) -> bytes:
    return cast(bytes, msgpack.packb(obj, use_bin_type=True))


def _terminal_width() -> int:
    return _shutil.get_terminal_size((80, 20)).columns


class Server:
    """协议桥, 把 OneBot 翻译成极简协议对外暴露.

    两条通道, 共用同一套事件翻译与指令处理逻辑:

    - HTTP (JSON): GET events_path 收事件 (SSE), POST action_path 发指令.
    - WebSocket (msgpack): WS ws_path 连上后, 事件以 msgpack 二进制帧推出,
      指令以 msgpack 二进制帧发入并收到 msgpack 响应.

    协议消息段 (统一对象, 不含 CQ 码):
        text:  {"type":"text", "text":"hi"}
        媒体:  {"type":"image", "content":"<base64>"}   (image/video/audio/file/voice)
        其他:  {"type":"at", "qq":"1"}                   (at/reply 等扁平透传)

    协议事件 (只有 message 和 notice 两种, request 并入 notice):
        message: {"detail","sub","user_id","group_id","message","message_id","self_id"}
        notice:  {"detail","sub","user_id","group_id"[,"comment"]}

    协议指令:
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
        ws_path: str = "/ws",
    ) -> None:
        self.bot = bot
        self.host = host
        self.port = port
        self.events_path = events_path
        self.action_path = action_path
        self.ws_path = ws_path
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._runner: web.AppRunner | None = None

        # 挂到 dispatcher 的三类事件上, 统一走翻译后推给订阅者
        for post_type in ("message", "notice", "request"):
            bot.dispatcher.on(post_type)(self._on_event)

    # ---- 事件翻译 + 分发 ----

    async def _on_event(self, event: Event) -> None:
        proto = await self._translate(event)
        logger.debug("事件 {}: {}", proto["type"], proto["data"])
        self._log_event_summary(proto)
        for q in self._subscribers:
            q.put_nowait(proto)

    @staticmethod
    def _log_event_summary(proto: dict[str, Any]) -> None:
        etype = proto["type"]
        data = proto["data"]
        width = _terminal_width()
        budget = width or 80
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

    async def _translate(self, event: Event) -> dict[str, Any]:
        if isinstance(event, MessageEvent):
            return {
                "type": "message",
                "data": {
                    "detail": event.message_type,
                    "sub": event.sub_type,
                    "user_id": event.user_id,
                    "group_id": event.group_id,
                    "message": await _ob_segments_to_proto(event.message.segments),
                    "message_id": event.raw.get("message_id"),
                    "self_id": event.self_id,
                },
            }
        if isinstance(event, (NoticeEvent, RequestEvent)):
            detail = (
                event.notice_type
                if isinstance(event, NoticeEvent)
                else event.request_type
            )
            data: dict[str, Any] = {
                "detail": detail,
                "sub": event.sub_type,
                "user_id": event.user_id,
                "group_id": event.group_id,
            }
            if isinstance(event, RequestEvent):
                data["comment"] = event.comment
            return {"type": "notice", "data": data}
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

    # ---- WebSocket handler (msgpack) ----

    async def _ws_push_events(
        self, ws: web.WebSocketResponse, queue: asyncio.Queue[dict[str, Any]]
    ) -> None:
        """从订阅队列取事件, msgpack 编码后推给 WS 客户端."""
        while True:
            proto = await queue.get()
            if ws.closed:
                break
            try:
                await ws.send_bytes(_packb(proto))
            except (ConnectionResetError, BrokenPipeError):
                break

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subscribers.add(queue)
        push_task = asyncio.create_task(self._ws_push_events(ws, queue))
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    if msg.data is None:
                        continue
                    body = msgpack.unpackb(msg.data, raw=False)
                    _, payload = await self._handle_action(body)
                    if not ws.closed:
                        await ws.send_bytes(_packb(payload))
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        finally:
            push_task.cancel()
            self._subscribers.discard(queue)
            with contextlib.suppress(asyncio.CancelledError):
                await push_task
        return ws

    # ---- 生命周期 ----

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(self.events_path, self._sse_handler)
        app.router.add_post(self.action_path, self._action_handler)
        app.router.add_get(self.ws_path, self._ws_handler)
        return app

    async def _start_http(self) -> None:
        app = self._build_app()
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()

    async def run(self) -> None:
        """启动 HTTP + SSE + WS 服务, 并阻塞运行 OneBot WS 连接."""
        await self._start_http()
        logger.info("协议服务监听 http://{}:{}", self.host, self.port)
        await self.bot.run()

    async def close(self) -> None:
        await self.bot.close()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
