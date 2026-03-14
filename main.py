import asyncio
import logging
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from redis.asyncio import Redis
from telethon import TelegramClient, events
from telethon.sessions import StringSession


@dataclass
class Config:
    group_id: int
    sessions: List[str]
    session_paths: List[str]
    admin_session: str
    admin_index: int
    delay_min: int
    delay_max: int
    prompt: str
    context_max_messages: int
    emoji_probability: float
    short_reply_probability: float
    gif_probability: float
    gif_urls: List[str]
    gif_topic_map: Dict[str, List[str]]
    bot_personas: List[str]
    redis_url: str
    redis_key_prefix: str


def load_config(path: str = "config.yaml") -> Config:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {path}. Create it from config.yaml.example and fill real values."
        )

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    required = [
        "group_id",
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
        sessions=list(data.get("sessions") or []),
        session_paths=list(data.get("session_paths") or []),
        admin_session=str(data["admin_session"]),
        admin_index=int(data.get("admin_index", -1)),
        delay_min=int(data["delay_min"]),
        delay_max=int(data["delay_max"]),
        prompt=str(data["prompt"]),
        context_max_messages=int(data.get("context_max_messages", 15)),
        emoji_probability=float(data.get("emoji_probability", 0.25)),
        short_reply_probability=float(data.get("short_reply_probability", 0.5)),
        gif_probability=float(data.get("gif_probability", 0.05)),
        gif_urls=list(data.get("gif_urls") or []),
        gif_topic_map=dict(data.get("gif_topic_map") or {}),
        bot_personas=list(data.get("bot_personas") or []),
        redis_url=str(data.get("redis_url", "")).strip(),
        redis_key_prefix=str(data.get("redis_key_prefix", "tg_userbot")).strip(),
    )


def choose_next_bot(bot_indices: List[int], last_index: Optional[int]) -> int:
    candidates = [i for i in bot_indices if i != last_index]
    return random.choice(candidates) if candidates else bot_indices[0]


def build_prompt(
    style_prompt: str,
    context: List[str],
    use_emoji: bool,
    short_reply: bool,
    persona: str,
) -> str:
    context_text = "\n".join(context)
    emoji_rule = (
        "Optionally add one fitting emoji at the end."
        if use_emoji
        else "Do not use emojis."
    )
    length_rule = (
        "Reply with a very short message (3-8 words)."
        if short_reply
        else "Reply in 1-2 short sentences."
    )
    persona_line = f"Persona: {persona}\n" if persona else ""
    return (
        "System:\n"
        "You are a participant in a group chat. Follow the style and topic strictly.\n"
        f"{persona_line}"
        f"Style/Topic: {style_prompt}\n\n"
        "Conversation (latest messages):\n"
        f"{context_text}\n\n"
        f"Continue the conversation naturally. {length_rule} {emoji_rule}"
    )


def clamp_short_message(text: str, max_chars: int = 600) -> str:
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return ""
    sentences = []
    current = []
    for ch in cleaned:
        current.append(ch)
        if ch in ".!?":
            sentences.append("".join(current).strip())
            current = []
        if len(sentences) >= 2:
            break
    if not sentences and current:
        sentences.append("".join(current).strip())
    result = " ".join(sentences).strip()
    if len(result) > max_chars:
        cut = result[:max_chars].rstrip()
        last_space = cut.rfind(" ")
        if last_space > 0:
            cut = cut[:last_space].rstrip()
        result = cut
    return result


class ContextStore:
    async def add(self, speaker: str, text: str) -> None:
        raise NotImplementedError

    async def get_recent(self) -> List[str]:
        raise NotImplementedError

    async def clear(self) -> None:
        raise NotImplementedError


class MemoryContextStore(ContextStore):
    def __init__(self, max_messages: int) -> None:
        self._context: List[str] = []
        self._max = max_messages

    async def add(self, speaker: str, text: str) -> None:
        if not text:
            return
        self._context.append(f"{speaker}: {text}")
        if len(self._context) > self._max:
            del self._context[: len(self._context) - self._max]

    async def get_recent(self) -> List[str]:
        return list(self._context)

    async def clear(self) -> None:
        self._context.clear()


