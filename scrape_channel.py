import asyncio
import os
import json
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto
from dotenv import load_dotenv

load_dotenv()

api_id = int(os.getenv("TG_API_ID"))
api_hash = os.getenv("TG_API_HASH")
CHANNEL = "insilver_ua"
PHOTOS_DIR = "channel_photos"
DATA_FILE = "channel_data.json"

async def download_with_retry(msg, filename, retries=3):
    for attempt in range(retries):
        try:
            await msg.download_media(filename)
            return True
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Retry {attempt+1} для фото {msg.id}...")
                await asyncio.sleep(3)
            else:
                print(f"  Пропускаємо фото {msg.id}: {e}")
                return False

async def main():
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    client = TelegramClient("insilver_session", api_id, api_hash)
    await client.start()

    # завантажуємо існуючі дані якщо є
    existing = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            for p in json.load(f):
                existing[p["id"]] = p
        print(f"Вже є {len(existing)} постів, продовжуємо...")

    posts = dict(existing)
    photo_count = 0
    total = 0

    async for msg in client.iter_messages(CHANNEL, limit=9999):
        total += 1
        if total % 500 == 0:
            print(f"  Оброблено: {total}, нових фото: {photo_count}...")
            # зберігаємо проміжний результат
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(list(posts.values()), f, ensure_ascii=False, indent=2)

        if msg.id in existing:
            continue

        post = {
            "id": msg.id,
            "date": str(msg.date),
            "text": msg.text or "",
            "photo": None,
            "topic_id": None,
        }

        if hasattr(msg, 'reply_to') and msg.reply_to:
            post["topic_id"] = (
                getattr(msg.reply_to, 'reply_to_top_id', None) or
                getattr(msg.reply_to, 'reply_to_msg_id', None)
            )

        if msg.media and isinstance(msg.media, MessageMediaPhoto):
            filename = f"{PHOTOS_DIR}/photo_{msg.id}.jpg"
            if not os.path.exists(filename):
                ok = await download_with_retry(msg, filename)
                if ok:
                    photo_count += 1
            if os.path.exists(filename):
                post["photo"] = filename

        if post["text"] or post["photo"]:
            posts[msg.id] = post

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(list(posts.values()), f, ensure_ascii=False, indent=2)

    print(f"\nГотово!")
    print(f"Всього оброблено: {total}")
    print(f"Збережено постів: {len(posts)}")
    print(f"Нових фото: {photo_count}")
    await client.disconnect()

asyncio.run(main())
