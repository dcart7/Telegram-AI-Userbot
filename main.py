import asyncio
import logging
import os
import random
import re
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
    gif_context_map: Dict[str, List[str]]
    bot_personas: List[str]
    redis_url: str
    redis_key_prefix: str
    xai_model: str
    prompt_mode: str
    max_context_chars: int
    reply_to_last_probability: float
    typing_base_min: float
    typing_base_max: float
    typing_min_delay: float
    typing_max_delay: float
    typing_chars_per_second_min: float
    typing_chars_per_second_max: float
    split_message_probability: float
    split_min_chars: int
    split_max_chars: int
    emoji_context_map: Dict[str, List[str]]
    reaction_context_map: Dict[str, List[str]]
    max_parallel_requests: int
    gif_tone_probability: float
    emoji_force_probability: float
    emoji_emotional_insert_probability: float
    emoji_mid_probability: float


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

    def normalize_context_map(raw: Dict[str, object]) -> Dict[str, List[str]]:
        result: Dict[str, List[str]] = {}
        for key, value in (raw or {}).items():
            if not isinstance(key, str):
                key = str(key)
            if isinstance(value, list):
                items = [str(x) for x in value if str(x).strip()]
                if items:
                    result[key.strip().lower()] = items
        return result

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
        gif_context_map=normalize_context_map(data.get("gif_context_map") or {}),
        bot_personas=list(data.get("bot_personas") or []),
        redis_url=str(data.get("redis_url", "")).strip(),
        redis_key_prefix=str(data.get("redis_key_prefix", "tg_userbot")).strip(),
        typing_base_min=float(data.get("typing_base_min", 0.4)),
        typing_base_max=float(data.get("typing_base_max", 1.6)),
        typing_min_delay=float(data.get("typing_min_delay", 0.8)),
        typing_max_delay=float(data.get("typing_max_delay", 8.0)),
        typing_chars_per_second_min=float(
            data.get("typing_chars_per_second_min", 8.0)
        ),
        typing_chars_per_second_max=float(
            data.get("typing_chars_per_second_max", 18.0)
        ),
        split_message_probability=float(data.get("split_message_probability", 0.18)),
        split_min_chars=int(data.get("split_min_chars", 50)),
        split_max_chars=int(data.get("split_max_chars", 240)),
        emoji_context_map=normalize_context_map(data.get("emoji_context_map") or {}),
        reaction_context_map=normalize_context_map(
            data.get("reaction_context_map") or {}
        ),
        max_parallel_requests=int(data.get("max_parallel_requests", 4)),
        gif_tone_probability=float(data.get("gif_tone_probability", 0.6)),
        emoji_force_probability=float(data.get("emoji_force_probability", 0.6)),
        emoji_emotional_insert_probability=float(
            data.get("emoji_emotional_insert_probability", 0.5)
        ),
        emoji_mid_probability=float(data.get("emoji_mid_probability", 0.35)),
        xai_model=str(data.get("xai_model", "grok-3-fast")).strip(),
        prompt_mode=str(data.get("prompt_mode", "compact")).strip(),
        max_context_chars=int(data.get("max_context_chars", 1200)),
        reply_to_last_probability=float(
            data.get("reply_to_last_probability", 0.25)
        ),
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
    focus_message: Optional[str],
    reply_context: Optional[str],
    allow_split: bool,
    split_token: str,
    emoji_hint: Optional[str],
    prompt_mode: str,
) -> str:
    context_text = "\n".join(context)
    emoji_rule = (
        "Emojis are allowed. If you use one, it may appear within the text, not only at the end."
        if use_emoji
        else "Do not use emojis."
    )
    if use_emoji and emoji_hint:
        emoji_rule = f"{emoji_rule} Prefer emojis that match tone: {emoji_hint}."
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
    accuracy_rules = (
        "Be specific and grounded in the last message. "
        "If replying, address it directly and clearly. "
        "Avoid generic filler."
    )
    casual_rule = (
        "Write like a real chat: short fragments, simple words, "
        "occasional lowercase starts or ellipses, no formal tone."
    )
    language_rule = "Reply in the same language as the latest human message."
    greeting_rule = "Do not use greetings or farewells in every message, only occasionally."
    focus_rule = (
        f"Message to respond to: {focus_message}\n"
        if focus_message
        else "No direct message to respond to.\n"
    )
    reply_rule = ""
    if allow_split and reply_context:
        reply_rule = (
            f"If you decide to split, output exactly two parts separated by {split_token}. "
            "Part 1 is the main message about the current topic. "
            f"Part 2 is a short continuation that replies to: {reply_context}. "
            "If you do not split, output a single message without the token."
        )
    compact = (
        "System:\n"
        "You are in a group chat. Follow style and topic.\n"
        f"{persona_line}"
        f"Style: {base_prompt}\n"
        f"Topic: {topic_prompt}\n"
        f"Recent messages:\n{context_text}\n"
        f"{focus_rule}\n"
        f"Rules: {length_rule} {emoji_rule} {language_rule} {accuracy_rules}\n"
        f"{variety_rules} {casual_rule} {greeting_rule} {reply_rule}"
    )
    full = (
        "System:\n"
        "You are a participant in a group chat. Follow the style and topic strictly.\n"
        f"{persona_line}"
        f"Style: {base_prompt}\n"
        f"Current topic: {topic_prompt}\n\n"
        "Conversation (latest messages):\n"
        f"{context_text}\n\n"
        f"{focus_rule}\n"
        f"Continue the conversation naturally. {length_rule} {emoji_rule}\n"
        f"Additional rules: {variety_rules} {accuracy_rules} {language_rule} {greeting_rule} {reply_rule}"
    )
    return full if prompt_mode.lower() == "full" else compact


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


