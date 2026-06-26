from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MessageSegment:
    """OneBot 11 消息段。

    一条消息由若干段组成,每段形如 {"type": "text", "data": {"text": "..."}}。
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def text(cls, text: str) -> MessageSegment:
        return cls(type="text", data={"text": text})

    @classmethod
    def at(cls, qq: int | str) -> MessageSegment:
        return cls(type="at", data={"qq": str(qq)})

    @classmethod
    def reply(cls, id: int) -> MessageSegment:
        return cls(type="reply", data={"id": str(id)})

    @classmethod
    def image(cls, file: str, **extra: Any) -> MessageSegment:
        data = {"file": file, **extra}
        return cls(type="image", data=data)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MessageSegment:
        return cls(type=raw["type"], data=raw.get("data", {}))


@dataclass
class Message:
    """消息段列表的薄封装,支持字符串/段/字典互转。"""

    segments: list[MessageSegment] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Any) -> Message:
        """从 OneBot 原始消息字段构造。

        原始消息可能是字符串(CQ 码或纯文本)或消息段数组。
        """
        if isinstance(raw, str):
            return cls([MessageSegment.text(raw)])
        if isinstance(raw, list):
            return cls([MessageSegment.from_dict(seg) for seg in raw])
        return cls()

    def to_dict(self) -> list[dict[str, Any]]:
        return [seg.to_dict() for seg in self.segments]

    def __str__(self) -> str:
        out: list[str] = []
        for seg in self.segments:
            if seg.type == "text":
                out.append(seg.data.get("text", ""))
            else:
                out.append(f"[{seg.type}:{seg.data}]")
        return "".join(out)
