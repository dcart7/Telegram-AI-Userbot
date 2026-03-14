import asyncio
import os
import yaml
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.messages import CreateChatRequest, AddChatUserRequest
from telethon.sessions import StringSession

load_dotenv()

api_id = int(os.getenv("APP_API_ID", "0"))
api_hash = os.getenv("APP_API_HASH", "")
contact_phone_2 = os.getenv("TG_CONTACT_PHONE_2", "").strip()

group_title = "Userbot Test Group"


def load_config(path: str = "config.yaml") -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Missing {path}. Create it from config.yaml.example and fill real values."
        )
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def main() -> None:
    if not api_id or not api_hash:
        raise ValueError("APP_API_ID и APP_API_HASH должны быть в .env")

    cfg = load_config()
    sessions = list(cfg.get("sessions") or [])
    session_paths = list(cfg.get("session_paths") or [])
    if not sessions and not session_paths:
        raise ValueError("config.yaml must include sessions or session_paths")

    if session_paths:
        client1 = TelegramClient(session_paths[0], api_id, api_hash)
        client2 = TelegramClient(session_paths[1], api_id, api_hash)
    else:
        client1 = TelegramClient(StringSession(sessions[0]), api_id, api_hash)
        client2 = TelegramClient(StringSession(sessions[1]), api_id, api_hash)

    await client1.start()
    await client2.start()

    print("Аккаунты успешно подключены")

    me2 = await client2.get_me()
    input_user = None
    if me2.username:
        input_user = await client1.get_input_entity(me2.username)
    elif contact_phone_2:
        from telethon.tl.functions.contacts import ImportContactsRequest
        from telethon.tl.types import InputPhoneContact

        result = await client1(
            ImportContactsRequest(
                [
                    InputPhoneContact(
                        client_id=0,
                        phone=contact_phone_2,
                        first_name=me2.first_name or "User",
                        last_name=me2.last_name or "",
                    )
                ]
            )
        )
        if result.users:
            input_user = await client1.get_input_entity(result.users[0])

    if not input_user:
        raise ValueError(
            "Невозможно получить пользователя второго аккаунта. "
            "Задай username для второго аккаунта или укажи TG_CONTACT_PHONE_2 в .env."
        )

    await client1(CreateChatRequest(users=[input_user], title=group_title))
    print("Группа создана")

    dialogs = await client1.get_dialogs()
    group_id = next((d.id for d in dialogs if d.name == group_title), None)

    print("GROUP ID:", group_id)

    try:
        await client1(
            AddChatUserRequest(
                chat_id=group_id,
                user_id=me2.id,
                fwd_limit=10,
            )
        )
        print("Второй аккаунт добавлен")
    except Exception:
        print("Аккаунт уже в группе")

    await client1.disconnect()
    await client2.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
