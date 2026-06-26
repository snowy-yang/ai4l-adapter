from __future__ import annotations

import asyncio
import base64
import os
import tempfile
from typing import Any, cast
from unittest.mock import patch

import aiohttp
import msgpack
from aiohttp.test_utils import TestClient, TestServer

from onebot_adapter.bot import Bot
from onebot_adapter.event import Event, MessageEvent, NoticeEvent, RequestEvent
from onebot_adapter.server import Server, _ob_segments_to_proto, _proto_segments_to_ob


def _pack(obj: Any, **kwargs: Any) -> bytes:
    return cast(bytes, msgpack.packb(obj, **kwargs))


def _stub_bot_api(
    bot: Bot, response: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """让 bot.connection.send 自动回响应, 并记录发出的 payload."""
    sent: list[dict[str, Any]] = []
    default = {"status": "ok", "retcode": 0, "data": {}}

    async def fake_send(payload: dict[str, Any]) -> None:
        sent.append(payload)
        resp = dict(response or default)
        resp["echo"] = payload["echo"]
        bot.api.feed_response(resp)

    bot.connection.send = fake_send  # type: ignore[method-assign]
    return sent


class TestObSegmentsToProto:
    async def test_text_segment(self) -> None:
        from onebot_adapter.message import MessageSegment

        result = await _ob_segments_to_proto([MessageSegment.text("hi")])
        assert result == [{"type": "text", "text": "hi"}]

    async def test_at_segment_passed_through_flat(self) -> None:
        from onebot_adapter.message import MessageSegment

        result = await _ob_segments_to_proto([MessageSegment.at(1)])
        assert result == [{"type": "at", "qq": "1"}]

    async def test_reply_segment_passed_through_flat(self) -> None:
        from onebot_adapter.message import MessageSegment

        result = await _ob_segments_to_proto([MessageSegment.reply(99)])
        assert result == [{"type": "reply", "id": "99"}]

    async def test_image_base64_prefix_stripped(self) -> None:
        from onebot_adapter.message import MessageSegment

        b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"
        seg = MessageSegment(type="image", data={"file": f"base64://{b64}"})
        result = await _ob_segments_to_proto([seg])
        assert result == [{"type": "image", "content": b64}]

    async def test_image_url_downloaded_to_base64(self) -> None:
        from onebot_adapter.message import MessageSegment

        raw_bytes = b"\x89PNG fake image data"
        b64_expected = base64.b64encode(raw_bytes).decode("ascii")
        seg = MessageSegment(type="image", data={"file": "https://example.com/a.png"})

        class FakeResp:
            def raise_for_status(self) -> None:
                pass

            async def read(self) -> bytes:
                return raw_bytes

        class FakeCtx:
            async def __aenter__(self) -> FakeResp:
                return FakeResp()

            async def __aexit__(self, *args: Any) -> None:
                pass

        class FakeSession:
            closed = False

            def get(self, url: str) -> FakeCtx:
                return FakeCtx()

            async def close(self) -> None:
                pass

        fake = FakeSession()
        with patch("onebot_adapter.server.aiohttp.ClientSession", return_value=fake):
            result = await _ob_segments_to_proto([seg])
        assert result == [{"type": "image", "content": b64_expected}]

    async def test_image_local_file_read_to_base64(self) -> None:
        from onebot_adapter.message import MessageSegment

        raw_bytes = b"fake file content"
        b64_expected = base64.b64encode(raw_bytes).decode("ascii")
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(raw_bytes)
            path = f.name
        try:
            seg = MessageSegment(type="image", data={"file": path})
            result = await _ob_segments_to_proto([seg])
            assert result == [{"type": "image", "content": b64_expected}]
        finally:
            os.unlink(path)

    async def test_image_unrecognized_file_returns_as_is(self) -> None:
        from onebot_adapter.message import MessageSegment

        seg = MessageSegment(type="image", data={"file": "not-a-real-path"})
        result = await _ob_segments_to_proto([seg])
        assert result == [{"type": "image", "content": "not-a-real-path"}]

    async def test_empty_file_returns_empty_content(self) -> None:
        from onebot_adapter.message import MessageSegment

        seg = MessageSegment(type="image", data={"file": ""})
        result = await _ob_segments_to_proto([seg])
        assert result == [{"type": "image", "content": ""}]

    async def test_video_segment_uses_content(self) -> None:
        from onebot_adapter.message import MessageSegment

        b64 = "AAAAIGZ0cbsAAAAA"
        seg = MessageSegment(type="video", data={"file": f"base64://{b64}"})
        result = await _ob_segments_to_proto([seg])
        assert result == [{"type": "video", "content": b64}]

    async def test_mixed_segments(self) -> None:
        from onebot_adapter.message import MessageSegment

        b64 = "iVBORw0KGgo="
        segments = [
            MessageSegment.text("hi "),
            MessageSegment.at(1),
            MessageSegment(type="image", data={"file": f"base64://{b64}"}),
        ]
        result = await _ob_segments_to_proto(segments)
        assert result == [
            {"type": "text", "text": "hi "},
            {"type": "at", "qq": "1"},
            {"type": "image", "content": b64},
        ]

    async def test_empty_segments(self) -> None:
        result = await _ob_segments_to_proto([])
        assert result == []


class TestProtoSegmentsToOb:
    def test_text_segment(self) -> None:
        result = _proto_segments_to_ob([{"type": "text", "text": "hi"}])
        assert result == [{"type": "text", "data": {"text": "hi"}}]

    def test_image_content_becomes_base64_file(self) -> None:
        result = _proto_segments_to_ob([{"type": "image", "content": "iVBORw0KGgo="}])
        assert result == [{"type": "image", "data": {"file": "base64://iVBORw0KGgo="}}]

    def test_image_content_with_extra_fields(self) -> None:
        result = _proto_segments_to_ob(
            [{"type": "image", "content": "abc", "cache": 0}]
        )
        assert result == [
            {"type": "image", "data": {"file": "base64://abc", "cache": 0}}
        ]

    def test_at_segment_flat_to_nested(self) -> None:
        result = _proto_segments_to_ob([{"type": "at", "qq": "1"}])
        assert result == [{"type": "at", "data": {"qq": "1"}}]

    def test_reply_segment(self) -> None:
        result = _proto_segments_to_ob([{"type": "reply", "id": "99"}])
        assert result == [{"type": "reply", "data": {"id": "99"}}]

    def test_video_content_becomes_base64_file(self) -> None:
        result = _proto_segments_to_ob([{"type": "video", "content": "AAAA"}])
        assert result == [{"type": "video", "data": {"file": "base64://AAAA"}}]

    def test_empty_list(self) -> None:
        assert _proto_segments_to_ob([]) == []

    def test_roundtrip_text(self) -> None:
        proto = [{"type": "text", "text": "hello"}]
        ob = _proto_segments_to_ob(proto)
        assert ob == [{"type": "text", "data": {"text": "hello"}}]

    def test_segment_without_content_flat_to_nested(self) -> None:
        result = _proto_segments_to_ob([{"type": "poke", "id": "5", "name": "x"}])
        assert result == [{"type": "poke", "data": {"id": "5", "name": "x"}}]


class TestTranslate:
    async def test_message_group_event(self) -> None:
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "group",
                "user_id": 1,
                "group_id": 2,
                "message": "hi",
                "message_id": 99,
                "self_id": 100,
            }
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["type"] == "message"
        assert proto["data"] == {
            "kind": "group",
            "user_id": 1,
            "group_id": 2,
            "message": [{"type": "text", "text": "hi"}],
            "message_id": 99,
            "self_id": 100,
        }

    async def test_message_private_kind_and_none_group(self) -> None:
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 1,
                "message": "x",
            }
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["data"]["kind"] == "private"
        assert proto["data"]["group_id"] is None

    async def test_message_text_and_at_segments(self) -> None:
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "group",
                "message": [
                    {"type": "text", "data": {"text": "hi "}},
                    {"type": "at", "data": {"qq": "1"}},
                ],
            }
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["data"]["message"] == [
            {"type": "text", "text": "hi "},
            {"type": "at", "qq": "1"},
        ]

    async def test_message_image_base64_prefix(self) -> None:
        b64 = "iVBORw0KGgo="
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 1,
                "message": [{"type": "image", "data": {"file": f"base64://{b64}"}}],
            }
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["data"]["message"] == [{"type": "image", "content": b64}]

    async def test_message_empty_segments(self) -> None:
        event = MessageEvent.from_raw(
            {"post_type": "message", "message_type": "private", "user_id": 1}
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["data"]["message"] == []

    async def test_notice_event(self) -> None:
        event = NoticeEvent.from_raw(
            {
                "post_type": "notice",
                "notice_type": "poke",
                "sub_type": "abc",
                "user_id": 3,
                "group_id": 4,
            }
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["type"] == "notice"
        assert proto["data"] == {
            "notice_type": "poke",
            "sub_type": "abc",
            "user_id": 3,
            "group_id": 4,
        }

    async def test_request_event(self) -> None:
        event = RequestEvent.from_raw(
            {
                "post_type": "request",
                "request_type": "friend",
                "sub_type": "add",
                "user_id": 5,
                "comment": "hi",
            }
        )
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["type"] == "request"
        assert proto["data"]["request_type"] == "friend"
        assert proto["data"]["comment"] == "hi"
        assert proto["data"]["group_id"] is None

    async def test_unknown_event_falls_back_to_raw(self) -> None:
        event = Event(post_type="weird", raw={"foo": "bar"})
        server = Server(Bot("ws://x"))
        proto = await server._translate(event)
        assert proto["type"] == "weird"
        assert proto["data"] == {"foo": "bar"}


class TestServerConstruction:
    def test_registers_handler_for_each_post_type(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        for pt in ("message", "notice", "request"):
            assert server._on_event in bot.dispatcher._handlers[pt]

    def test_default_host_port_and_paths(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        assert server.host == "127.0.0.1"
        assert server.port == 8080
        assert server.events_path == "/events"
        assert server.action_path == "/action"
        assert server.ws_path == "/ws"

    def test_custom_paths(self) -> None:
        bot = Bot("ws://x")
        server = Server(
            bot,
            host="0.0.0.0",
            port=9000,
            events_path="/e",
            action_path="/a",
            ws_path="/w",
        )
        assert server.host == "0.0.0.0"
        assert server.port == 9000
        assert server.events_path == "/e"
        assert server.action_path == "/a"
        assert server.ws_path == "/w"

    def test_build_app_has_all_routes(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        app = server._build_app()
        resources = [r.resource for r in app.router.routes()]
        paths = {r.canonical for r in resources if r is not None}
        assert "/events" in paths
        assert "/action" in paths
        assert "/ws" in paths


class TestOnEventDispatch:
    async def test_event_pushed_to_all_subscribers(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        q1: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        q2: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        server._subscribers.add(q1)
        server._subscribers.add(q2)
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 1,
                "message": "hi",
            }
        )
        await server._on_event(event)
        assert q1.qsize() == 1
        assert q2.qsize() == 1
        assert q1.get_nowait()["type"] == "message"
        assert q2.get_nowait()["data"]["user_id"] == 1

    async def test_no_subscribers_is_noop(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        event = NoticeEvent.from_raw({"post_type": "notice", "notice_type": "x"})
        # should not raise
        await server._on_event(event)

    async def test_dispatched_via_bot_dispatcher(self) -> None:
        """Server 注册到 dispatcher, 所以 bot._on_message(原始) 会触发 _on_event."""
        bot = Bot("ws://x")
        server = Server(bot)
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        server._subscribers.add(q)
        raw = {"post_type": "notice", "notice_type": "poke", "user_id": 9}
        await bot._on_message(raw)
        assert q.qsize() == 1
        assert q.get_nowait()["data"]["notice_type"] == "poke"


class TestHandleAction:
    async def test_valid_action_returns_ok_with_data(self) -> None:
        bot = Bot("ws://x")
        _stub_bot_api(bot, {"retcode": 0, "data": {"message_id": 7}})
        server = Server(bot)
        status, payload = await server._handle_action(
            {
                "action": "send_msg",
                "params": {"group_id": 1, "message": [{"type": "text", "text": "hi"}]},
            }
        )
        assert status == 200
        assert payload == {"ok": True, "data": {"message_id": 7}, "error": None}

    async def test_text_segment_translated_to_onebot(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action(
            {
                "action": "send_msg",
                "params": {
                    "user_id": 1,
                    "message": [{"type": "text", "text": "hello"}],
                },
            }
        )
        assert sent[0]["params"]["message"] == [
            {"type": "text", "data": {"text": "hello"}}
        ]

    async def test_image_content_translated_to_base64_file(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action(
            {
                "action": "send_msg",
                "params": {
                    "user_id": 1,
                    "message": [{"type": "image", "content": "iVBORw0KGgo="}],
                },
            }
        )
        assert sent[0]["params"]["message"] == [
            {"type": "image", "data": {"file": "base64://iVBORw0KGgo="}}
        ]

    async def test_image_content_with_extra_fields(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action(
            {
                "action": "send_msg",
                "params": {
                    "user_id": 1,
                    "message": [{"type": "image", "content": "abc", "cache": 0}],
                },
            }
        )
        assert sent[0]["params"]["message"] == [
            {"type": "image", "data": {"file": "base64://abc", "cache": 0}}
        ]

    async def test_at_segment_translated_to_onebot(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action(
            {
                "action": "send_msg",
                "params": {
                    "user_id": 1,
                    "message": [
                        {"type": "text", "text": "hi "},
                        {"type": "at", "qq": "1"},
                    ],
                },
            }
        )
        assert sent[0]["params"]["message"] == [
            {"type": "text", "data": {"text": "hi "}},
            {"type": "at", "data": {"qq": "1"}},
        ]

    async def test_empty_message_list_passes_through(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action(
            {"action": "send_msg", "params": {"user_id": 1, "message": []}}
        )
        assert sent[0]["params"]["message"] == []

    async def test_non_list_message_passed_unchanged(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        # 字符串不再特殊处理, 原样透传 (OneBot 会拒绝)
        await server._handle_action(
            {"action": "send_msg", "params": {"user_id": 1, "message": "raw string"}}
        )
        assert sent[0]["params"]["message"] == "raw string"

    async def test_no_params_defaults_to_empty(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action({"action": "get_login_info"})
        assert sent[0]["action"] == "get_login_info"
        assert sent[0]["params"] == {}

    async def test_params_null_treated_as_empty(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action({"action": "get_login_info", "params": None})
        assert sent[0]["params"] == {}

    async def test_missing_action_returns_400(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        status, payload = await server._handle_action({"params": {}})
        assert status == 400
        assert payload["ok"] is False
        assert payload["error"]["message"] == "missing action"

    async def test_non_dict_body_returns_400(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        status, _ = await server._handle_action("not a dict")
        assert status == 400

    async def test_api_error_returns_error_body(self) -> None:
        bot = Bot("ws://x")

        async def fake_send(payload: dict[str, Any]) -> None:
            bot.api.feed_response(
                {"retcode": 1000, "msg": "参数错误", "echo": payload["echo"]}
            )

        bot.connection.send = fake_send  # type: ignore[method-assign]
        server = Server(bot)
        status, payload = await server._handle_action(
            {
                "action": "send_msg",
                "params": {"message": [{"type": "text", "text": "x"}]},
            }
        )
        assert status == 200
        assert payload["ok"] is False
        assert payload["error"] == {"retcode": 1000, "message": "参数错误"}

    async def test_send_failure_returns_500(self) -> None:
        bot = Bot("ws://x")

        async def fake_send(payload: dict[str, Any]) -> None:
            raise ConnectionError("WebSocket 未连接")

        bot.connection.send = fake_send  # type: ignore[method-assign]
        server = Server(bot)
        status, payload = await server._handle_action({"action": "get_login_info"})
        assert status == 500
        assert payload["ok"] is False
        assert "WebSocket 未连接" in payload["error"]["message"]


class TestActionEndpointIntegration:
    async def test_post_action_returns_json_response(self) -> None:
        bot = Bot("ws://x")
        _stub_bot_api(bot, {"retcode": 0, "data": {"message_id": 7}})
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post(
                "/action",
                json={
                    "action": "send_msg",
                    "params": {
                        "group_id": 1,
                        "message": [{"type": "text", "text": "hi"}],
                    },
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body == {"ok": True, "data": {"message_id": 7}, "error": None}
        finally:
            await client.close()

    async def test_post_invalid_json_returns_400(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post("/action", data="not json")
            assert resp.status == 400
            body = await resp.json()
            assert body["ok"] is False
            assert body["error"]["message"] == "invalid json"
        finally:
            await client.close()

    async def test_post_missing_action_returns_400(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            resp = await client.post("/action", json={"params": {}})
            assert resp.status == 400
        finally:
            await client.close()


class TestWebsocketEndpoint:
    async def test_action_roundtrip_msgpack(self) -> None:
        bot = Bot("ws://x")
        _stub_bot_api(bot, {"retcode": 0, "data": {"message_id": 42}})
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            req = {
                "action": "send_msg",
                "params": {"group_id": 1, "message": [{"type": "text", "text": "hi"}]},
            }
            await ws.send_bytes(_pack(req, use_bin_type=True))
            msg = await ws.receive(timeout=2)
            assert msg.type == aiohttp.WSMsgType.BINARY
            resp = msgpack.unpackb(cast(bytes, msg.data), raw=False)
            assert resp == {"ok": True, "data": {"message_id": 42}, "error": None}
            await ws.close()
        finally:
            await client.close()

    async def test_action_missing_action_returns_error(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_bytes(_pack({"params": {}}, use_bin_type=True))
            msg = await ws.receive(timeout=2)
            assert msg.type == aiohttp.WSMsgType.BINARY
            resp = msgpack.unpackb(cast(bytes, msg.data), raw=False)
            assert resp["ok"] is False
            assert resp["error"]["message"] == "missing action"
            await ws.close()
        finally:
            await client.close()

    async def test_action_api_error_returns_error_body(self) -> None:
        bot = Bot("ws://x")

        async def fake_send(payload: dict[str, Any]) -> None:
            bot.api.feed_response(
                {"retcode": 1000, "msg": "参数错误", "echo": payload["echo"]}
            )

        bot.connection.send = fake_send  # type: ignore[method-assign]
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            req = {
                "action": "send_msg",
                "params": {"message": [{"type": "text", "text": "x"}]},
            }
            await ws.send_bytes(_pack(req, use_bin_type=True))
            msg = await ws.receive(timeout=2)
            resp = msgpack.unpackb(cast(bytes, msg.data), raw=False)
            assert resp["ok"] is False
            assert resp["error"] == {"retcode": 1000, "message": "参数错误"}
            await ws.close()
        finally:
            await client.close()

    async def test_event_pushed_as_msgpack(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            # 等 push_task 注册到 subscribers
            await asyncio.sleep(0.1)
            # 触发一个 notice 事件 (无媒体, 无需网络)
            raw = {"post_type": "notice", "notice_type": "poke", "user_id": 9}
            await bot._on_message(raw)
            msg = await ws.receive(timeout=2)
            assert msg.type == aiohttp.WSMsgType.BINARY
            proto = msgpack.unpackb(cast(bytes, msg.data), raw=False)
            assert proto["type"] == "notice"
            assert proto["data"]["notice_type"] == "poke"
            assert proto["data"]["user_id"] == 9
            await ws.close()
        finally:
            await client.close()

    async def test_message_event_with_text_pushed_as_msgpack(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            await asyncio.sleep(0.1)
            raw = {
                "post_type": "message",
                "message_type": "group",
                "user_id": 1,
                "group_id": 2,
                "message": "hello",
                "message_id": 55,
            }
            await bot._on_message(raw)
            msg = await ws.receive(timeout=2)
            proto = msgpack.unpackb(cast(bytes, msg.data), raw=False)
            assert proto["type"] == "message"
            assert proto["data"]["kind"] == "group"
            assert proto["data"]["message"] == [{"type": "text", "text": "hello"}]
            assert proto["data"]["message_id"] == 55
            await ws.close()
        finally:
            await client.close()

    async def test_action_with_image_content_translated(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            req = {
                "action": "send_msg",
                "params": {
                    "user_id": 1,
                    "message": [{"type": "image", "content": "iVBORw0KGgo="}],
                },
            }
            await ws.send_bytes(_pack(req, use_bin_type=True))
            await ws.receive(timeout=2)
            assert sent[0]["params"]["message"] == [
                {"type": "image", "data": {"file": "base64://iVBORw0KGgo="}}
            ]
            await ws.close()
        finally:
            await client.close()

    async def test_text_frame_ignored(self) -> None:
        bot = Bot("ws://x")
        _stub_bot_api(bot)
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            # 发 TEXT 帧, 应被忽略 (不发响应)
            await ws.send_str("not msgpack")
            # 发一个正确的 BINARY 帧, 确认连接仍正常
            await ws.send_bytes(_pack({"action": "get_login_info"}, use_bin_type=True))
            msg = await ws.receive(timeout=2)
            assert msg.type == aiohttp.WSMsgType.BINARY
            resp = msgpack.unpackb(cast(bytes, msg.data), raw=False)
            assert resp["ok"] is True
            await ws.close()
        finally:
            await client.close()

    async def test_multiple_actions_sequential(self) -> None:
        bot = Bot("ws://x")
        _stub_bot_api(bot, {"retcode": 0, "data": {"user_id": 1}})
        server = Server(bot)
        client = TestClient(TestServer(server._build_app()))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            for _ in range(3):
                await ws.send_bytes(
                    _pack({"action": "get_login_info"}, use_bin_type=True)
                )
                msg = await ws.receive(timeout=2)
                resp = msgpack.unpackb(cast(bytes, msg.data), raw=False)
                assert resp["ok"] is True
            await ws.close()
        finally:
            await client.close()