def trim_context(context: List[str], max_chars: int) -> List[str]:
    if max_chars <= 0:
        return []
    total = 0
    trimmed: List[str] = []
    for item in reversed(context):
        item_len = len(item) + 1
        if total + item_len > max_chars:
            break
        trimmed.append(item)
        total += item_len
    return list(reversed(trimmed))


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


def infer_tone(text: str) -> str:
    if not text:
        return "neutral"
    lower = text.lower()
    patterns = {
        "angry": [
            r"\bидиот\w*\b",
            r"\bтуп(ой|ая|ые|о|ость)?\b",
            r"\bбес\w*\b",
            r"\bненавижу\b",
            r"\bзл(ой|ая|ость|ишь|ит)\b",
            r"\bагресс\w*\b",
            r"\bубью\b",
            r"\bсука\b",
            r"\bfuck\b",
            r"\bhate\b",
            r"\bangry\b",
            r"\bstupid\b",
        ],
        "sad": [
            r"\bгруст\w*\b",
            r"\bпечал\w*\b",
            r"\bплохо\b",
            r"\bустал\w*\b",
            r"\bодинок\w*\b",
            r"\bдепресс\w*\b",
            r"\bsad\b",
            r"\btired\b",
            r"\blonely\b",
            r"\bdepress\w*\b",
        ],
        "happy": [
            r"\bкласс\b",
            r"\bсупер\b",
            r"\bрад\b",
            r"\bкруто\b",
            r"\bnice\b",
            r"\bawesome\b",
            r"\bgreat\b",
            r"\bура\b",
        ],
        "laugh": [
            r"\bаха+\b",
            r"\bхаха+\b",
            r"\bлол\b",
            r"\blol\b",
            r"\blmao\b",
            r"\brofl\b",
        ],
        "surprise": [
            r"\bого\b",
            r"\bвау\b",
            r"\bничего себе\b",
            r"\bwow\b",
            r"\bwhoa\b",
        ],
        "agree": [
            r"\bсогласен\b",
            r"\bточно\b",
            r"\bда\b",
            r"\bверно\b",
            r"\bagree\b",
            r"\btrue\b",
            r"\byep\b",
        ],
        "question": [
            r"\bпочему\b",
            r"\bзачем\b",
            r"\bкак\b",
            r"\bчто\b",
            r"\bwhen\b",
            r"\bwhy\b",
            r"\bwhat\b",
            r"\bhow\b",
        ],
    }

    emoji_hits = {
        "angry": ["😡", "😠", "🤬", "👿"],
        "sad": ["😢", "😭", "🥲", "😔"],
        "happy": ["😄", "😁", "😊", "🙂", "😃"],
        "laugh": ["😂", "🤣", "😹"],
        "surprise": ["😮", "😲", "🤯", "😱"],
        "agree": ["👍", "✅", "👌"],
        "question": ["❓", "🤔"],
    }

    scores: Dict[str, int] = {k: 0 for k in patterns.keys()}
    for tone, regexes in patterns.items():
        for rx in regexes:
            scores[tone] += len(re.findall(rx, lower))

    for tone, emojis in emoji_hits.items():
        for emo in emojis:
            if emo in text:
                scores[tone] += 2

    question_marks = text.count("?") + text.count("？")
    if question_marks:
        scores["question"] += 1 + question_marks // 2

    exclamations = text.count("!") + text.count("！")
    if exclamations >= 2:
        scores["angry"] += 1
        scores["surprise"] += 1

    best_tone = "neutral"
    best_score = 0
    for tone, score in scores.items():
        if score > best_score:
            best_score = score
            best_tone = tone

    return best_tone if best_score > 0 else "neutral"


