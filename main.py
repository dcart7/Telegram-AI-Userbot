import asyncio
import logging
import os
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from redis.asyncio import Redis
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.functions.messages import SendReactionRequest
from telethon.tl.types import ReactionEmoji


@dataclass
class GroupConfig:
    group_id: int
    prompt: str
    delay_min: int
    delay_max: int


@dataclass
class Config:
    groups: List[GroupConfig]
    sessions: List[str]
    session_paths: List[str]
    admin_session: str
    admin_index: int
    context_max_messages: int
    emoji_probability: float
    short_reply_probability: float
    gif_probability: float
    reaction_probability: float
    reaction_emojis: List[str]
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
        "admin_session",
        "delay_min",
        "delay_max",
        "prompt",
    ]
    missing = [k for k in required if k not in data]
    if missing and "groups" not in data:
        raise ValueError(f"Missing config keys: {', '.join(missing)}")

    groups: List[GroupConfig] = []
    if data.get("groups"):
        for item in data["groups"]:
            group_id = int(item["group_id"])
            prompt = str(item["prompt"])
            delay_min = int(item.get("delay_min", data.get("delay_min", 10)))
            delay_max = int(item.get("delay_max", data.get("delay_max", 40)))
            groups.append(
                GroupConfig(
                    group_id=group_id,
                    prompt=prompt,
                    delay_min=delay_min,
                    delay_max=delay_max,
                )
            )
    else:
        groups.append(
            GroupConfig(
                group_id=int(data["group_id"]),
                prompt=str(data["prompt"]),
                delay_min=int(data["delay_min"]),
                delay_max=int(data["delay_max"]),
            )
        )

    return Config(
        groups=groups,
        sessions=list(data.get("sessions") or []),
        session_paths=list(data.get("session_paths") or []),
        admin_session=str(data["admin_session"]),
        admin_index=int(data.get("admin_index", -1)),
        context_max_messages=int(data.get("context_max_messages", 15)),
        emoji_probability=float(data.get("emoji_probability", 0.25)),
        short_reply_probability=float(data.get("short_reply_probability", 0.5)),
        gif_probability=float(data.get("gif_probability", 0.05)),
        reaction_probability=float(data.get("reaction_probability", 0.3)),
        reaction_emojis=list(data.get("reaction_emojis") or ["👍"]),
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
    base_prompt: str,
    topic_prompt: str,
    context: List[str],
    use_emoji: bool,
    short_reply: bool,
    persona: str,
) -> str:
    context_text = "\n".join(context)
    emoji_rule = (
        "Emojis are allowed. If you use one, it may appear within the text, not only at the end."
        if use_emoji
        else "Do not use emojis."
    )
    length_rule = (
        "Reply with a very short message (3-8 words)."
        if short_reply
        else "Reply in 1-2 short sentences."
    )
    persona_line = f"Persona: {persona}\n" if persona else ""
    variety_rules = (
        "Avoid repeating the last speaker or their phrasing. "
        "Don't start with the same filler as the previous bot. "
        "Add a light, playful or ironic touch occasionally. "
        "Sometimes ask a short follow-up question. "
        "Keep it natural and human, not overly formal."
    )
    greeting_rule = "Do not use greetings or farewells in every message, only occasionally."
    return (
        "System:\n"
        "You are a participant in a group chat. Follow the style and topic strictly.\n"
        f"{persona_line}"
        f"Style: {base_prompt}\n"
        f"Current topic: {topic_prompt}\n\n"
        "Conversation (latest messages):\n"
        f"{context_text}\n\n"
        f"Continue the conversation naturally. {length_rule} {emoji_rule}\n"
        f"Additional rules: {variety_rules} {greeting_rule}"
    )


