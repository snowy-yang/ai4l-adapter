from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

from loguru import logger

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


def _init_config(target: Path = _DEFAULT_CONFIG) -> bool:
    """从模板复制配置文件, 若已存在则不覆盖. 返回是否新生成."""
    if target.exists():
        logger.info("配置文件已存在: {}", target)
        return False
    shutil.copyfile(_TEMPLATE, target)
    logger.info("已生成配置文件: {}", target)
    return True


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


def _setup_logging(level: str) -> None:
    """配置 loguru, 替换默认 handler."""
    logger.remove()
    logger.add(
        sys.stderr,
        level=level.upper(),
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> "
            "<level>{level: <8}</level> "
            "<level>{message}</level>"
        ),
    )


def main() -> None:
    args = parse_args()
    config_path = Path(args.config) if args.config else _DEFAULT_CONFIG

    if args.init:
        _setup_logging("INFO")
        _init_config(config_path)
        return

    if not config_path.exists():
        _setup_logging("INFO")
        _init_config(config_path)
        logger.error("请编辑 {} 后重新启动", config_path)
        sys.exit(1)

    config = Config.load(str(config_path))
    _setup_logging(config.log.level)
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        sys.exit(0)
