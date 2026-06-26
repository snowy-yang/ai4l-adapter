# onebot-adapter

OneBot 11 正向 WebSocket 适配器, 并内置一个 HTTP + SSE 桥, 把 OneBot 翻译成极简协议对外暴露.

- 收: OneBot 实现 (NapCat / Lagrange / go-cqhttp 等) 通过正向 WebSocket 推送事件, 适配器接收并解析.
- 发: 调用 OneBot API (send_msg 等) 并等待响应, 通过 echo 字段配对请求与响应.
- 桥: `Server` 把 OneBot 事件翻译成极简格式经 SSE 推出, 把外部 HTTP 指令翻译成 OneBot API 调用.

## 安装

需要 Python >= 3.13.

```bash
uv sync
```

## 快速开始

### 作为 OneBot 客户端直接使用

```python
import asyncio
from onebot_adapter import Bot

async def main():
    bot = Bot("ws://127.0.0.1:3001", access_token=None)

    @bot.on_message()
    async def on_msg(event):
        if event.is_group:
            print(f"[群{event.group_id}] {event.user_id}: {event.message}")
        else:
            print(f"[私聊] {event.user_id}: {event.message}")
        await bot.send_msg(user_id=event.user_id, message="收到")

    await bot.run()

asyncio.run(main())
```

### 作为协议桥运行 (推荐)

```python
import asyncio
import logging
from onebot_adapter import Bot, Server

logging.basicConfig(level=logging.INFO)

async def main():
    bot = Bot("ws://127.0.0.1:3001", access_token=None)
    server = Server(bot, host="127.0.0.1", port=8080)
    await server.run()

asyncio.run(main())
```

或直接:

```bash
python main.py
```

桥启动后, 你的代码用 httpx 连上来即可, 无需接触 OneBot 细节.

## 架构

```
OneBot 实现                  onebot-adapter                   你的代码
                                  Bot
   │ WebSocket                    │
   ├──── 事件 ───────────► Connection ──► Dispatcher ──► Server._translate ──► SSE /events ──────► httpx SSE 客户端
   │                              │                                              │
   │                              ApiCaller ◄── _handle_action ◄── POST /action ◄── httpx POST 客户端
   ├◄── API 响应 ───────────────┤
   └──── API 调用 ──────────────►┘
```

### 模块

| 模块 | 职责 |
|------|------|
| `connection.py` | 正向 WebSocket 连接管理, 收发原始 JSON, 断线自动重连 |
| `api.py` | API 调用层, 通过 echo 字段配对请求与响应 |
| `event.py` | 事件解析与分发, Event / MessageEvent / NoticeEvent / RequestEvent |
| `message.py` | 消息段与消息列表, 支持字符串 / 段 / 字典互转 |
| `bot.py` | 组合以上三层, 提供便捷 API 封装与装饰器 |
| `server.py` | HTTP + SSE 桥, 事件翻译推出, 指令翻译转入 |

## 协议规范

### 事件 (SSE)

连接 `GET /events`, 返回 `text/event-stream`. 每个 SSE 事件的 `event:` 字段对应 OneBot `post_type`, `data:` 为 JSON.

SSE 首帧发送 `retry: 3000`, 客户端断开后按此间隔自动重连.

#### message

