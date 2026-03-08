import json
import os
from datetime import datetime

FAQ_FILE = "faq_items.json"

def load_faq():
    if not os.path.exists(FAQ_FILE):
        return []
    with open(FAQ_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_faq(items):
    with open(FAQ_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)

def add_faq(topic: str, photo_path: str, caption: str = ""):
    items = load_faq()
    items.append({
        "id": len(items) + 1,
        "topic": topic,
        "photo_path": photo_path,
        "caption": caption,
        "created": datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    save_faq(items)

def find_faq(user_text: str, ai_client) -> dict | None:
    items = load_faq()
    if not items:
        return None
    faq_list = "\n".join(
        f'{item["id"]}. {item["topic"]}'
        for item in items
    )
    prompt = (
        f"Клієнт написав: \"{user_text}\"\n\n"
        f"Список FAQ:\n{faq_list}\n\n"
        f"Якщо питання клієнта стосується одного з FAQ — відповідай ТІЛЬКИ цифрою ID. "
        f"Якщо жоден не підходить — відповідай: 0"
    )
    try:
        r = ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Визнач чи питання стосується FAQ. Відповідай тільки цифрою."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=10,
            temperature=0,
        )
        faq_id = int(r.choices[0].message.content.strip())
        if faq_id == 0:
            return None
        for item in items:
            if item["id"] == faq_id:
                return item
    except Exception:
        pass
    return None
