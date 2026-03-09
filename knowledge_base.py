import json
import os
from datetime import datetime

KB_FILE = "knowledge_base.json"

def load_kb() -> list:
    if not os.path.exists(KB_FILE):
        return []
    with open(KB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_kb(items: list):
    with open(KB_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def add_entry(trigger: str, response_text: str, media: list = None, tags: list = None, source: str = "admin"):
    """Додає або оновлює запис в базі знань."""
    items = load_kb()
    # перевіряємо чи не дублюємо
    for item in items:
        if item["trigger"].lower() == trigger.lower():
            item["response_text"] = response_text
            if media:
                item["media"] = media
            item["updated"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            save_kb(items)
            return item["id"]
    new_id = max([i["id"] for i in items], default=0) + 1
    items.append({
        "id": new_id,
        "trigger": trigger,
        "response_text": response_text,
        "media": media or [],
        "created": datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    save_kb(items)
    return new_id

def attach_media(entry_id: int, photo_path: str):
    """Прикріплює фото до існуючого запису."""
    items = load_kb()
    for item in items:
        if item["id"] == entry_id:
            if "media" not in item:
                item["media"] = []
            item["media"].append(photo_path)
            save_kb(items)
            return True
    return False

def get_last_entry_id() -> int | None:
    """Повертає ID останнього доданого запису."""
    items = load_kb()
    if not items:
        return None
    return items[-1]["id"]

def find_response(user_text: str, ai_client) -> dict | None:
    """Шукає відповідь в базі знань через AI."""
    items = load_kb()
    if not items:
        return None
    kb_list = "\n".join(
        f'{item["id"]}. {item["trigger"]}'
        for item in items
    )
    prompt = (
        f"Клієнт написав: \"{user_text}\"\n\n"
        f"База знань:\n{kb_list}\n\n"
        f"Знайди запис який ТОЧНО відповідає запиту клієнта. "
        f"Відповідай тільки цифрою ID. "
        f"Якщо жоден не підходить точно — відповідай: 0"
    )
    try:
        r = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ти матчиш запит клієнта з базою знань. Відповідай тільки цифрою ID або 0."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=10,
            temperature=0,
        )
        entry_id = int(r.choices[0].message.content.strip())
        if entry_id == 0:
            return None
        for item in items:
            if item["id"] == entry_id:
                return item
    except Exception:
        pass
    return None

def format_learned_summary() -> str:
    """Форматує список вивченого для Владислава."""
    items = load_kb()
    if not items:
        return "База знань порожня."
    lines = []
    for item in items:
        media_note = f" + 📷{len(item['media'])}фото" if item.get("media") else ""
        lines.append(f"{item['id']}. На запит \"{item['trigger']}\" → {item['response_text'][:60]}...{media_note}")
    return "📚 Що я знаю:\n\n" + "\n".join(lines)
