from __future__ import annotations

from typing import Any

from loguru import logger

from onebot_adapter.event import (
    Dispatcher,
    Event,
    MessageEvent,
    NoticeEvent,
    RequestEvent,
)


class TestEventFromRaw:
    def test_message_post_type_returns_message_event(self) -> None:
        data = {
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "user_id": 100,
            "group_id": 200,
            "message": [{"type": "text", "data": {"text": "hi"}}],
            "raw_message": "hi",
            "self_id": 999,
        }
        event = Event.from_raw(data)
        assert isinstance(event, MessageEvent)
        assert event.post_type == "message"
        assert event.message_type == "group"
        assert event.sub_type == "normal"
        assert event.user_id == 100
        assert event.group_id == 200
        assert event.self_id == 999
        assert event.raw_message == "hi"
        assert event.raw is data
        assert len(event.message.segments) == 1
        assert event.message.segments[0].type == "text"

    def test_notice_post_type_returns_notice_event(self) -> None:
        data = {
            "post_type": "notice",
            "notice_type": "group_increase",
            "sub_type": "approve",
            "user_id": 10,
            "group_id": 20,
        }
        event = Event.from_raw(data)
        assert isinstance(event, NoticeEvent)
        assert event.notice_type == "group_increase"
        assert event.sub_type == "approve"
        assert event.user_id == 10
        assert event.group_id == 20
        assert event.raw is data

    def test_request_post_type_returns_request_event(self) -> None:
        data = {
            "post_type": "request",
            "request_type": "friend",
            "sub_type": "add",
            "user_id": 7,
            "comment": "我是谁",
            "group_id": None,
        }
        event = Event.from_raw(data)
        assert isinstance(event, RequestEvent)
        assert event.request_type == "friend"
        assert event.sub_type == "add"
        assert event.user_id == 7
        assert event.comment == "我是谁"
        assert event.group_id is None

    def test_unknown_post_type_returns_base_event(self) -> None:
        data = {"post_type": "something_new", "foo": "bar"}
        event = Event.from_raw(data)
        assert type(event) is Event
        assert event.post_type == "something_new"
        assert event.raw is data

    def test_missing_post_type_returns_base_event(self) -> None:
        data = {"foo": "bar"}
        event = Event.from_raw(data)
        assert type(event) is Event
        assert event.post_type == ""


class TestMessageEventDefaults:
    def test_is_private_and_is_group(self) -> None:
        private = MessageEvent.from_raw(
            {"post_type": "message", "message_type": "private"}
        )
        group = MessageEvent.from_raw({"post_type": "message", "message_type": "group"})
        other = MessageEvent.from_raw(
            {"post_type": "message", "message_type": "unknown"}
        )
        assert private.is_private is True
        assert private.is_group is False
        assert group.is_group is True
        assert group.is_private is False
        assert other.is_private is False
        assert other.is_group is False

    def test_defaults_when_fields_missing(self) -> None:
        event = MessageEvent.from_raw({"post_type": "message"})
        assert event.message_type == ""
        assert event.sub_type == ""
        assert event.user_id == 0
        assert event.group_id is None
        assert event.raw_message == ""
        assert event.self_id == 0
        assert event.message.segments == []


class TestDispatcher:
    async def test_on_registers_and_dispatch_calls_handler(self) -> None:
        disp = Dispatcher()
        received: list[Event] = []

        @disp.on("message")
        async def handler(event: Event) -> None:
            received.append(event)

        event = Event(post_type="message", raw={})
        await disp.dispatch(event)
        assert received == [event]

    def test_on_returns_decorator_that_returns_function(self) -> None:
        disp = Dispatcher()

        @disp.on("message")
        async def handler(event: Event) -> None:
            pass

        assert handler is not None
        # decorator should return the original function unchanged
        assert callable(handler)

    async def test_multiple_handlers_all_called_in_order(self) -> None:
        disp = Dispatcher()
        order: list[str] = []

        @disp.on("notice")
        async def first(event: Event) -> None:
            order.append("first")

        @disp.on("notice")
        async def second(event: Event) -> None:
            order.append("second")

        await disp.dispatch(Event(post_type="notice", raw={}))
        assert order == ["first", "second"]

    async def test_no_handlers_is_noop(self) -> None:
        disp = Dispatcher()
        # should not raise
        await disp.dispatch(Event(post_type="notice", raw={}))

    async def test_handler_exception_is_swallowed_and_does_not_stop_others(
        self,
    ) -> None:
        disp = Dispatcher()
        called: list[str] = []
        logs: list[str] = []

        @disp.on("message")
        async def boom(event: Event) -> None:
            raise ValueError("boom")

        @disp.on("message")
        async def after(event: Event) -> None:
            called.append("after")

        sink_id = logger.add(lambda msg: logs.append(msg), level="ERROR")
        try:
            await disp.dispatch(Event(post_type="message", raw={}))
        finally:
            logger.remove(sink_id)

        assert called == ["after"]
        assert any("handler error" in log for log in logs)

    async def test_handlers_isolated_per_post_type(self) -> None:
        disp = Dispatcher()
        msg_called: list[bool] = []
        notice_called: list[bool] = []

        @disp.on("message")
        async def on_msg(event: Event) -> None:
            msg_called.append(True)

        @disp.on("notice")
        async def on_notice(event: Event) -> None:
            notice_called.append(True)

        await disp.dispatch(Event(post_type="message", raw={}))
        assert msg_called == [True]
        assert notice_called == []

    async def test_handler_can_return_value(self) -> None:
        disp = Dispatcher()

        @disp.on("message")
        async def handler(event: Event) -> str:
            return "ok"

        # dispatch should not choke on a non-None return
        await disp.dispatch(Event(post_type="message", raw={}))

    async def test_handler_receives_correct_event_subtype(self) -> None:
        disp = Dispatcher()
        received: list[Any] = []

        @disp.on("message")
        async def handler(event: Event) -> None:
            received.append(event)

        data = {"post_type": "message", "message_type": "group", "group_id": 5}
        event = Event.from_raw(data)
        await disp.dispatch(event)
        assert isinstance(received[0], MessageEvent)
        assert received[0].is_group is True
