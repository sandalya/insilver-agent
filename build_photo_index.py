import json
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

with open("channel_data.json", "r", encoding="utf-8") as f:
    posts = json.load(f)

products = [p for p in posts if p["text"] and p["photo"]]

print(f"Обробляємо {len(products)} постів...")

index = []
for p in products:
    # витягуємо ключові слова через AI
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": 
                 "З опису ювелірного виробу витягни: тип виробу, назву/артикул, матеріал, масу, особливості. "
                 "Відповідай тільки JSON: {\"type\": \"\", \"name\": \"\", \"keywords\": [], \"description\": \"\"}"},
                {"role": "user", "content": p["text"]}
            ],
            max_tokens=150, temperature=0,
        )
        raw = r.choices[0].message.content.strip()
        # прибираємо markdown якщо є
        raw = raw.replace("```json", "").replace("```", "").strip()
        data = json.loads(raw)
        index.append({
            "photo": p["photo"],
            "date": p["date"][:10],
            "original_text": p["text"],
            "type": data.get("type", ""),
            "name": data.get("name", ""),
            "keywords": data.get("keywords", []),
            "description": data.get("description", ""),
        })
        print(f"  ✓ {data.get('type','')} — {data.get('name','')}")
    except Exception as e:
        print(f"  ✗ Помилка: {e}")
        index.append({
            "photo": p["photo"],
            "date": p["date"][:10],
            "original_text": p["text"],
            "type": "", "name": "", "keywords": [], "description": "",
        })

with open("photo_index.json", "w", encoding="utf-8") as f:
    json.dump(index, f, ensure_ascii=False, indent=2)

print(f"\nІндекс збережено: {len(index)} виробів -> photo_index.json")
