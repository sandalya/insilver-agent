import json
import os
from datetime import datetime

ORDERS_FILE = os.path.join(os.path.dirname(__file__), "orders.json")

if not os.path.exists(ORDERS_FILE):
    exit()

with open(ORDERS_FILE, "r", encoding="utf-8") as f:
    orders = json.load(f)

now = datetime.now()
changed = 0
for o in orders:
    if o.get("status") == "sent":
        try:
            created = datetime.strptime(o.get("created", ""), "%d.%m.%Y %H:%M")
            if (now - created).days >= 30:
                o["status"] = "archived"
                changed += 1
        except Exception:
            pass

if changed:
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    print(f"Архівовано: {changed} замовлень")
