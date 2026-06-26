import asyncio
import logging

from onebot_adapter import Bot, Server

logging.basicConfig(level=logging.INFO)


async def main() -> None:
    bot = Bot("ws://127.0.0.1:3001", access_token=None)
    server = Server(bot, host="127.0.0.1", port=8080)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
