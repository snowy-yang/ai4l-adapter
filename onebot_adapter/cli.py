from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import sys
from pathlib import Path

from . import Bot, Server
from .config import Config

_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "config.toml"
_DEFAULT_CONFIG = Path("config.toml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="onebot-adapter", description="OneBot 协议桥")
    parser.add_argument(
        "--config", "-c", default=None, help="配置文件路径 (默认: ./config.toml)"
    )
    parser.add_argument(
        "--init", action="store_true", help="从模板生成 ./config.toml 后退出"
    )
    return parser.parse_args()


def _init_config(target: Path = _DEFAULT_CONFIG) -> Path:
    """从模板复制配置文件, 若已存在则不覆盖."""
    if target.exists():
        print(f"配置文件已存在: {target}")
        return target
    shutil.copyfile(_TEMPLATE, target)
    print(f"已生成: {target}")
    return target


async def _run(config: Config) -> None:
    bot = Bot(
        config.onebot.ws_url,
        access_token=config.onebot.access_token or None,
        reconnect_interval=config.onebot.reconnect_interval,
    )
    server = Server(
        bot,
        host=config.server.host,
        port=config.server.port,
        events_path=config.server.events_path,
        action_path=config.server.action_path,
        ws_path=config.server.ws_path,
    )
    await server.run()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}, 从模板自动生成")
        _init_config(config_path)
    config = Config.load(str(config_path))
    logging.basicConfig(level=getattr(logging, config.log.level.upper(), logging.INFO))
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        sys.exit(0)
