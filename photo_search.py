import json
import os
import random

with open("photo_index.json", "r", encoding="utf-8") as f:
    PHOTO_INDEX = json.load(f)

TYPE_ROOTS = {
    "браслет":  ["браслет"],
    "ланцюжок": ["ланцюж", "ланцюг", "цепочк", "цеп"],
    "кулон":    ["кулон", "підвіск"],
    "обручка":  ["обручк", "перстен", "каблучк", "печатк", "кільц"],
    "сережки":  ["сережк", "серьг"],
    "хрестик":  ["хрестик", "хрест"],
}

shown_photos = {}
last_query = {}  # chat_id -> останній пошуковий запит

def find_photo(query: str, chat_id: int = 0) -> str | None:
    q = query.lower()

    # "ще / інший / другий" — повторюємо останній запит
    repeat_words = ["ще", "інший", "інше", "другий", "ще один", "більше", "далі", "наступний"]
    if any(r in q for r in repeat_words) and chat_id in last_query:
        q = last_query[chat_id]
    else:
        last_query[chat_id] = q

    candidates = []

    # по назві плетіння
    for item in PHOTO_INDEX:
        name = (item.get("name") or "").lower()
        if name and name in q:
            if item["photo"] and os.path.exists(item["photo"]):
                candidates.append(item["photo"])

    # пошук по keywords вимкнено

    # пошук по original_text вимкнено — дає помилкові збіги

    # по типу виробу — тільки точний збіг типу
    if not candidates:
        for item_type, roots in TYPE_ROOTS.items():
            if any(r in q for r in roots):
                matches = [i["photo"] for i in PHOTO_INDEX
                          if (i.get("type") or "").lower() == item_type
                          and i["photo"] and os.path.exists(i["photo"])
                          and i.get("type")]
                candidates.extend(matches)
                break
    # якщо нічого не знайшли по типу — не повертаємо випадкове фото
    if not candidates:
        return None

    if not candidates:
        return None

    shown = shown_photos.get(chat_id, set())
    fresh = [p for p in candidates if p not in shown]
    if not fresh:
        shown_photos[chat_id] = set()
        fresh = candidates

    pick = random.choice(fresh)
    shown_photos.setdefault(chat_id, set()).add(pick)
    return pick

PHOTO_TRIGGER_WORDS = [
    "покажи", "фото", "як виглядає", "є фото", "можна побачити",
    "покажіть", "фотка", "зображення", "photo", "show",
    "подивитись", "подивитися", "глянути", "глянь",
]

REPEAT_TRIGGER_WORDS = [
    "ще", "інший", "інше", "другий", "ще один", "більше фото",
    "далі", "наступний", "ще покажи", "а ще",
]

def wants_photo(text: str, chat_id: int = 0) -> bool:
    t = text.lower()
    if any(w in t for w in PHOTO_TRIGGER_WORDS):
        return True
    if any(w in t for w in REPEAT_TRIGGER_WORDS) and chat_id in last_query:
        return True
    return False
