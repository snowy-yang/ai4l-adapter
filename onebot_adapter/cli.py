from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from . import Bot, Server
from .config import Config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="onebot-adapter", description="OneBot 协议桥")
    parser.add_argument(
        "--config", "-c", default=None, help="配置文件路径 (默认: ./config.toml)"
    )
    return parser.parse_args()


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
    config = Config.load(args.config)
    logging.basicConfig(level=getattr(logging, config.log.level.upper(), logging.INFO))
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        sys.exit(0)