def clamp_short_message(
    text: str, max_chars: int = 800, max_words: int = 60
) -> str:
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
    words = result.split()
    if len(words) > max_words:
        result = " ".join(words[:max_words]).rstrip() + "…"
    if len(result) > max_chars:
        cut = result[:max_chars].rstrip()
        last_space = cut.rfind(" ")
        if last_space > 0:
            cut = cut[:last_space].rstrip()
        result = cut + "…"
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

    group_states: Dict[int, Dict[str, object]] = {}
    redis: Optional[Redis] = None
    if cfg.redis_url:
        redis = Redis.from_url(cfg.redis_url, decode_responses=True)
        try:
            await redis.ping()
        except Exception as exc:
            raise RuntimeError(
                f"Redis unavailable at {cfg.redis_url}: {exc}"
            ) from exc

    for group in cfg.groups:
        if redis:
            key = f"{cfg.redis_key_prefix}:group:{group.group_id}:context"
            context_store: ContextStore = RedisContextStore(
                redis, key, cfg.context_max_messages
            )
        else:
            context_store = MemoryContextStore(cfg.context_max_messages)

        group_states[group.group_id] = {
            "base_prompt": group.prompt,
            "topic": group.prompt,
            "context_store": context_store,
            "last_index": None,
            "delay_min": group.delay_min,
            "delay_max": group.delay_max,
        }

    def get_persona(idx: int) -> str:
        if 0 <= idx < len(cfg.bot_personas):
            return str(cfg.bot_personas[idx]).strip()
        return ""

    async def generate_message(
        base_prompt: str, topic_value: str, ctx: List[str], persona: str
    ) -> str:
        use_emoji = random.random() < cfg.emoji_probability
        short_reply = random.random() < cfg.short_reply_probability
        prompt = build_prompt(base_prompt, topic_value, ctx, use_emoji, short_reply, persona)
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
        group_id: int,
        bot_client: TelegramClient,
        msg: str,
        reply_to: Optional[int] = None,
    ) -> None:
        state = group_states[group_id]
        topic = str(state["topic"])
        use_gif = random.random() < cfg.gif_probability
        gif_url = choose_gif_url(topic) if use_gif else None

        async with bot_client.action(group_id, "typing"):
            await asyncio.sleep(random.randint(1, 3))

        if gif_url:
            caption = msg if len(msg) <= 120 else None
            await bot_client.send_file(
                group_id, gif_url, caption=caption, reply_to=reply_to
            )
            bot_me = await bot_client.get_me()
            logging.info(
                "Sent GIF as %s (reply_to=%s): %s",
                bot_names.get(bot_me.id, "bot"),
                reply_to,
                caption or "sent a GIF",
            )
            context_store = state["context_store"]
            await context_store.add(
                bot_names.get(bot_me.id, "bot"), caption or "sent a GIF"
            )
            state["last_index"] = bot_index_by_id.get(bot_me.id)
            return

        await bot_client.send_message(group_id, msg, reply_to=reply_to)
        bot_me = await bot_client.get_me()
        logging.info(
            "Sent message as %s (reply_to=%s): %s",
            bot_names.get(bot_me.id, "bot"),
            reply_to,
            msg,
        )
        context_store = state["context_store"]
        await context_store.add(bot_names.get(bot_me.id, "bot"), msg)
        state["last_index"] = bot_index_by_id.get(bot_me.id)

    async def send_reply(
        group_id: int, reply_to_event, bot_client: TelegramClient, bot_idx: int
    ) -> None:
        state = group_states[group_id]
        text = reply_to_event.message.message or ""
        sender = await reply_to_event.get_sender()
        sender_name = sender.first_name or sender.username or str(sender.id)
        logging.info(
            "Incoming reply from %s: %s",
            sender_name,
            text,
        )
        context_store = state["context_store"]
        await context_store.add(sender_name, text)

        persona = get_persona(bot_idx)
        ctx = await context_store.get_recent()
        base_prompt = str(state["base_prompt"])
        topic = str(state["topic"])
        msg = await generate_message(base_prompt, topic, ctx, persona)
        if not msg:
            return

        await send_message_or_gif(
            group_id, bot_client, msg, reply_to=reply_to_event.message.id
        )

    async def maybe_react(event) -> None:
        if random.random() >= cfg.reaction_probability:
            return
        if not cfg.reaction_emojis:
            return
        if not event.message or not getattr(event.message, "id", None):
            return
        try:
            reactor = random.choice(clients)
            reaction = random.choice(cfg.reaction_emojis)
            peer = await reactor.get_input_entity(event.chat_id)
            await reactor(
                SendReactionRequest(
                    peer=peer,
                    msg_id=event.message.id,
                    reaction=[ReactionEmoji(emoticon=reaction)],
                )
            )
            reactor_me = await reactor.get_me()
            reactor_name = bot_names.get(reactor_me.id, "bot")
            logging.info(
                "Reacted as %s with %s to message %s",
                reactor_name,
                reaction,
                event.message.id,
            )
        except Exception as exc:
            logging.warning(
                "Failed to react: %s (%s)",
                exc,
                exc.__class__.__name__,
            )

    def parse_admin_topic(text: str) -> Tuple[List[int], str]:
        cleaned = text.strip()
        if ":" in cleaned:
            prefix, rest = cleaned.split(":", 1)
            try:
                group_id = int(prefix.strip())
                if group_id in group_states:
                    return [group_id], rest.strip()
            except ValueError:
                pass
        return list(group_states.keys()), cleaned

    @admin_client.on(events.NewMessage)
    async def on_admin_private(event) -> None:
        if not event.is_private or event.out:
            return
        new_topic = (event.message.message or "").strip()
        if not new_topic:
            return
        target_groups, topic_text = parse_admin_topic(new_topic)
        if not topic_text:
            return

        for group_id in target_groups:
            state = group_states[group_id]
            state["topic"] = topic_text
            context_store = state["context_store"]
            await context_store.clear()

            admin_name = bot_names.get((await admin_client.get_me()).id, "admin")
            ctx = []
            base_prompt = str(state["base_prompt"])
            msg = await generate_message(base_prompt, topic_text, ctx, "")
            msg = msg or f"{admin_name}: {topic_text}"
            await send_message_or_gif(group_id, admin_client, msg)
            logging.info("Topic changed to: %s (group %s)", topic_text, group_id)

    listener_client = admin_client

    def resolve_sender_id(message) -> Optional[int]:
        sender_id = getattr(message, "sender_id", None)
        if sender_id:
            return sender_id
        from_id = getattr(message, "from_id", None)
        if from_id and getattr(from_id, "user_id", None):
            return from_id.user_id
        return None

    def register_group_handler(group_id: int) -> None:
        @listener_client.on(events.NewMessage(chats=group_id))
        async def on_group_message(event) -> None:
            if event.out:
                return
            text = event.message.message or ""
            sender = await event.get_sender()
            sender_name = sender.first_name or sender.username or str(sender.id)
            logging.info("Incoming message from %s: %s", sender_name, text)

            state = group_states[group_id]
            context_store = state["context_store"]
            await context_store.add(sender_name, text)
            await maybe_react(event)

            if event.is_reply:
                reply_msg = await event.get_reply_message()
                if reply_msg:
                    sender_id = resolve_sender_id(reply_msg)
                    if sender_id in bot_ids:
                        bot_client = bot_ids[sender_id]
                        bot_idx = bot_index_by_id.get(sender_id, -1)
                        await send_reply(group_id, event, bot_client, bot_idx)
                    else:
                        state = group_states[group_id]
                        last_index = state["last_index"]
                        idx = choose_next_bot(list(range(len(clients))), last_index)
                        bot_client = clients[idx]
                        await send_reply(group_id, event, bot_client, idx)

    for group_id in group_states:
        register_group_handler(group_id)

    async def conversation_loop(group_id: int) -> None:
        state = group_states[group_id]
        bot_indices = list(range(len(clients)))

        while True:
            delay = random.randint(int(state["delay_min"]), int(state["delay_max"]))
            await asyncio.sleep(delay)

            last_index = state["last_index"]
            idx = choose_next_bot(bot_indices, last_index)
            bot_client = clients[idx]

            persona = get_persona(idx)
            context_store = state["context_store"]
            ctx = await context_store.get_recent()
            base_prompt = str(state["base_prompt"])
            topic = str(state["topic"])
            msg = await generate_message(base_prompt, topic, ctx, persona)
            if not msg:
                continue

            await send_message_or_gif(group_id, bot_client, msg)

    await asyncio.gather(*(conversation_loop(gid) for gid in group_states.keys()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
