import asyncio
import os
import sys
import yaml
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.channels import InviteToChannelRequest
from telethon.tl.functions.contacts import ImportContactsRequest
from telethon.tl.functions.messages import AddChatUserRequest
from telethon.tl.types import Chat
from telethon.tl.types import InputPhoneContact
from telethon.sessions import StringSession

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
    if len(sys.argv) < 2:
        raise SystemExit("Usage: add_user.py <username_or_phone> [more...]")
    if not api_id or not api_hash:
        raise ValueError("APP_API_ID и APP_API_HASH должны быть в .env")

    cfg = load_config()
    group_id = int(cfg["group_id"])
    sessions = list(cfg.get("sessions") or [])
    session_paths = list(cfg.get("session_paths") or [])
    admin_index = int(cfg.get("admin_index", -1))
    admin_session = cfg.get("admin_session")
    if not sessions and not session_paths:
        raise ValueError("config.yaml must include sessions or session_paths")

    if admin_index >= 0:
        session = (session_paths or sessions)[admin_index]
        client = (
            TelegramClient(session, api_id, api_hash)
            if session_paths
            else TelegramClient(StringSession(session), api_id, api_hash)
        )
    else:
        if session_paths and admin_session in session_paths:
            client = TelegramClient(admin_session, api_id, api_hash)
        elif sessions and admin_session in sessions:
            client = TelegramClient(StringSession(admin_session), api_id, api_hash)
        else:
            raise ValueError("admin_session must match configured sessions/session_paths")
    await client.start()
    async def resolve_user(identifier: str):
        value = identifier.strip()
        if not value:
            raise ValueError("Identifier cannot be empty")
        if value.startswith("@"):
            value = value[1:]
        is_phone = value.startswith("+") or value.isdigit()
        if not is_phone:
            return await client.get_input_entity(value)

        result = await client(
            ImportContactsRequest(
                [
                    InputPhoneContact(
                        client_id=0,
                        phone=value,
                        first_name="Temp",
                        last_name="Contact",
                    )
                ]
            )
        )
        if result.users:
            return await client.get_input_entity(result.users[0])
        raise ValueError(
            f"Не удалось найти пользователя по номеру {value}. "
            "Проверь номер или добавь username."
        )

    entity = await client.get_entity(group_id)
    for identifier in sys.argv[1:]:
        user = await resolve_user(identifier)
        if isinstance(entity, Chat):
            await client(
                AddChatUserRequest(chat_id=entity.id, user_id=user, fwd_limit=10)
            )
        else:
            await client(InviteToChannelRequest(channel=entity, users=[user]))
        print(f"Added: {identifier}")
    await client.disconnect()
    print("Done")


if __name__ == "__main__":
    asyncio.run(main())
