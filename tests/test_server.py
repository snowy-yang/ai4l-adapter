from __future__ import annotations

import asyncio
from typing import Any

from aiohttp.test_utils import TestClient, TestServer

from onebot_adapter.bot import Bot
from onebot_adapter.event import Event, MessageEvent, NoticeEvent, RequestEvent
from onebot_adapter.server import Server


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


class TestTranslate:
    def test_message_group_event(self) -> None:
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
        proto = Server._translate(event)
        assert proto["type"] == "message"
        assert proto["data"] == {
            "kind": "group",
            "user_id": 1,
            "group_id": 2,
            "message": "hi",
            "message_id": 99,
            "self_id": 100,
        }

    def test_message_private_kind_and_none_group(self) -> None:
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 1,
                "message": "x",
            }
        )
        proto = Server._translate(event)
        assert proto["data"]["kind"] == "private"
        assert proto["data"]["group_id"] is None

    def test_message_str_uses_message_repr(self) -> None:
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
        proto = Server._translate(event)
        assert proto["data"]["message"] == "hi [at:{'qq': '1'}]"

    def test_message_missing_message_id_is_none(self) -> None:
        event = MessageEvent.from_raw(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 1,
                "message": "x",
            }
        )
        proto = Server._translate(event)
        assert proto["data"]["message_id"] is None

    def test_notice_event(self) -> None:
        event = NoticeEvent.from_raw(
            {
                "post_type": "notice",
                "notice_type": "poke",
                "sub_type": "abc",
                "user_id": 3,
                "group_id": 4,
            }
        )
        proto = Server._translate(event)
        assert proto["type"] == "notice"
        assert proto["data"] == {
            "notice_type": "poke",
            "sub_type": "abc",
            "user_id": 3,
            "group_id": 4,
        }

    def test_request_event(self) -> None:
        event = RequestEvent.from_raw(
            {
                "post_type": "request",
                "request_type": "friend",
                "sub_type": "add",
                "user_id": 5,
                "comment": "hi",
            }
        )
        proto = Server._translate(event)
        assert proto["type"] == "request"
        assert proto["data"]["request_type"] == "friend"
        assert proto["data"]["comment"] == "hi"
        assert proto["data"]["group_id"] is None

    def test_unknown_event_falls_back_to_raw(self) -> None:
        event = Event(post_type="weird", raw={"foo": "bar"})
        proto = Server._translate(event)
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

    def test_custom_paths(self) -> None:
        bot = Bot("ws://x")
        server = Server(
            bot, host="0.0.0.0", port=9000, events_path="/e", action_path="/a"
        )
        assert server.host == "0.0.0.0"
        assert server.port == 9000
        assert server.events_path == "/e"
        assert server.action_path == "/a"

    def test_build_app_has_both_routes(self) -> None:
        bot = Bot("ws://x")
        server = Server(bot)
        app = server._build_app()
        resources = [r.resource for r in app.router.routes()]
        paths = {r.canonical for r in resources if r is not None}
        assert "/events" in paths
        assert "/action" in paths


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
            {"action": "send_msg", "params": {"group_id": 1, "message": "hi"}}
        )
        assert status == 200
        assert payload == {"ok": True, "data": {"message_id": 7}, "error": None}

    async def test_string_message_normalized_to_text_segment(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        await server._handle_action(
            {"action": "send_msg", "params": {"user_id": 1, "message": "hello"}}
        )
        assert sent[0]["params"]["message"] == [
            {"type": "text", "data": {"text": "hello"}}
        ]

    async def test_list_message_passed_through(self) -> None:
        bot = Bot("ws://x")
        sent = _stub_bot_api(bot)
        server = Server(bot)
        segs = [{"type": "image", "data": {"file": "f.png"}}]
        await server._handle_action(
            {"action": "send_msg", "params": {"user_id": 1, "message": segs}}
        )
        assert sent[0]["params"]["message"] == segs

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
            {"action": "send_msg", "params": {"message": "x"}}
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
                json={"action": "send_msg", "params": {"group_id": 1, "message": "hi"}},
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
