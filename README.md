# Telegram Userbot 

Userbot that simulates a natural group chat using multiple Telegram user accounts and Grok (xAI) for text generation.

## Features
- Multiple user accounts (Telethon string sessions or `.session` files).
- Shared prompt and shared context per group.
- Random bot selection (no same account twice in a row).
- Random delay and realistic typing simulation (length-aware).
- Reply handling: if a real user replies to a bot, the bot replies in-thread.
- Optional split replies: one main message plus a short in-thread continuation.
- Contextual reactions and emoji hints based on message tone.
- Optional realism: short replies, emojis, GIFs (low probability).
- Admin can change topic via private message; context is reset on topic change.

## Requirements
- Python 3.10+
- Telegram API credentials (`api_id`, `api_hash`)
- xAI API key (Grok)

## Install
```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
```

## Configuration
Create `config.yaml` from `config.yaml.example` and fill real values.

Minimal fields (single group):
- `group_id`: target group id (usually `-100...`)
- `prompt`: shared style/topic prompt
- `delay_min`, `delay_max`

Sessions (choose one):
- `sessions`: list of string sessions
- `session_paths`: list of full paths to `.session` files

Admin selection (choose one):
- `admin_session`: one entry from `sessions` or `session_paths`
- `admin_index`: index of the admin session (0-based); set to `-1` to use `admin_session`

Example (short):
```yaml
group_id: -1001234567890
session_paths:
  - "/full/path/to/a.session"
  - "/full/path/to/b.session"
admin_session: "/full/path/to/a.session"
admin_index: -1

delay_min: 10
delay_max: 40

prompt: "Group of friends discussing startups and technology"

context_max_messages: 25
max_context_chars: 1200
prompt_mode: "compact"
reply_to_last_probability: 0.25
emoji_probability: 0.25
short_reply_probability: 0.5
gif_probability: 0.05
split_message_probability: 0.18

bot_personas:
  - "Thoughtful and playful; reflects a bit, then shares lighthearted everyday AI uses."
  - "Skeptical and pragmatic; questions hype and focuses on limitations and tradeoffs."

redis_url: "redis://localhost:6379/0"
redis_key_prefix: "tg_userbot"
```

Multi-group example (optional):
```yaml
groups:
  - group_id: -1001111111111
    prompt: "Group of friends discussing startups and technology"
    delay_min: 10
    delay_max: 40
  - group_id: -1002222222222
    prompt: "Casual chat about music, movies, and everyday life"
    delay_min: 12
    delay_max: 45
```

Admin topic change:
- Send a private message to the admin account.
- If you have multiple groups, you can target a group by prefixing:  
  `-1001234567890: new topic here`  
  Otherwise, the topic is applied to all configured groups.

## Environment
Create `.env` based on `.env.example`:
```
APP_API_ID=...
APP_API_HASH=...
XAI_API_KEY=...
```

## Run
```bash
venv/bin/python main.py
```

## Notes
- Keep `.env`, `.session`, and `config.yaml` out of Git.
- If you need string sessions, generate them via Telethon once per account.
- GIFs use public URLs; replace with your own lists per topic in `config.yaml`.
- If `redis_url` is set, Redis stores the shared context (recommended for persistence).
- You can tune typing realism with `typing_*` settings and tone-aware emojis/reactions/GIFs using `*_context_map`.
- GIF tone selection can be tuned with `gif_tone_probability`.

## Mini-presentation (summary)
**Architecture**
```
Telegram Clients
    ↓
Event Handlers
    ↓
Context Manager
    ↓
Grok Generator
    ↓
Message Scheduler
```

**Why this design**
- Scales across multiple accounts.
- Shared prompt + shared context makes dialogue coherent.
- Random scheduling + typing simulation improves realism.
- Reply handling keeps interactions natural.
