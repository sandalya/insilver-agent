import json
import os
import secrets
import hashlib
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORDERS_FILE = os.path.join(BASE_DIR, "orders.json")
FACTS_FILE = os.path.join(BASE_DIR, "learned_facts.json")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x]

app = FastAPI()
TOKENS_FILE = os.path.join(BASE_DIR, "web_tokens.json")

def load_tokens():
    if not os.path.exists(TOKENS_FILE):
        return {}
    with open(TOKENS_FILE, "r") as f:
        return json.load(f)

def save_tokens(tokens):
    with open(TOKENS_FILE, "w") as f:
        json.dump(tokens, f)

def load_orders():
    if not os.path.exists(ORDERS_FILE):
        return []
    with open(ORDERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def verify_token(token: str):
    tokens = load_tokens()
    if token not in tokens:
        return False
    expires = datetime.fromisoformat(tokens[token]["expires"])
    if datetime.now() > expires:
        tokens.pop(token)
        save_tokens(tokens)
        return False
    return True

@app.get("/auth/{token}", response_class=HTMLResponse)
async def auth(token: str):
    if not verify_token(token):
        raise HTTPException(status_code=403, detail="Посилання недійсне або застаріло")
    with open(os.path.join(BASE_DIR, "web/templates/dashboard.html"), "r", encoding="utf-8") as f:
        html = f.read()
    return HTMLResponse(html.replace("{{TOKEN}}", token))

@app.get("/api/stats")
async def api_stats(token: str):
    if not verify_token(token):
        raise HTTPException(status_code=403)
    orders = load_orders()
    now = datetime.now()

    def in_period(o, days):
        try:
            created = datetime.strptime(o.get("created", ""), "%d.%m.%Y %H:%M")
            return (now - created).days <= days
        except:
            return False

    week = [o for o in orders if in_period(o, 7) and o.get("status") != "archived"]
    month = [o for o in orders if in_period(o, 30) and o.get("status") != "archived"]
    all_active = [o for o in orders if o.get("status") != "archived"]

    month_weight = sum(o.get("weight_actual", 0) for o in month)
    month_revenue = sum(o.get("total_price", 0) for o in month)

    status_counts = {}
    for o in all_active:
        s = o.get("status", "new")
        status_counts[s] = status_counts.get(s, 0) + 1

    sources = {}
    for o in month:
        s = o.get("source", "Telegram")
        sources[s] = sources.get(s, 0) + 1

    styles = {}
    for o in month:
        s = o.get("style", "")
        if s:
            styles[s] = styles.get(s, 0) + 1
    top_styles = sorted(styles.items(), key=lambda x: -x[1])[:5]

    # замовлень по днях за місяць
    days_chart = {}
    for o in month:
        try:
            d = datetime.strptime(o.get("created", ""), "%d.%m.%Y %H:%M").strftime("%d.%m")
            days_chart[d] = days_chart.get(d, 0) + 1
        except:
            pass

    return {
        "week_count": len(week),
        "month_count": len(month),
        "month_weight": round(month_weight, 1),
        "month_revenue": month_revenue,
        "status_counts": status_counts,
        "sources": sources,
        "top_styles": top_styles,
        "days_chart": days_chart,
    }

@app.get("/api/orders")
async def api_orders(token: str, status: str = "all"):
    if not verify_token(token):
        raise HTTPException(status_code=403)
    orders = load_orders()
    if status == "archived":
        filtered = [o for o in orders if o.get("status") == "archived"]
    elif status != "all":
        filtered = [o for o in orders if o.get("status", "new") == status and o.get("status") != "archived"]
    else:
        filtered = [o for o in orders if o.get("status") != "archived"]
    return list(reversed(filtered))

def generate_token(chat_id: int) -> str:
    token = secrets.token_urlsafe(32)
    tokens = load_tokens()
    tokens[token] = {
        "expires": (datetime.now() + timedelta(hours=24)).isoformat(),
        "chat_id": chat_id,
    }
    save_tokens(tokens)
    return token

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
