import asyncio
import os
import sys
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.sessions import StringSession
import yaml

load_dotenv()

api_id = int(os.getenv("APP_API_ID", "0"))
api_hash = os.getenv("APP_API_HASH", "")

def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {path}. Create it from config.yaml.example and fill real values."
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: send_message.py <first|second> <message>")

    which = sys.argv[1].strip().lower()
    message = " ".join(sys.argv[2:]).strip()
    if not message:
        raise SystemExit("Message cannot be empty")
    if not api_id or not api_hash:
        raise ValueError("APP_API_ID и APP_API_HASH должны быть в .env")

    cfg = load_config()
    group_id = int(cfg["group_id"])

    sessions = list(cfg.get("sessions") or [])
    session_paths = list(cfg.get("session_paths") or [])
    if not sessions and not session_paths:
        raise ValueError("config.yaml must include sessions or session_paths")

    index = 0 if which in {"first", "1"} else 1
    if session_paths:
        session = session_paths[index]
        client = TelegramClient(session, api_id, api_hash)
    else:
        session = sessions[index]
        client = TelegramClient(StringSession(session), api_id, api_hash)
    await client.start()
    await client.send_message(group_id, message)
    await client.disconnect()
    print("Sent")


if __name__ == "__main__":
    asyncio.run(main())
