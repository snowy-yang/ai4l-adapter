from __future__ import annotations

from onebot_adapter.message import Message, MessageSegment


class TestMessageSegment:
    def test_text_factory(self) -> None:
        seg = MessageSegment.text("hello")
        assert seg.type == "text"
        assert seg.data == {"text": "hello"}

    def test_at_factory_stringifies_qq(self) -> None:
        seg = MessageSegment.at(12345)
        assert seg.type == "at"
        assert seg.data == {"qq": "12345"}

    def test_at_factory_accepts_str(self) -> None:
        seg = MessageSegment.at("all")
        assert seg.data == {"qq": "all"}

    def test_reply_factory(self) -> None:
        seg = MessageSegment.reply(99)
        assert seg.type == "reply"
        assert seg.data == {"id": "99"}

    def test_image_factory_merges_extra(self) -> None:
        seg = MessageSegment.image("file:///a.png", cache=0, proxy=True)
        assert seg.type == "image"
        assert seg.data["file"] == "file:///a.png"
        assert seg.data["cache"] == 0
        assert seg.data["proxy"] is True

    def test_to_dict_shape(self) -> None:
        seg = MessageSegment.text("x")
        assert seg.to_dict() == {"type": "text", "data": {"text": "x"}}

    def test_from_dict_roundtrip(self) -> None:
        raw = {"type": "image", "data": {"file": "f.png"}}
        seg = MessageSegment.from_dict(raw)
        assert seg.type == "image"
        assert seg.data == {"file": "f.png"}
        assert seg.to_dict() == raw

    def test_from_dict_defaults_empty_data(self) -> None:
        seg = MessageSegment.from_dict({"type": "poke"})
        assert seg.type == "poke"
        assert seg.data == {}

    def test_default_data_is_unique_per_instance(self) -> None:
        a = MessageSegment(type="text")
        b = MessageSegment(type="text")
        a.data["text"] = "a"
        assert b.data == {}


class TestMessage:
    def test_from_raw_string_becomes_text_segment(self) -> None:
        msg = Message.from_raw("plain text")
        assert len(msg.segments) == 1
        assert msg.segments[0].type == "text"
        assert msg.segments[0].data == {"text": "plain text"}

    def test_from_raw_list_of_segments(self) -> None:
        raw = [
            {"type": "text", "data": {"text": "hi "}},
            {"type": "at", "data": {"qq": "1"}},
        ]
        msg = Message.from_raw(raw)
        assert len(msg.segments) == 2
        assert msg.segments[0].type == "text"
        assert msg.segments[1].type == "at"
        assert msg.segments[1].data == {"qq": "1"}

    def test_from_raw_none_returns_empty(self) -> None:
        msg = Message.from_raw(None)
        assert msg.segments == []

    def test_from_raw_other_type_returns_empty(self) -> None:
        msg = Message.from_raw(42)
        assert msg.segments == []

    def test_to_dict_maps_segments(self) -> None:
        msg = Message([MessageSegment.text("a"), MessageSegment.at(1)])
        assert msg.to_dict() == [
            {"type": "text", "data": {"text": "a"}},
            {"type": "at", "data": {"qq": "1"}},
        ]

    def test_str_text_only(self) -> None:
        msg = Message([MessageSegment.text("foo"), MessageSegment.text("bar")])
        assert str(msg) == "foobar"

    def test_str_mixed_segments(self) -> None:
        msg = Message([MessageSegment.text("hi "), MessageSegment.at(1)])
        assert str(msg) == "hi [at:{'qq': '1'}]"

    def test_str_empty(self) -> None:
        assert str(Message()) == ""

    def test_str_text_segment_missing_text_key(self) -> None:
        msg = Message([MessageSegment(type="text")])
        assert str(msg) == ""

    def test_default_segments_is_unique_per_instance(self) -> None:
        a = Message()
        b = Message()
        a.segments.append(MessageSegment.text("x"))
        assert b.segments == []
