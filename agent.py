import os
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from openai import OpenAI
from dotenv import load_dotenv
from knowledge import SYSTEM_PROMPT, ESCALATION_KEYWORDS
from photo_search import find_photo, wants_photo
from learned_knowledge import load_facts, save_fact, get_facts_for_prompt

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_CHAT_ID"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", str(OWNER_ID)).split(",")]
ORDERS_FILE = "orders.json"
CHAT_LOG_FILE = "logs/conversations.log"

client = OpenAI(api_key=OPENAI_KEY)
logging.basicConfig(level=logging.INFO)
user_states = {}
chat_histories = {}
admin_modes = {}

# ===== КРОКИ АНКЕТИ =====
STEPS = [
    {"key": "source", "admin_only": True, "text": "Звідки клієнт?",
     "options": ["Telegram", "Viber", "Телефон", "OLX", "Сайт", "Інше ✏️"]},
    {"key": "type", "text": "Що замовляємо?",
     "options": ["Ланцюжок", "Браслет", "Хрестик", "Кулон", "Печатка", "Набір", "Інше ✏️"]},
    {"key": "style", "text": "Плетіння?",
     "options": ["Бісмарк", "Козацьке", "Рамзес", "Лисячий хвіст", "Візантія", "Водоспад", "Якірне", "Фараон", "Інше ✏️"]},
    {"key": "size", "text": "Довжина (см)?",
     "options": ["40см", "45см", "50см", "55см", "60см", "17см", "18см", "20см", "Інше ✏️"]},
    {"key": "weight", "text": "Маса виробу?",
     "options": ["Тонкий ~3-7г", "Середній ~8-15г", "Масивний ~20г+", "Не знаю ✏️"]},
    {"key": "coating", "text": "Покриття?",
     "options": ["Срібло біле", "Чорніння", "Інше ✏️"]},
    {"key": "clasp", "text": "Застібка?",
     "options": ["Карабін", "Коробочка 600грн", "Коробочка XL 1500грн", "Інше ✏️"]},
    {"key": "note", "text": "Додатково?\n(дедлайн, гравіювання, інше)",
     "options": ["Немає", "Є дедлайн ✏️", "Гравіювання тексту 500грн", "Гравіювання малюнку 700грн", "Інше ✏️"]},
    {"key": "contact", "text": "Контакт клієнта?\n(імʼя + телефон або Telegram)", "options": None},
]

def get_steps_for(is_admin):
    return [s for s in STEPS if not s.get("admin_only") or is_admin]

def make_keyboard(options):
    buttons = []
    row = []
    for i, opt in enumerate(options):
        row.append(InlineKeyboardButton(opt, callback_data=f"ans:{opt}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

def get_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {"step": None, "data": {}, "waiting_text": False, "is_admin_order": False}
    return user_states[chat_id]

def get_history(chat_id):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    return chat_histories[chat_id]

def log_conv(chat_id, username, direction, text):
    ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    who = f"@{username}" if username else f"id:{chat_id}"
    arrow = ">>>" if direction == "in" else "<<<"
    with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {who} {arrow} {text}\n")

def load_orders():
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_order(data):
    orders = load_orders()
    oid = f"IS-{str(len(orders)+1).zfill(3)}"
    orders.append({"id": oid, "created": datetime.now().strftime("%d.%m.%Y %H:%M"), "status": "new", **data})
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    return oid

def order_summary(oid, d, mention=""):
    source = f"Звідки: {d.get('source')}\n" if d.get('source') else ""
    return (
        f"🆕 НОВЕ ЗАМОВЛЕННЯ {oid}{mention}\n\n"
        f"{source}"
        f"Тип: {d.get('type','—')} — {d.get('style','—')}\n"
        f"Довжина: {d.get('size','—')} | Маса: {d.get('weight','—')}\n"
        f"Покриття: {d.get('coating','—')} | Застібка: {d.get('clasp','—')}\n"
        f"Додатково: {d.get('note','—')}\n"
        f"Контакт: {d.get('contact','—')}\n\n"
        f"/setstatus {oid} in_progress"
    )

async def send_step(chat_id, context, steps, step_idx):
    step = steps[step_idx]
    total = len(steps)
    text = f"[{step_idx+1}/{total}] {step['text']}"
    if step["options"]:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_keyboard(step["options"]))
    else:
        await context.bot.send_message(chat_id=chat_id, text=text)

async def finish_order(chat_id, username, context):
    state = get_state(chat_id)
    d = state["data"]
    oid = save_order(d)
    state["step"] = None
    state["data"] = {}
    state["waiting_text"] = False

    client_summary = (
        f"✅ Замовлення прийнято! {oid}\n\n"
        f"Тип: {d.get('type','—')} — {d.get('style','—')}\n"
        f"Довжина: {d.get('size','—')} | Маса: {d.get('weight','—')}\n"
        f"Покриття: {d.get('coating','—')} | Застібка: {d.get('clasp','—')}\n"
        f"Додатково: {d.get('note','—')}\n"
        f"Контакт: {d.get('contact','—')}\n\n"
        f"Владислав зв'яжеться з вами 🩶"
    )
    await context.bot.send_message(chat_id=chat_id, text=client_summary)

    mention = f"\n@{username}" if username else ""
    await context.bot.send_message(chat_id=OWNER_ID, text=order_summary(oid, d, mention))

def ask_ai(chat_id, text, is_admin=False):
    try:
        history = get_history(chat_id)
        history.append({"role": "user", "content": text})
        if len(history) > 10:
            history = history[-10:]
        chat_histories[chat_id] = history
        extra = get_facts_for_prompt()
        system = SYSTEM_PROMPT + extra
        if is_admin:
            system += """

ТИ ЗАРАЗ В РЕЖИМІ АДМІНА — спілкуєшся з Владиславом (власником InSilver).
1. Якщо Владислав пише факт — перефразуй і запитай підтвердження. Додай: LEARN|||перефразований факт
2. Якщо відповідає "так/вірно" — збережи. Додай: CONFIRM|||
3. Якщо питає як клієнт — відповідай нормально
Завжди додавай в кінці: [🔧 Режим адміна]"""
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}] + history,
            max_tokens=500, temperature=0.5,
        )
        reply = r.choices[0].message.content.strip()
        history.append({"role": "assistant", "content": reply})
        chat_histories[chat_id] = history
        return reply
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        return None

