from __future__ import annotations

import asyncio
from typing import Any

import pytest

from onebot_adapter.api import ApiCaller, ApiError


def _make_auto_responder(
    api: ApiCaller, response: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Build a send stub that records sent payloads and feeds a response back.

    The response's echo is copied from the sent payload so call() can resolve.
    """
    sent: list[dict[str, Any]] = []
    default_response: dict[str, Any] = {"status": "ok", "retcode": 0, "data": {}}

    async def send(payload: dict[str, Any]) -> None:
        sent.append(payload)
        resp = dict(response or default_response)
        resp["echo"] = payload["echo"]
        api.feed_response(resp)

    api.bind_send(send)
    return sent


class TestApiCallerBasics:
    def test_init_has_empty_futures_and_no_send(self) -> None:
        api = ApiCaller()
        assert api._futures == {}
        assert api._send is None

    def test_bind_send_sets_send(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            pass

        api.bind_send(send)
        assert api._send is send

    async def test_call_without_send_raises_runtime_error(self) -> None:
        api = ApiCaller()
        with pytest.raises(RuntimeError, match="未绑定发送函数"):
            await api.call("send_msg", message="x")


class TestFeedResponse:
    async def test_call_resolves_with_full_response_when_retcode_zero(self) -> None:
        api = ApiCaller()
        sent = _make_auto_responder(
            api, {"status": "ok", "retcode": 0, "data": {"message_id": 42}}
        )
        result = await api.call("send_msg", message="hi")
        assert result["data"] == {"message_id": 42}
        assert result["retcode"] == 0
        # the future should be removed from the pending dict
        assert len(sent) == 1
        assert sent[0]["action"] == "send_msg"
        assert sent[0]["params"] == {"message": "hi"}
        assert "echo" in sent[0]
        assert api._futures == {}

    async def test_call_payload_has_unique_echo_each_call(self) -> None:
        api = ApiCaller()
        sent = _make_auto_responder(api)
        await api.call("get_login_info")
        await api.call("get_login_info")
        assert sent[0]["echo"] != sent[1]["echo"]

    async def test_feed_response_returns_true_on_match(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            pass

        api.bind_send(send)
        # kick off a call to register a future
        task = asyncio.create_task(api.call("send_msg", message="x"))
        await asyncio.sleep(0)  # let send run and register the future
        echo = next(iter(api._futures))
        matched = api.feed_response({"retcode": 0, "data": {}, "echo": echo})
        assert matched is True
        await task

    def test_feed_response_returns_false_when_echo_missing(self) -> None:
        api = ApiCaller()
        assert api.feed_response({"retcode": 0, "data": {}}) is False

    def test_feed_response_returns_false_when_echo_unknown(self) -> None:
        api = ApiCaller()
        assert api.feed_response({"retcode": 0, "data": {}, "echo": "nope"}) is False

    def test_feed_response_returns_false_when_future_already_done(self) -> None:
        api = ApiCaller()
        loop = asyncio.new_event_loop()
        try:
            fut: asyncio.Future[dict[str, Any]] = loop.create_future()
            fut.set_result({"retcode": 0})
            api._futures["echo-done"] = fut
            assert (
                api.feed_response({"retcode": 0, "data": {}, "echo": "echo-done"})
                is False
            )
            # pop happens before the done-check, so the entry is removed
            assert "echo-done" not in api._futures
        finally:
            loop.close()

    async def test_feed_response_raises_api_error_when_retcode_nonzero(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            api.feed_response(
                {
                    "retcode": 1000,
                    "msg": "参数错误",
                    "data": None,
                    "echo": payload["echo"],
                }
            )

        api.bind_send(send)
        with pytest.raises(ApiError) as exc_info:
            await api.call("send_msg", message="x")
        assert exc_info.value.retcode == 1000
        assert exc_info.value.message == "参数错误"
        assert "1000" in str(exc_info.value)
        assert "参数错误" in str(exc_info.value)
        # future cleaned up after error
        assert api._futures == {}

    async def test_feed_response_falls_back_to_wording_when_msg_missing(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            api.feed_response(
                {"retcode": 1, "wording": "备用文案", "echo": payload["echo"]}
            )

        api.bind_send(send)
        with pytest.raises(ApiError) as exc_info:
            await api.call("send_msg", message="x")
        assert exc_info.value.message == "备用文案"

    async def test_feed_response_falls_back_to_unknown_when_no_msg(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            api.feed_response({"retcode": 2, "echo": payload["echo"]})

        api.bind_send(send)
        with pytest.raises(ApiError) as exc_info:
            await api.call("send_msg", message="x")
        assert exc_info.value.message == "unknown"

    async def test_feed_response_missing_retcode_treated_as_success(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            # retcode omitted entirely -> defaults to 0 -> success path
            api.feed_response({"data": {"ok": True}, "echo": payload["echo"]})

        api.bind_send(send)
        result = await api.call("send_msg", message="x")
        assert result["data"] == {"ok": True}


class TestCancelAll:
    async def test_cancel_all_rejects_pending_with_connection_error(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            pass

        api.bind_send(send)
        task = asyncio.create_task(api.call("send_msg", message="x"))
        await asyncio.sleep(0)
        assert len(api._futures) == 1
        api.cancel_all()
        with pytest.raises(ConnectionError, match="连接已断开"):
            await task
        assert api._futures == {}

    async def test_cancel_all_skips_done_futures(self) -> None:
        api = ApiCaller()

        async def send(payload: dict[str, Any]) -> None:
            api.feed_response({"retcode": 0, "data": {}, "echo": payload["echo"]})

        api.bind_send(send)
        await api.call("send_msg", message="x")  # already resolved
        # should not raise even though there are no pending futures
        api.cancel_all()
        assert api._futures == {}

    def test_cancel_all_on_empty_is_noop(self) -> None:
        api = ApiCaller()
        api.cancel_all()
        assert api._futures == {}


class TestApiError:
    def test_message_and_retcode_stored(self) -> None:
        err = ApiError(retcode=404, message="not found")
        assert err.retcode == 404
        assert err.message == "not found"
        assert str(err) == "[404] not found"

    def test_is_exception_subclass(self) -> None:
        assert issubclass(ApiError, Exception)
        with pytest.raises(ApiError):
            raise ApiError(1, "x")
