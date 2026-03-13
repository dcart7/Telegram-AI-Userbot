import asyncio
import logging
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from telethon import TelegramClient, events
from telethon.sessions import StringSession


@dataclass
class Config:
    group_id: int
    sessions: List[str]
    admin_session: str
    delay_min: int
    delay_max: int
    prompt: str
    context_max_messages: int


def load_config(path: str = "config.yaml") -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {path}. Create it from config.yaml.example and fill real values."
        )

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    required = [
        "group_id",
        "sessions",
        "admin_session",
        "delay_min",
        "delay_max",
        "prompt",
    ]
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"Missing config keys: {', '.join(missing)}")

    return Config(
        group_id=int(data["group_id"]),
        sessions=list(data["sessions"]),
        admin_session=str(data["admin_session"]),
        delay_min=int(data["delay_min"]),
        delay_max=int(data["delay_max"]),
        prompt=str(data["prompt"]),
        context_max_messages=int(data.get("context_max_messages", 15)),
    )


def choose_next_bot(bot_indices: List[int], last_index: Optional[int]) -> int:
    candidates = [i for i in bot_indices if i != last_index]
    return random.choice(candidates) if candidates else bot_indices[0]


def build_prompt(topic: str, context: List[str]) -> str:
    context_text = "\n".join(context)
    return (
        f"Topic: {topic}\n\n"
        "Conversation:\n"
        f"{context_text}\n\n"
        "Continue the conversation naturally. Reply in 1-2 short sentences."
    )


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
    )

    load_dotenv()

    api_id = os.getenv("APP_API_ID")
    api_hash = os.getenv("APP_API_HASH")
    openai_key = os.getenv("OPENAI_API_KEY")

    if not api_id or not api_hash:
        raise ValueError("APP_API_ID and APP_API_HASH must be set in .env")
    if not openai_key:
        raise ValueError("OPENAI_API_KEY must be set in .env")

    cfg = load_config()
    client = OpenAI()

    clients: List[TelegramClient] = []
    for session in cfg.sessions:
        clients.append(TelegramClient(StringSession(session), int(api_id), api_hash))

    await asyncio.gather(*(c.start() for c in clients))

    bot_ids: Dict[int, TelegramClient] = {}
    bot_names: Dict[int, str] = {}
    for c in clients:
        me = await c.get_me()
        bot_ids[me.id] = c
        name = me.first_name or me.username or str(me.id)
        bot_names[me.id] = name

    if cfg.admin_session not in cfg.sessions:
        raise ValueError("admin_session must be one of sessions in config.yaml")

    admin_index = cfg.sessions.index(cfg.admin_session)
    admin_client = clients[admin_index]

    context: List[str] = []
    topic = cfg.prompt

    def add_context(speaker: str, text: str) -> None:
        if not text:
            return
        context.append(f"{speaker}: {text}")
        if len(context) > cfg.context_max_messages:
            del context[: len(context) - cfg.context_max_messages]

    async def generate_message(topic_value: str, ctx: List[str]) -> str:
        prompt = build_prompt(topic_value, ctx)
        response = await asyncio.to_thread(
            client.responses.create,
            model="gpt-4.1-mini",
            input=prompt,
        )
        text = response.output_text
        return text.strip() if text else ""

    async def send_reply(reply_to_event, bot_client: TelegramClient) -> None:
        nonlocal topic
        text = reply_to_event.message.message or ""
        sender = await reply_to_event.get_sender()
        sender_name = sender.first_name or sender.username or str(sender.id)
        add_context(sender_name, text)

        msg = await generate_message(topic, context)
        if not msg:
            return

        await bot_client.send_chat_action(cfg.group_id, "typing")
        await asyncio.sleep(random.randint(1, 3))
        await bot_client.send_message(cfg.group_id, msg, reply_to=reply_to_event.message.id)
        bot_me = await bot_client.get_me()
        add_context(bot_names.get(bot_me.id, "bot"), msg)

    @admin_client.on(events.NewMessage)
    async def on_admin_private(event) -> None:
        nonlocal topic, context
        if not event.is_private or event.out:
            return
        new_topic = (event.message.message or "").strip()
        if not new_topic:
            return
        topic = new_topic
        context.clear()
        await admin_client.send_message(cfg.group_id, f"New topic: {topic}")
        logging.info("Topic changed to: %s", topic)

    listener_client = admin_client

    @listener_client.on(events.NewMessage(chats=cfg.group_id))
    async def on_group_message(event) -> None:
        if event.out:
            return
        text = event.message.message or ""
        sender = await event.get_sender()
        sender_name = sender.first_name or sender.username or str(sender.id)
        add_context(sender_name, text)

        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id in bot_ids:
                bot_client = bot_ids[reply_msg.sender_id]
                await send_reply(event, bot_client)

    async def conversation_loop() -> None:
        last_index: Optional[int] = None
        bot_indices = list(range(len(clients)))

        while True:
            delay = random.randint(cfg.delay_min, cfg.delay_max)
            await asyncio.sleep(delay)

            idx = choose_next_bot(bot_indices, last_index)
            last_index = idx
            bot_client = clients[idx]

            msg = await generate_message(topic, context)
            if not msg:
                continue

            await bot_client.send_chat_action(cfg.group_id, "typing")
            await asyncio.sleep(random.randint(1, 3))
            await bot_client.send_message(cfg.group_id, msg)
            bot_me = await bot_client.get_me()
            add_context(bot_names.get(bot_me.id, "bot"), msg)

    await conversation_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
