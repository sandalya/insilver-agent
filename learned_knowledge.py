import os
import json
from datetime import datetime

LEARNED_FILE = "learned_facts.json"

def load_facts() -> list:
    if os.path.exists(LEARNED_FILE):
        with open(LEARNED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_fact(fact: str, original: str):
    facts = load_facts()
    facts.append({
        "fact": fact,
        "original": original,
        "date": datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    with open(LEARNED_FILE, "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)

def get_facts_for_prompt() -> str:
    facts = load_facts()
    if not facts:
        return ""
    lines = [f["fact"] for f in facts]
    return "\n\nДОДАТКОВІ ЗНАННЯ (підтверджені майстром):\n" + "\n".join(f"• {l}" for l in lines)
