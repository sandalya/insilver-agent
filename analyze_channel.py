import json

with open("channel_data.json", "r", encoding="utf-8") as f:
    posts = json.load(f)

texts = [p for p in posts if p["text"]]
photos = [p for p in posts if p["photo"]]
both = [p for p in posts if p["text"] and p["photo"]]

print(f"Всього постів: {len(posts)}")
print(f"З текстом: {len(texts)}")
print(f"З фото: {len(photos)}")
print(f"З текстом і фото: {len(both)}")
print(f"\n--- Перші 5 постів з текстом ---")
for p in texts[:5]:
    print(f"\n[{p['date'][:10]}] {p['text'][:200]}")
    if p['photo']:
        print(f"  фото: {p['photo']}")
