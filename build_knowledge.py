import json

with open("channel_data.json", "r", encoding="utf-8") as f:
    posts = json.load(f)

# пости з текстом і фото — це описи виробів
products = [p for p in posts if p["text"] and p["photo"]]

print(f"Постів з описом і фото: {len(products)}")
print(f"\n--- Всі описи виробів ---")
for p in products:
    print(f"\n[{p['date'][:10]}]")
    print(f"Текст: {p['text'][:300]}")
    print(f"Фото: {p['photo']}")