def pick_contextual_emojis(
    tone: str, context_map: Dict[str, List[str]], fallback: List[str]
) -> List[str]:
    if context_map:
        key = tone.lower().strip()
        if key in context_map and context_map[key]:
            return context_map[key]
        if "neutral" in context_map and context_map["neutral"]:
            return context_map["neutral"]
    return fallback


def split_response(text: str, token: str) -> List[str]:
    if not text:
        return []
    if token not in text:
        return [text]
    parts = [p.strip() for p in text.split(token) if p.strip()]
    if not parts:
        return []
    if len(parts) > 2:
        return [parts[0], " ".join(parts[1:]).strip()]
    return parts


def ensure_emoji(parts: List[str], emoji_pool: List[str]) -> List[str]:
    if not parts or not emoji_pool:
        return parts
    has_emoji = any(any(emo in part for emo in emoji_pool) for part in parts)
    if has_emoji:
        return parts
    chosen = random.choice(emoji_pool)
    parts[-1] = f"{parts[-1]} {chosen}".strip()
    return parts


def insert_emoji_naturally(
    parts: List[str],
    emoji_pool: List[str],
    tone: str,
    mid_probability: float,
) -> List[str]:
    if not parts or not emoji_pool:
        return parts
    if tone in {"neutral", "agree", "question"}:
        return parts
    has_any = any(any(emo in part for emo in emoji_pool) for part in parts)
    if has_any:
        return parts
    chosen = random.choice(emoji_pool)
    text = parts[-1]
    words = text.split()
    if len(words) >= 4 and random.random() < mid_probability:
        idx = random.randint(1, max(1, len(words) - 2))
        words.insert(idx, chosen)
        parts[-1] = " ".join(words)
    else:
        parts[-1] = f"{parts[-1]} {chosen}".strip()
    return parts


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
    llm_semaphore = asyncio.Semaphore(max(1, cfg.max_parallel_requests))

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
            "last_human_message_id": None,
            "last_human_message_text": "",
        }

    def get_persona(idx: int) -> str:
        if 0 <= idx < len(cfg.bot_personas):
            return str(cfg.bot_personas[idx]).strip()
        return ""

    async def generate_message(
        base_prompt: str,
        topic_value: str,
        ctx: List[str],
        persona: str,
        focus_message: Optional[str],
        reply_context: Optional[str],
        allow_split: bool,
        emoji_hint: Optional[str],
        emoji_pool: List[str],
        tone: str,
    ) -> List[str]:
        use_emoji = random.random() < cfg.emoji_probability
        short_reply = random.random() < cfg.short_reply_probability
        split_token = "<SPLIT>"
        trimmed_ctx = trim_context(ctx, cfg.max_context_chars)
        prompt = build_prompt(
            base_prompt,
            topic_value,
            trimmed_ctx,
            use_emoji,
            short_reply,
            persona,
            focus_message,
            reply_context,
            allow_split,
            split_token,
            emoji_hint,
            cfg.prompt_mode,
        )
        async with llm_semaphore:
            try:
                response = await asyncio.to_thread(
                    xai_client.responses.create,
                    model=cfg.xai_model,
                    input=prompt,
                )
            except Exception as exc:
                logging.warning(
                    "LLM request failed: %s (%s)",
                    exc,
                    exc.__class__.__name__,
                )
                await asyncio.sleep(5)
                return []
        text = response.output_text
        parts = split_response(text, split_token) or []
        cleaned_parts = []
        for idx, part in enumerate(parts):
            max_chars = cfg.split_max_chars if idx == 0 else cfg.split_max_chars
            cleaned = clamp_short_message(part, max_chars=max_chars)
            if cleaned:
                cleaned_parts.append(cleaned)
        if (
            use_emoji
            and cleaned_parts
            and random.random() < cfg.emoji_emotional_insert_probability
        ):
            cleaned_parts = insert_emoji_naturally(
                cleaned_parts, emoji_pool, tone, cfg.emoji_mid_probability
            )
        return cleaned_parts

    def choose_gif_url(topic_value: str, tone: str) -> Optional[str]:
        use_tone = random.random() < cfg.gif_tone_probability
        if use_tone and cfg.gif_context_map:
            pool = pick_contextual_emojis(tone, cfg.gif_context_map, [])
            if pool:
                return random.choice(pool)
        topic_lower = topic_value.lower()
        for key, urls in cfg.gif_topic_map.items():
            if key.lower() in topic_lower and urls:
                return random.choice(urls)
        if cfg.gif_urls:
            return random.choice(cfg.gif_urls)
        return None

    def estimate_typing_delay(text: str) -> float:
        char_count = max(1, len(text))
        base = random.uniform(cfg.typing_base_min, cfg.typing_base_max)
        cps = random.uniform(
            cfg.typing_chars_per_second_min, cfg.typing_chars_per_second_max
        )
        delay = base + (char_count / max(1.0, cps))
        return max(cfg.typing_min_delay, min(cfg.typing_max_delay, delay))

    async def simulate_typing(
        bot_client: TelegramClient, group_id: int, text: str
    ) -> None:
        delay = estimate_typing_delay(text)
        async with bot_client.action(group_id, "typing"):
            await asyncio.sleep(delay)

    async def send_message_or_gif(
        group_id: int,
        bot_client: TelegramClient,
        msg: str,
        reply_to: Optional[int] = None,
        allow_gif: bool = True,
        tone: str = "neutral",
    ) -> None:
        state = group_states[group_id]
        topic = str(state["topic"])
        use_gif = allow_gif and random.random() < cfg.gif_probability
        gif_url = choose_gif_url(topic, tone) if use_gif else None

        await simulate_typing(bot_client, group_id, msg)

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

    async def send_text_parts(
        group_id: int,
        bot_client: TelegramClient,
        parts: List[str],
        reply_target_id: Optional[int],
        tone: str,
        reply_single_to_id: Optional[int] = None,
    ) -> None:
        if not parts:
            return
        if len(parts) == 1:
            await send_message_or_gif(
                group_id,
                bot_client,
                parts[0],
                reply_to=reply_single_to_id,
                tone=tone,
            )
            return
        await send_message_or_gif(
            group_id,
            bot_client,
            parts[0],
            reply_to=None,
            allow_gif=False,
            tone="neutral",
        )
        await asyncio.sleep(random.uniform(0.6, 1.5))
        await send_message_or_gif(
            group_id,
            bot_client,
            parts[1],
            reply_to=reply_target_id,
            allow_gif=False,
            tone=tone,
        )

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
        reply_target_text = None
        reply_target_id = None
        if reply_to_event.is_reply:
            reply_msg = await reply_to_event.get_reply_message()
            if reply_msg:
                reply_target_text = reply_msg.message or ""
                reply_target_id = reply_msg.id
        tone = infer_tone(text or reply_target_text or "")
        emoji_hint_list = pick_contextual_emojis(
            tone, cfg.emoji_context_map, cfg.reaction_emojis
        )
        emoji_hint = " ".join(emoji_hint_list) if emoji_hint_list else None
        allow_split = (
            bool(reply_target_text)
            and len(text) >= cfg.split_min_chars
            and random.random() < cfg.split_message_probability
        )
        parts = await generate_message(
            base_prompt,
            topic,
            ctx,
            persona,
            text,
            reply_target_text,
            allow_split,
            emoji_hint,
            emoji_hint_list,
            tone,
        )
        if not parts:
            return

        if len(parts) == 1:
            await send_message_or_gif(
                group_id,
                bot_client,
                parts[0],
                reply_to=reply_to_event.message.id,
                tone=tone,
            )
        else:
            await send_text_parts(
                group_id, bot_client, parts, reply_target_id, tone=tone
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
            tone = infer_tone(event.message.message or "")
            pool = pick_contextual_emojis(
                tone, cfg.reaction_context_map, cfg.reaction_emojis
            )
            reaction = random.choice(pool) if pool else random.choice(cfg.reaction_emojis)
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
            parts = await generate_message(
                base_prompt,
                topic_text,
                ctx,
                "",
                None,
                None,
                False,
                None,
                [],
                "neutral",
            )
            msg = parts[0] if parts else f"{admin_name}: {topic_text}"
            await send_message_or_gif(group_id, admin_client, msg, tone="neutral")
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
            state["last_human_message_id"] = event.message.id
            state["last_human_message_text"] = text
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
            last_text = str(state.get("last_human_message_text") or "")
            reply_target_id = state.get("last_human_message_id")
            reply_to_last = (
                reply_target_id is not None
                and random.random() < cfg.reply_to_last_probability
            )
            tone = infer_tone(last_text)
            emoji_hint_list = pick_contextual_emojis(
                tone, cfg.emoji_context_map, cfg.reaction_emojis
            )
            emoji_hint = " ".join(emoji_hint_list) if emoji_hint_list else None
            allow_split = (
                bool(last_text)
                and len(last_text) >= cfg.split_min_chars
                and random.random() < cfg.split_message_probability
            )
            parts = await generate_message(
                base_prompt,
                topic,
                ctx,
                persona,
                last_text if reply_to_last else None,
                last_text if allow_split else None,
                allow_split,
                emoji_hint,
                emoji_hint_list,
                tone,
            )
            if not parts:
                continue

            await send_text_parts(
                group_id,
                bot_client,
                parts,
                reply_target_id,
                tone=tone,
                reply_single_to_id=reply_target_id if reply_to_last else None,
            )

    await asyncio.gather(*(conversation_loop(gid) for gid in group_states.keys()))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
