import asyncio
import os
from telethon import TelegramClient
from dotenv import load_dotenv

load_dotenv()

api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")

async def main():
    client = TelegramClient("insilver_session", api_id, api_hash)
    await client.start()
    me = await client.get_me()
    print(f"Авторизовано як: {me.first_name} (@{me.username})")
    await client.disconnect()

asyncio.run(main())