def needs_escalation(text):
    return any(k in text.lower() for k in ESCALATION_KEYWORDS)

# ===== КОМАНДИ =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    admin_modes[chat_id] = False
    await update.message.reply_text(
        "Вітаємо в InSilver! 🩶\n\nМи виготовляємо вироби зі срібла 925°\n"
        "Ланцюжки, браслети, кулони, печатки, набори\n\n"
        "/order — оформити замовлення\n/catalog — каталог\n/contacts — контакти"
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_IDS:
        await update.message.reply_text("Ця команда недоступна.")
        return
    admin_modes[chat_id] = not admin_modes.get(chat_id, False)
    chat_histories[chat_id] = []
    if admin_modes[chat_id]:
        facts = load_facts()
        await update.message.reply_text(
            "🔧 Режим адміна увімкнено\n\n"
            "• Писати факти про вироби — запам'ятаю\n"
            "• Тестувати бота як клієнт\n"
            "• /neworder — нове замовлення вручну\n"
            "• /orders — всі замовлення\n"
            "• /facts — збережені знання\n\n"
            f"Збережено фактів: {len(facts)}\n"
            "/admin — вимкнути режим\n\n[🔧 Режим адміна]"
        )
    else:
        await update.message.reply_text("✅ Режим адміна вимкнено.")

async def facts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    facts = load_facts()
    if not facts:
        await update.message.reply_text("Збережених знань поки немає.")
        return
    lines = [f"[{f['date']}] {f['fact']}" for f in facts]
    await update.message.reply_text(f"📚 Збережені знання ({len(facts)}):\n\n" + "\n\n".join(lines))

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    orders = load_orders()
    if not orders:
        await update.message.reply_text("Замовлень поки немає.")
        return
    STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦"}
    lines = ["📋 Замовлення (останні 10):\n"]
    for o in reversed(orders[-10:]):
        e = STATUS_EMOJI.get(o.get("status", "new"), "🆕")
        lines.append(
            f"{e} {o['id']} — {o.get('type','?')} {o.get('style','')}\n"
            f"   {o.get('contact','—')} | {o.get('size','—')}\n"
            f"   Додатково: {o.get('note','—')}\n"
        )
    await update.message.reply_text("\n".join(lines))

async def neworder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_IDS:
        await update.message.reply_text("Ця команда тільки для адміна.")
        return
    state = get_state(chat_id)
    state["step"] = 0
    state["data"] = {}
    state["waiting_text"] = False
    state["is_admin_order"] = True
    steps = get_steps_for(True)
    await send_step(chat_id, context, steps, 0)

async def order_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    state["step"] = 0
    state["data"] = {}
    state["waiting_text"] = False
    state["is_admin_order"] = False
    steps = get_steps_for(False)
    await send_step(chat_id, context, steps, 0)

async def setstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Використання:\n/setstatus IS-001 in_progress\n\nСтатуси: new, in_progress, ready, sent")
        return
    oid, new_status = args[0].upper(), args[1].lower()
    valid = ["new", "in_progress", "ready", "sent"]
    if new_status not in valid:
        await update.message.reply_text(f"Невірний статус. Доступні: {', '.join(valid)}")
        return
    orders = load_orders()
    found = False
    for o in orders:
        if o["id"] == oid:
            o["status"] = new_status
            found = True
            break
    if not found:
        await update.message.reply_text(f"Замовлення {oid} не знайдено.")
        return
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦"}
    await update.message.reply_text(f"{STATUS_EMOJI.get(new_status)} {oid} — статус змінено на {new_status}")

async def contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Telegram: @InSilver_925\nТелефон: 0936931493\n"
        "Сайт: www.insilver.pp.ua\nГрупа: t.me/insilver_ua\nOLX: insilver.olx.ua"
    )

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Каталог InSilver (срібло 925°):\n\nЛанцюжки — 15 видів\nБраслети — 14 видів\n"
        "Кулони, хрестики, ладанки\nПечатки та персні\nНабори\n\nФото: www.insilver.pp.ua"
    )

