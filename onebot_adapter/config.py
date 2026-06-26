from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

_DEFAULT_PATH = "config.toml"


@dataclass
class OneBotConfig:
    ws_url: str = "ws://127.0.0.1:3001"
    access_token: str = ""
    reconnect_interval: float = 3.0


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    events_path: str = "/events"
    action_path: str = "/action"
    ws_path: str = "/ws"


@dataclass
class LogConfig:
    level: str = "INFO"


@dataclass
class Config:
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    log: LogConfig = field(default_factory=LogConfig)

    @classmethod
    def load(cls, path: str | os.PathLike[str] | None = None) -> Config:
        """从 TOML 文件加载配置.

        路径查找顺序: 显式参数 > 环境变量 ONEBOT_ADAPTER_CONFIG > ./config.toml
        未知字段自动忽略. 使用 tomlkit 解析, 保留注释与格式.
        """
        resolved = _resolve_path(path)
        if resolved is None or not Path(resolved).is_file():
            return cls()
        with open(resolved, encoding="utf-8") as f:
            doc = tomlkit.parse(f.read())
        return cls(
            onebot=_section(OneBotConfig, doc.get("onebot", {})),
            server=_section(ServerConfig, doc.get("server", {})),
            log=_section(LogConfig, doc.get("log", {})),
        )


def _section(cls: type, raw: Any) -> Any:
    """只取 cls 已知字段, 忽略多余键."""
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in raw.items() if k in names})


def _resolve_path(path: str | os.PathLike[str] | None) -> str | None:
    if path is not None:
        return str(path)
    env = os.environ.get("ONEBOT_ADAPTER_CONFIG")
    if env:
        return env
    if Path(_DEFAULT_PATH).is_file():
        return _DEFAULT_PATH
    return None
