from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onebot_adapter.bot import Bot, _normalize_message
from onebot_adapter.event import Event, MessageEvent
from onebot_adapter.message import Message, MessageSegment


def _stub_connection(
    bot: Bot, response: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Replace bot.connection.send with a recording auto-responder.

    Returns the list of payloads that were sent over the connection.
    """
    sent: list[dict[str, Any]] = []
    default_response: dict[str, Any] = {"status": "ok", "retcode": 0, "data": {}}

    async def fake_send(payload: dict[str, Any]) -> None:
        sent.append(payload)
        resp = dict(response or default_response)
        resp["echo"] = payload["echo"]
        bot.api.feed_response(resp)

    # monkeypatch the real Connection's send method
    bot.connection.send = fake_send  # type: ignore[method-assign]
    return sent


def _stub_close(bot: Bot) -> list[bool]:
    closed: list[bool] = []

    async def fake_close() -> None:
        closed.append(True)

    bot.connection.close = fake_close  # type: ignore[method-assign]
    return closed


class TestNormalizeMessage:
    def test_str_becomes_single_text_segment(self) -> None:
        result = _normalize_message("hello")
        assert result == [{"type": "text", "data": {"text": "hello"}}]

    def test_message_object_uses_to_dict(self) -> None:
        msg = Message([MessageSegment.text("a"), MessageSegment.at(1)])
        assert _normalize_message(msg) == [
            {"type": "text", "data": {"text": "a"}},
            {"type": "at", "data": {"qq": "1"}},
        ]

    def test_list_of_segments(self) -> None:
        segs = [MessageSegment.text("x"), MessageSegment.reply(3)]
        assert _normalize_message(segs) == [
            {"type": "text", "data": {"text": "x"}},
            {"type": "reply", "data": {"id": "3"}},
        ]

    def test_empty_str(self) -> None:
        assert _normalize_message("") == [{"type": "text", "data": {"text": ""}}]


class TestSendMsg:
    async def test_group_id_takes_priority_over_user_id(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        await bot.send_msg(user_id=10, group_id=20, message="hi")
        assert len(sent) == 1
        assert sent[0]["action"] == "send_msg"
        assert sent[0]["params"] == {
            "group_id": 20,
            "message": [{"type": "text", "data": {"text": "hi"}}],
        }
        # user_id should NOT be present when group_id is given
        assert "user_id" not in sent[0]["params"]

    async def test_user_id_used_when_no_group_id(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        await bot.send_msg(user_id=10, message="hi")
        assert sent[0]["params"] == {
            "user_id": 10,
            "message": [{"type": "text", "data": {"text": "hi"}}],
        }

    async def test_only_message_when_neither_id_given(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        await bot.send_msg(message="hi")
        assert sent[0]["params"] == {
            "message": [{"type": "text", "data": {"text": "hi"}}]
        }

    async def test_message_object_normalized(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        msg = Message([MessageSegment.at(5), MessageSegment.text(" go")])
        await bot.send_msg(user_id=1, message=msg)
        assert sent[0]["params"]["message"] == [
            {"type": "at", "data": {"qq": "5"}},
            {"type": "text", "data": {"text": " go"}},
        ]

    async def test_returns_full_response(self) -> None:
        bot = Bot("ws://example")
        _stub_connection(bot, {"retcode": 0, "data": {"message_id": 7}})
        result = await bot.send_msg(user_id=1, message="hi")
        assert result["data"] == {"message_id": 7}


class TestSendPrivateMsg:
    async def test_calls_send_private_msg_with_user_id(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        await bot.send_private_msg(123, "hello")
        assert sent[0]["action"] == "send_private_msg"
        assert sent[0]["params"] == {
            "user_id": 123,
            "message": [{"type": "text", "data": {"text": "hello"}}],
        }

    async def test_accepts_message_object(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        await bot.send_private_msg(1, Message([MessageSegment.image("f.png")]))
        assert sent[0]["params"]["message"] == [
            {"type": "image", "data": {"file": "f.png"}}
        ]


class TestSendGroupMsg:
    async def test_calls_send_group_msg_with_group_id(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot)
        await bot.send_group_msg(456, "hello")
        assert sent[0]["action"] == "send_group_msg"
        assert sent[0]["params"] == {
            "group_id": 456,
            "message": [{"type": "text", "data": {"text": "hello"}}],
        }


class TestGetLoginInfo:
    async def test_calls_get_login_info_with_no_params(self) -> None:
        bot = Bot("ws://example")
        sent = _stub_connection(bot, {"retcode": 0, "data": {"user_id": 1}})
        result = await bot.get_login_info()
        assert sent[0]["action"] == "get_login_info"
        assert sent[0]["params"] == {}
        assert result["data"] == {"user_id": 1}


class TestOnMessageRouting:
    async def test_on_message_event_dispatched_to_handler(self) -> None:
        bot = Bot("ws://example")
        received: list[Event] = []

        @bot.on_message()
        async def handler(event: Event) -> None:
            received.append(event)

        raw = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 5,
            "message": "hi",
        }
        await bot._on_message(raw)
        assert len(received) == 1
        assert isinstance(received[0], MessageEvent)
        assert received[0].is_private is True
        assert received[0].raw is raw

    async def test_on_notice_event_dispatched(self) -> None:
        bot = Bot("ws://example")
        received: list[Event] = []

        @bot.on_notice()
        async def handler(event: Event) -> None:
            received.append(event)

        await bot._on_message({"post_type": "notice", "notice_type": "poke"})
        assert len(received) == 1
        assert received[0].post_type == "notice"

    async def test_on_request_event_dispatched(self) -> None:
        bot = Bot("ws://example")
        received: list[Event] = []

        @bot.on_request()
        async def handler(event: Event) -> None:
            received.append(event)

        await bot._on_message({"post_type": "request", "request_type": "friend"})
        assert len(received) == 1
        assert received[0].post_type == "request"

    async def test_non_event_data_routed_to_api_feed_response(self) -> None:
        bot = Bot("ws://example")
        # use a non-feeding send so the future stays pending and we can
        # route the response back through _on_message ourselves
        sent: list[dict[str, Any]] = []

        async def noop_send(payload: dict[str, Any]) -> None:
            sent.append(payload)

        bot.connection.send = noop_send  # type: ignore[method-assign]
        # start an api call to create a pending future
        task = asyncio.create_task(bot.get_login_info())
        await asyncio.sleep(0)
        echo = next(iter(bot.api._futures))
        # feed the matching response through _on_message (no post_type)
        await bot._on_message({"retcode": 0, "data": {"user_id": 9}, "echo": echo})
        result = await task
        assert result["data"] == {"user_id": 9}
        assert sent[0]["action"] == "get_login_info"


class TestDecorators:
    def test_on_message_returns_decorator(self) -> None:
        bot = Bot("ws://example")

        @bot.on_message()
        async def handler(event: Event) -> None:
            pass

        assert "message" in bot.dispatcher._handlers
        assert handler in bot.dispatcher._handlers["message"]

    def test_on_notice_returns_decorator(self) -> None:
        bot = Bot("ws://example")

        @bot.on_notice()
        async def handler(event: Event) -> None:
            pass

        assert handler in bot.dispatcher._handlers["notice"]

    def test_on_request_returns_decorator(self) -> None:
        bot = Bot("ws://example")

        @bot.on_request()
        async def handler(event: Event) -> None:
            pass

        assert handler in bot.dispatcher._handlers["request"]


class TestClose:
    async def test_close_cancels_api_futures_and_closes_connection(self) -> None:
        bot = Bot("ws://example")

        async def noop_send(payload: dict[str, Any]) -> None:
            pass

        bot.connection.send = noop_send  # type: ignore[method-assign]
        closed = _stub_close(bot)

        # create a pending api call so cancel_all has work to do
        task = asyncio.create_task(bot.get_login_info())
        await asyncio.sleep(0)
        assert len(bot.api._futures) == 1

        await bot.close()
        assert closed == [True]
        assert bot.api._futures == {}
        with pytest.raises(ConnectionError):
            await task

    async def test_close_with_no_pending_futures(self) -> None:
        bot = Bot("ws://example")
        closed = _stub_close(bot)
        await bot.close()
        assert closed == [True]
        assert bot.api._futures == {}


class TestBotConstruction:
    def test_wires_connection_to_on_message(self) -> None:
        bot = Bot("ws://example")
        # bound methods are fresh objects per access, so compare the
        # underlying function and instance instead of using `is`
        callback = bot.connection._on_message
        assert callback is not None
        assert callback.__func__ is Bot._on_message  # type: ignore[attr-defined]
        assert callback.__self__ is bot  # type: ignore[attr-defined]

    def test_binds_api_send_to_bot_send(self) -> None:
        bot = Bot("ws://example")
        send = bot.api._send
        assert send is not None
        assert send.__func__ is Bot._send  # type: ignore[attr-defined]
        assert send.__self__ is bot  # type: ignore[attr-defined]

    def test_passes_access_token_and_reconnect(self) -> None:
        bot = Bot(
            "ws://example",
            access_token="tok",
            reconnect_interval=5.0,
        )
        assert bot.connection.access_token == "tok"
        assert bot.connection.reconnect_interval == 5.0