# ===== CALLBACK (кнопки) =====

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if not data.startswith("ans:"):
        return

    value = data[4:]
    state = get_state(chat_id)

    if state["step"] is None:
        return

    is_admin_order = state.get("is_admin_order", False)
    steps = get_steps_for(is_admin_order)

    if value.endswith("✏️") or value == "Не знаю ✏️" or value == "Є дедлайн ✏️":
        state["waiting_text"] = True
        key = steps[state["step"]]["key"]
        hints = {
            "type": "Введіть тип виробу:",
            "style": "Введіть назву плетіння:",
            "size": "Введіть довжину в см:",
            "weight": "Введіть масу в грамах:",
            "coating": "Введіть покриття:",
            "clasp": "Введіть тип застібки:",
            "note": "Введіть додаткову інформацію:",
            "source": "Введіть звідки клієнт:",
        }
        await query.message.reply_text(hints.get(key, "Введіть значення:"))
        return

    # зберігаємо відповідь
    key = steps[state["step"]]["key"]
    state["data"][key] = value
    await query.message.edit_reply_markup(reply_markup=None)

    state["step"] += 1
    if state["step"] < len(steps):
        await send_step(chat_id, context, steps, state["step"])
    else:
        username = query.from_user.username or query.from_user.first_name
        await finish_order(chat_id, username, context)

# ===== ТЕКСТОВІ ПОВІДОМЛЕННЯ =====

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    user = update.effective_user
    username = user.username or user.first_name or str(chat_id)
    log_conv(chat_id, username, "in", text)

    is_admin_chat = (chat_id in ADMIN_IDS and admin_modes.get(chat_id, False))
    state = get_state(chat_id)
    is_admin_order = state.get("is_admin_order", False)
    steps = get_steps_for(is_admin_order)

    # очікуємо вільний текст в анкеті — ПЕРШОЧЕРГОВА ПЕРЕВІРКА
    if state["step"] is not None and (state.get("waiting_text") or steps[state["step"]]["options"] is None):
        key = steps[state["step"]]["key"]
        state["data"][key] = text
        state["waiting_text"] = False
        state["step"] += 1
        if state["step"] < len(steps):
            await send_step(chat_id, context, steps, state["step"])
        else:
            await finish_order(chat_id, username, context)
        return

    # ескалація
    if needs_escalation(text) and not is_admin_chat:
        reply = "Передаю майстру Владиславу 🙏\nТел: 0936931493"
        await update.message.reply_text(reply)
        await context.bot.send_message(chat_id=OWNER_ID, text=f"⚠️ УВАГА\n@{username}: {text}")
        return

    # фото
    if wants_photo(text, chat_id):
        photo_path = find_photo(text, chat_id)
        if photo_path and os.path.exists(photo_path):
            await update.message.reply_photo(photo=open(photo_path, "rb"), caption="Ось приклад з нашої майстерні 🩶")
            log_conv(chat_id, "bot", "out", f"[фото: {photo_path}]")
            if not is_admin_chat:
                return

    # AI
    reply = ask_ai(chat_id, text, is_admin=is_admin_chat)
    if reply is None:
        reply = "Вибачте, технічна перерва. Телефонуйте: 0936931493 🙏"
        await context.bot.send_message(chat_id=OWNER_ID, text=f"🔴 OpenAI недоступний!\n@{username}: {text}")
        await update.message.reply_text(reply)
        return

    clean_reply = reply
    if is_admin_chat and "LEARN|||" in reply:
        parts = reply.split("LEARN|||")
        clean_reply = parts[0].strip()
        pending_fact = parts[1].split("\n")[0].strip()
        user_states[chat_id]["pending_fact"] = pending_fact
        user_states[chat_id]["pending_original"] = text

    if is_admin_chat and "CONFIRM|||" in reply:
        clean_reply = reply.replace("CONFIRM|||", "").strip()
        pending = user_states.get(chat_id, {}).get("pending_fact")
        original = user_states.get(chat_id, {}).get("pending_original", "")
        if pending:
            save_fact(pending, original)
            user_states[chat_id]["pending_fact"] = None
            clean_reply += "\n\n✅ Збережено в базу знань!"

    await update.message.reply_text(clean_reply)
    log_conv(chat_id, "bot", "out", clean_reply)

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("facts", facts_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("order", order_cmd))
    app.add_handler(CommandHandler("neworder", neworder_cmd))
    app.add_handler(CommandHandler("catalog", catalog))
    app.add_handler(CommandHandler("contacts", contacts))
    app.add_handler(CommandHandler("setstatus", setstatus_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("InSilver агент запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