```json
{
  "kind": "group",
  "user_id": 100,
  "group_id": 200,
  "message": "hi [at:{'qq': '1'}]",
  "message_id": 99,
  "self_id": 999
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `kind` | str | `"group"` 或 `"private"` |
| `user_id` | int | 发送者 QQ |
| `group_id` | int / null | 群号, 私聊为 null |
| `message` | str | 消息文本表示 (非文本段渲染为 `[type:{data}]`) |
| `message_id` | int / null | 消息 ID, 可能缺失 |
| `self_id` | int | 机器人 QQ |

#### notice

```json
{
  "notice_type": "poke",
  "sub_type": "abc",
  "user_id": 3,
  "group_id": 4
}
```

#### request

```json
{
  "request_type": "friend",
  "sub_type": "add",
  "user_id": 5,
  "group_id": null,
  "comment": "我是谁"
}
```

#### 其他

未知 `post_type` 原样透传: `{"type": "<post_type>", "data": <原始 raw>}`.

### 指令 (HTTP)

`POST /action`, body 为 JSON:

```json
{
  "action": "send_msg",
  "params": {"group_id": 1, "message": "hi"}
}
```

`action` 直接使用 OneBot API 名 (send_msg / send_private_msg / send_group_msg / get_login_info 等). `params` 透传给 OneBot.

便利: `params.message` 传字符串时自动转为消息段数组 `[{type:text}]`, 传列表时原样透传.

响应:

```json
// 成功
{"ok": true, "data": {"message_id": 7}, "error": null}

// OneBot 返回错误
{"ok": false, "data": null, "error": {"retcode": 1000, "message": "参数错误"}}

// 请求本身无效 (缺 action / 非 JSON)
// HTTP 400
{"ok": false, "data": null, "error": {"retcode": -1, "message": "..."}}
```

## 客户端示例 (httpx)

```python
import asyncio
import json
import httpx

async def listen_events():
    async with httpx.AsyncClient() as c:
        async with c.stream("GET", "http://127.0.0.1:8080/events") as resp:
            event_type = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    print(f"[{event_type}] {data}")

async def send_action(c: httpx.AsyncClient, action: str, **params):
    resp = await c.post(
        "http://127.0.0.1:8080/action",
        json={"action": action, "params": params},
    )
    return resp.json()

async def main():
    async with httpx.AsyncClient() as c:
        # 发消息
        r = await send_action(c, "send_msg", group_id=123, message="hello")
        print(r)
        # 收事件 (阻塞, 放到另一个任务里)
        # await listen_events()

asyncio.run(main())
```

## API 参考

### Bot

```python
Bot(ws_url, *, access_token=None, reconnect_interval=3.0)
```

- `bot.on_message()` / `bot.on_notice()` / `bot.on_request()` — 装饰器, 注册事件处理器
- `await bot.send_msg(*, user_id=None, group_id=None, message)` — 发消息 (group_id 优先)
- `await bot.send_private_msg(user_id, message)`
- `await bot.send_group_msg(group_id, message)`
- `await bot.get_login_info()`
- `await bot.run()` — 阻塞运行
- `await bot.close()`

`message` 参数接受 `str` / `Message` / `list[MessageSegment]`.

### Server

```python
Server(bot, *, host="127.0.0.1", port=8080, events_path="/events", action_path="/action")
```

- `await server.run()` — 启动 HTTP + SSE 服务并阻塞运行 OneBot WS 连接
- `await server.close()`

### Event

`Event.from_raw(data)` 按 `post_type` 分流:

- `MessageEvent` — `.is_private` / `.is_group` / `.message` / `.user_id` / `.group_id`
- `NoticeEvent` — `.notice_type` / `.sub_type` / `.user_id` / `.group_id`
- `RequestEvent` — `.request_type` / `.sub_type` / `.user_id` / `.comment`

所有事件保留 `.raw` 原始字典.

### Message / MessageSegment

```python
MessageSegment.text("hi")
MessageSegment.at(12345)
MessageSegment.reply(99)
MessageSegment.image("file:///a.png", cache=0)

Message.from_raw(raw)  # str -> [text], list -> 段数组
str(message)           # 文本表示
```

## 配置

OneBot 实现侧需开启正向 WebSocket 服务, 例如 NapCat:

```json
{
  "network": {
    "websocket": {
      "enable": true,
      "host": "127.0.0.1",
      "port": 3001
    }
  }
}
```

如设置了 `access_token`, 构造 Bot 时传入同名参数.

## 开发

```bash
uv sync                       # 安装依赖
python -m pytest              # 运行测试 (103 个)
python -m ruff check .        # lint
python -m ruff format .       # 格式化
python -m pyright             # 类型检查
```

## 许可

MIT