class RedisContextStore(ContextStore):
    def __init__(self, redis: Redis, key: str, max_messages: int) -> None:
        self._redis = redis
        self._key = key
        self._max = max_messages

    async def add(self, speaker: str, text: str) -> None:
        if not text:
            return
        value = f"{speaker}: {text}"
        await self._redis.rpush(self._key, value)
        await self._redis.ltrim(self._key, -self._max, -1)

    async def get_recent(self) -> List[str]:
        items = await self._redis.lrange(self._key, 0, -1)
        return [str(x) for x in items]

    async def clear(self) -> None:
        await self._redis.delete(self._key)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s %(message)s",
    )

    load_dotenv()

    api_id = os.getenv("APP_API_ID")
    api_hash = os.getenv("APP_API_HASH")
    xai_key = os.getenv("XAI_API_KEY")

    if not api_id or not api_hash:
        raise ValueError("APP_API_ID and APP_API_HASH must be set in .env")
    if not xai_key:
        raise ValueError("XAI_API_KEY must be set in .env")

    cfg = load_config()
    xai_client = OpenAI(base_url="https://api.x.ai/v1", api_key=xai_key)

    clients: List[TelegramClient] = []
    if cfg.session_paths:
        for path in cfg.session_paths:
            clients.append(TelegramClient(path, int(api_id), api_hash))
    else:
        for session in cfg.sessions:
            clients.append(TelegramClient(StringSession(session), int(api_id), api_hash))

    await asyncio.gather(*(c.start() for c in clients))

    bot_ids: Dict[int, TelegramClient] = {}
    bot_names: Dict[int, str] = {}
    bot_index_by_id: Dict[int, int] = {}
    for idx, c in enumerate(clients):
        me = await c.get_me()
        bot_ids[me.id] = c
        bot_index_by_id[me.id] = idx
        name = me.first_name or me.username or str(me.id)
        bot_names[me.id] = name

    if cfg.admin_index >= 0:
        if cfg.admin_index >= len(clients):
            raise ValueError("admin_index is out of range for configured sessions")
        admin_index = cfg.admin_index
    else:
        if cfg.session_paths:
            if cfg.admin_session not in cfg.session_paths:
                raise ValueError("admin_session must be one of session_paths in config.yaml")
            admin_index = cfg.session_paths.index(cfg.admin_session)
        else:
            if cfg.admin_session not in cfg.sessions:
                raise ValueError("admin_session must be one of sessions in config.yaml")
            admin_index = cfg.sessions.index(cfg.admin_session)
    admin_client = clients[admin_index]

    topic = cfg.prompt
    context_store: ContextStore
    if cfg.redis_url:
        redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        try:
            await redis.ping()
        except Exception as exc:
            raise RuntimeError(
                f"Redis unavailable at {cfg.redis_url}: {exc}"
            ) from exc
        key = f"{cfg.redis_key_prefix}:group:{cfg.group_id}:context"
        context_store = RedisContextStore(redis, key, cfg.context_max_messages)
    else:
        context_store = MemoryContextStore(cfg.context_max_messages)

    def get_persona(idx: int) -> str:
        if 0 <= idx < len(cfg.bot_personas):
            return str(cfg.bot_personas[idx]).strip()
        return ""

    async def generate_message(topic_value: str, ctx: List[str], persona: str) -> str:
        use_emoji = random.random() < cfg.emoji_probability
        short_reply = random.random() < cfg.short_reply_probability
        prompt = build_prompt(topic_value, ctx, use_emoji, short_reply, persona)
        response = await asyncio.to_thread(
            xai_client.responses.create,
            model="grok-3-fast",
            input=prompt,
        )
        text = response.output_text
        return clamp_short_message(text)

    def choose_gif_url(topic_value: str) -> Optional[str]:
        topic_lower = topic_value.lower()
        for key, urls in cfg.gif_topic_map.items():
            if key.lower() in topic_lower and urls:
                return random.choice(urls)
        if cfg.gif_urls:
            return random.choice(cfg.gif_urls)
        return None

    async def send_message_or_gif(
        bot_client: TelegramClient, msg: str, reply_to: Optional[int] = None
    ) -> None:
        use_gif = random.random() < cfg.gif_probability
        gif_url = choose_gif_url(topic) if use_gif else None

        async with bot_client.action(cfg.group_id, "typing"):
            await asyncio.sleep(random.randint(1, 3))

        if gif_url:
            caption = msg if len(msg) <= 120 else None
            await bot_client.send_file(
                cfg.group_id, gif_url, caption=caption, reply_to=reply_to
            )
            bot_me = await bot_client.get_me()
            logging.info(
                "Sent GIF as %s (reply_to=%s): %s",
                bot_names.get(bot_me.id, "bot"),
                reply_to,
                caption or "sent a GIF",
            )
            await context_store.add(
                bot_names.get(bot_me.id, "bot"), caption or "sent a GIF"
            )
            return

        await bot_client.send_message(cfg.group_id, msg, reply_to=reply_to)
        bot_me = await bot_client.get_me()
        logging.info(
            "Sent message as %s (reply_to=%s): %s",
            bot_names.get(bot_me.id, "bot"),
            reply_to,
            msg,
        )
        await context_store.add(bot_names.get(bot_me.id, "bot"), msg)

    async def send_reply(
        reply_to_event, bot_client: TelegramClient, bot_idx: int
    ) -> None:
        nonlocal topic
        text = reply_to_event.message.message or ""
        sender = await reply_to_event.get_sender()
        sender_name = sender.first_name or sender.username or str(sender.id)
        logging.info(
            "Incoming reply from %s: %s",
            sender_name,
            text,
        )
        await context_store.add(sender_name, text)

        persona = get_persona(bot_idx)
        ctx = await context_store.get_recent()
        msg = await generate_message(topic, ctx, persona)
        if not msg:
            return

        await send_message_or_gif(bot_client, msg, reply_to=reply_to_event.message.id)

    @admin_client.on(events.NewMessage)
    async def on_admin_private(event) -> None:
        nonlocal topic
        if not event.is_private or event.out:
            return
        new_topic = (event.message.message or "").strip()
        if not new_topic:
            return
        topic = new_topic
        await context_store.clear()
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
        logging.info("Incoming message from %s: %s", sender_name, text)
        await context_store.add(sender_name, text)

        if event.is_reply:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.sender_id in bot_ids:
                bot_client = bot_ids[reply_msg.sender_id]
                bot_idx = bot_index_by_id.get(reply_msg.sender_id, -1)
                await send_reply(event, bot_client, bot_idx)

    async def conversation_loop() -> None:
        last_index: Optional[int] = None
        bot_indices = list(range(len(clients)))

        while True:
            delay = random.randint(cfg.delay_min, cfg.delay_max)
            await asyncio.sleep(delay)

            idx = choose_next_bot(bot_indices, last_index)
            last_index = idx
            bot_client = clients[idx]

            persona = get_persona(idx)
            ctx = await context_store.get_recent()
            msg = await generate_message(topic, ctx, persona)
            if not msg:
                continue

            await send_message_or_gif(bot_client, msg)

    await conversation_loop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
