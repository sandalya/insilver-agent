PRICE_PER_GRAM = {
    "бісмарк": 165, "якірне": 165, "панцирне": 165, "пітон": 165, "фігаро": 165,
    "рамзес": 170, "козацьке": 170, "фараон": 170, "імператор": 170, "кардинал": 170,
    "водоспад": 170, "тризуб": 170, "лисячий хвіст": 170, "тракторне": 170, "візантія": 170, "біт": 170,
    "якір 5+2": 165,
}
DEFAULT_PRICE = 170

def get_price_per_gram(style):
    if not style:
        return DEFAULT_PRICE
    style_lower = style.lower()
    for key, price in PRICE_PER_GRAM.items():
        if key in style_lower:
            return price
    return DEFAULT_PRICE

import os
import json
import logging
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from openai import OpenAI
from dotenv import load_dotenv
from knowledge import SYSTEM_PROMPT, ESCALATION_KEYWORDS
from photo_search import find_photo
from web_server import generate_token
from learned_knowledge import load_facts, save_fact
from knowledge_base import load_kb, add_entry, attach_media, get_last_entry_id, find_response, format_learned_summary

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_CHAT_ID"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", str(OWNER_ID)).split(",")]
ORDERS_FILE = "orders.json"
MONITOR_CHAT_ID = int(os.getenv("MONITOR_CHAT_ID", "0"))
CHAT_LOG_FILE = "logs/conversations.log"
ADMIN_MODES_FILE = "admin_modes.json"

client = OpenAI(api_key=OPENAI_KEY)
logging.basicConfig(level=logging.INFO)

user_states = {}
chat_histories = {}

def load_admin_modes():
    if os.path.exists(ADMIN_MODES_FILE):
        with open(ADMIN_MODES_FILE, "r") as f:
            return {int(k): v for k, v in json.load(f).items()}
    return {}

def save_admin_modes(modes):
    with open(ADMIN_MODES_FILE, "w") as f:
        json.dump({str(k): v for k, v in modes.items()}, f)

admin_modes = load_admin_modes()

SIZE_OPTIONS = {
    "ланцюжок": ["40см", "45см", "50см", "55см", "60см", "65см", "70см", "75см", "Інше"],
    "браслет":  ["18см", "19см", "20см", "21см", "22см", "23см", "24см", "25см", "Інше"],
}
SIZE_OPTIONS_DEFAULT = ["40см", "45см", "50см", "55см", "60см", "Інше"]

def get_size_options(order_data):
    t = order_data.get("type", "").lower()
    for key, opts in SIZE_OPTIONS.items():
        if key in t:
            return opts
    return SIZE_OPTIONS_DEFAULT

STEPS_CLIENT = [
    {"key": "type",    "text": "Що замовляємо?",
     "options": ["⛓️ Ланцюжок", "📿 Браслет", "✝️ Хрестик", "💎 Кулон", "💍 Печатка", "🎁 Набір", "✏️ Інше"]},
    {"key": "style",   "text": "Плетіння?",
     "options": ["Бісмарк", "Козацьке", "Рамзес", "Лисячий хвіст", "Візантія", "Водоспад", "Якірне", "Фараон", "Інше"]},
    {"key": "size",    "text": "Довжина (см)?", "options": None},
    {"key": "weight",  "text": "Вкажіть бажану масу виробу\n(наприклад: 30г, 100г або не знаю):", "options": None},
    {"key": "coating", "text": "Покриття?",
     "options": ["⚪️ Срібло біле", "⚫️ Чорніння", "✨ Родіювання +95грн/г", "✏️ Інше"]},
    {"key": "clasp",   "text": "Застібка?",
     "options": ["🔗 Карабін", "📦 Коробочка 600грн", "📦 Коробочка XL 1500грн", "✏️ Інше"]},
    {"key": "note",    "text": "Додатково?\n(дедлайн, гравіювання, побажання)",
     "options": ["➖ Немає", "✍️ Гравіювання тексту 500грн", "✍️ Гравіювання малюнку 700грн", "✏️ Інше"]},
    {"key": "contact", "text": "Ваше ім'я та телефон або Telegram?", "options": None},
]

STEPS_ADMIN = [
    {"key": "source", "text": "Звідки клієнт?",
     "options": ["Telegram", "Viber", "Телефон", "OLX", "Сайт", "Інше"]},
] + STEPS_CLIENT

PHOTO_CAPTIONS = [
    "Ось наша робота",
    "Виготовлено в InSilver",
    "Срібло 925, ручна робота",
    "Майстерня InSilver",
    "Наш виріб зі срібла 925",
]

def get_photo_caption(photo_path):
    import random
    if os.path.exists("photo_index.json"):
        with open("photo_index.json", "r", encoding="utf-8") as f:
            index = json.load(f)
        for item in index:
            if item.get("photo") == photo_path:
                text = item.get("original_text", "").strip()
                if text and len(text) < 200:
                    return text
    return random.choice(PHOTO_CAPTIONS)

def make_keyboard(options, show_back=False):
    buttons, row = [], []
    for opt in options:
        row.append(InlineKeyboardButton(opt, callback_data="ans:" + opt))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    if show_back:
        buttons.append([InlineKeyboardButton("Назад", callback_data="order_back")])
    return InlineKeyboardMarkup(buttons)

def get_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {"step": None, "data": {}, "waiting_text": False, "is_admin_order": False, "last_kb_entry": None}
    return user_states[chat_id]

def get_history(chat_id):
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    return chat_histories[chat_id]

def log_conv(chat_id, username, direction, text):
    ts = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    who = "@" + str(username) if username else "id:" + str(chat_id)
    arrow = ">>>" if direction == "in" else "<<<"
    with open(CHAT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write("[" + ts + "] " + who + " " + arrow + " " + text + "\n")

async def monitor_log(context, username, user_text, bot_reply, source="AI", is_admin=False):
    if not MONITOR_CHAT_ID:
        return
    try:
        role = "[ADMIN]" if is_admin else "[client]"
        msg = role + " " + str(username) + ":\n" + user_text + "\n\n<< Bot [" + source + "]:\n" + bot_reply
        await context.bot.send_message(chat_id=MONITOR_CHAT_ID, text=msg)
    except Exception as e:
        logging.error("Monitor error: " + str(e))

def load_orders():
    if os.path.exists(ORDERS_FILE):
        with open(ORDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_order(data):
    orders = load_orders()
    oid = "IS-" + str(len(orders)+1).zfill(3)
    orders.append({"id": oid, "created": datetime.now().strftime("%d.%m.%Y %H:%M"), "status": "new", **data})
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    return oid

def ask_ai(chat_id, text, is_admin=False):
    try:
        history = get_history(chat_id)
        history.append({"role": "user", "content": text})
        if len(history) > 10:
            history = history[-10:]
        chat_histories[chat_id] = history
        system = SYSTEM_PROMPT + (
            "\n\nFORMAT (strict):\n"
            "Line 1: REASON|||reason\n"
            "Line 2 if needed: START_ORDER\n"
            "Line 3 if needed: PHOTO_REQUEST|||type or all\n"
            "Then: reply text (1-3 sentences)\n"
            "Tags are hidden from client. Do NOT mention /order."
        )
        if is_admin:
            system += (
                "\n\nExtra (owner Vladyslav):\n"
                "If message contains new fact about products/prices/conditions — append: LEARN|||fact in one sentence\n"
                "Otherwise reply as normal user."
            )
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": system}] + history,
            max_tokens=800, temperature=0.5,
        )
        reply = r.choices[0].message.content.strip()
        logging.info("GPT RAW: " + repr(reply[:200]))
        history.append({"role": "assistant", "content": reply})
        chat_histories[chat_id] = history
        return reply
    except Exception as e:
        logging.error("OpenAI error: " + str(e))
        return None

def needs_escalation(text):
    return any(k in text.lower() for k in ESCALATION_KEYWORDS)

async def send_step(chat_id, context, steps, step_idx, order_data=None):
    step = steps[step_idx]
    text = "[" + str(step_idx+1) + "/" + str(len(steps)) + "] " + step["text"]
    options = step["options"]
    if step["key"] == "size" and order_data is not None:
        options = get_size_options(order_data)
    show_back = step_idx > 0
    if options:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_keyboard(options, show_back=show_back))
    else:
        back_markup = InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="order_back")]]) if show_back else None
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=back_markup)

async def finish_order(chat_id, username, context):
    state = get_state(chat_id)
    d = state["data"]
    d["client_chat_id"] = chat_id
    oid = save_order(d)
    state["step"] = None
    state["data"] = {}
    state["waiting_text"] = False
    source = ("Звідки: " + str(d.get("source")) + "\n") if d.get("source") else ""
    client_msg = (
        "Замовлення прийнято! " + oid + "\n\n"
        "Тип: " + str(d.get("type","—")) + " — " + str(d.get("style","—")) + "\n"
        "Довжина: " + str(d.get("size","—")) + " | Маса: " + str(d.get("weight","—")) + "\n"
        "Покриття: " + str(d.get("coating","—")) + " | Застібка: " + str(d.get("clasp","—")) + "\n"
        "Додатково: " + str(d.get("note","—")) + "\n"
        "Контакт: " + str(d.get("contact","—")) + "\n\n"
        "Владислав зв'яжеться з вами"
    )
    await context.bot.send_message(chat_id=chat_id, text=client_msg)
    mention = "\n@" + str(username) if username else ""
    owner_msg = (
        "НОВЕ ЗАМОВЛЕННЯ " + oid + mention + "\n\n"
        + source +
        "Тип: " + str(d.get("type","—")) + " — " + str(d.get("style","—")) + "\n"
        "Довжина: " + str(d.get("size","—")) + " | Маса: " + str(d.get("weight","—")) + "\n"
        "Покриття: " + str(d.get("coating","—")) + " | Застібка: " + str(d.get("clasp","—")) + "\n"
        "Додатково: " + str(d.get("note","—")) + "\n"
        "Контакт: " + str(d.get("contact","—")) + "\n\n"
        "/setstatus " + oid + " in_progress"
    )
    await context.bot.send_message(chat_id=OWNER_ID, text=owner_msg, reply_markup=make_status_keyboard(oid))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    admin_modes[chat_id] = False
    save_admin_modes(admin_modes)
    await update.message.reply_text(
        "Вітаємо в InSilver!\n\nВироби зі срібла 925\n"
        "Ланцюжки, браслети, кулони, печатки, набори\n\n"
        "/order — оформити замовлення\n/catalog — каталог\n/contacts — контакти"
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_IDS:
        await update.message.reply_text("Ця команда недоступна.")
        return
    admin_modes[chat_id] = not admin_modes.get(chat_id, False)
    save_admin_modes(admin_modes)
    chat_histories[chat_id] = []
    if admin_modes[chat_id]:
        kb_items = load_kb()
        await update.message.reply_text(
            "Режим адміна увімкнено\n\n"
            "Розповідай про вироби — запам'ятаю\n"
            "Кидай фото після факту — збережу разом\n"
            "Питай 'що вивчили' — покажу базу знань\n\n"
            "/neworder — нове замовлення\n"
            "/orders — всі замовлення\n"
            "/kb — база знань\n"
            "/stats — статистика\n\n"
            "База знань: " + str(len(kb_items)) + " записів\n"
            "/admin — вимкнути"
        )
    else:
        await update.message.reply_text("Режим адміна вимкнено.")

async def kb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    text = format_learned_summary()
    for i in range(0, len(text), 4000):
        await update.message.reply_text(text[i:i+4000])

async def facts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    facts = load_facts()
    if not facts:
        await update.message.reply_text("Збережених знань поки немає.")
        return
    lines = ["#" + str(i+1) + " [" + f["date"] + "]\n" + f["fact"] for i, f in enumerate(facts)]
    await update.message.reply_text("Збережені знання (" + str(len(facts)) + "):\n\n" + "\n\n".join(lines))

async def delfact_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Використання: /delfact 1")
        return
    try:
        idx = int(args[0]) - 1
    except ValueError:
        await update.message.reply_text("Введіть номер факту.")
        return
    facts = load_facts()
    if idx < 0 or idx >= len(facts):
        await update.message.reply_text("Не знайдено. Всього: " + str(len(facts)))
        return
    removed = facts.pop(idx)
    with open("learned_facts.json", "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    await update.message.reply_text("Видалено:\n" + removed["fact"])

def make_filter_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Нові", callback_data="filter:new"),
         InlineKeyboardButton("В роботі", callback_data="filter:in_progress")],
        [InlineKeyboardButton("Готові", callback_data="filter:ready"),
         InlineKeyboardButton("Відправлені", callback_data="filter:sent")],
        [InlineKeyboardButton("Призупинені", callback_data="filter:paused"),
         InlineKeyboardButton("Всі активні", callback_data="filter:all")],
        [InlineKeyboardButton("Архів", callback_data="filter:archived")],
    ])

async def show_orders(chat_id, context, filter_status="all"):
    orders = load_orders()
    STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦", "paused": "⏸", "archived": "🗃"}
    if filter_status == "archived":
        filtered = [o for o in orders if o.get("status") == "archived"]
    elif filter_status != "all":
        filtered = [o for o in orders if o.get("status", "new") == filter_status and o.get("status") != "archived"]
    else:
        filtered = [o for o in orders if o.get("status") != "archived"]
    if not filtered:
        await context.bot.send_message(chat_id=chat_id, text="Замовлень немає.")
        return
    for o in reversed(filtered[-10:]):
        e = STATUS_EMOJI.get(o.get("status", "new"), "?")
        text = (
            "[" + e + "] " + o["id"] + " — " + str(o.get("type","?")) + " " + str(o.get("style","")) + "\n"
            "Клієнт: " + str(o.get("contact","—")) + "\n"
            "Довжина: " + str(o.get("size","—")) + " | Маса: " + str(o.get("weight","—")) + "\n"
            "Покриття: " + str(o.get("coating","—")) + " | Застібка: " + str(o.get("clasp","—")) + "\n"
            "Додатково: " + str(o.get("note","—")) + "\n"
            "Створено: " + str(o.get("created","—"))
        )
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_status_keyboard(o["id"]))

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Використання: /search Сашко")
        return
    q = " ".join(args).lower()
    orders = load_orders()
    results = [o for o in orders if q in o.get("id","").lower() or q in o.get("contact","").lower() or q in o.get("type","").lower()]
    if not results:
        await update.message.reply_text("Нічого не знайдено: " + q)
        return
    await update.message.reply_text("Знайдено: " + str(len(results)))
    STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦", "paused": "⏸", "archived": "🗃"}
    for o in results:
        e = STATUS_EMOJI.get(o.get("status","new"), "?")
        await update.message.reply_text(
            "[" + e + "] " + o["id"] + " — " + str(o.get("type","?")) + " " + str(o.get("style","")) + "\n"
            "Клієнт: " + str(o.get("contact","—")) + "\nСтворено: " + str(o.get("created","—")),
            reply_markup=make_status_keyboard(o["id"])
        )

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    await update.message.reply_text("Показати замовлення:", reply_markup=make_filter_keyboard())

async def neworder_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_IDS:
        return
    state = get_state(chat_id)
    state["step"] = 0
    state["data"] = {}
    state["waiting_text"] = False
    state["is_admin_order"] = True
    await send_step(chat_id, context, STEPS_ADMIN, 0, state["data"])

async def order_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    state["step"] = 0
    state["data"] = {}
    state["waiting_text"] = False
    state["is_admin_order"] = False
    await send_step(chat_id, context, STEPS_CLIENT, 0, state["data"])

async def setstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Використання: /setstatus IS-001 in_progress")
        return
    oid, new_status = args[0].upper(), args[1].lower()
    valid = ["new", "in_progress", "ready", "sent", "paused", "archived"]
    if new_status not in valid:
        await update.message.reply_text("Невірний статус. Доступні: " + ", ".join(valid))
        return
    orders = load_orders()
    found = False
    for o in orders:
        if o["id"] == oid:
            o["status"] = new_status
            found = True
            break
    if not found:
        await update.message.reply_text("Замовлення " + oid + " не знайдено.")
        return
    with open(ORDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    await update.message.reply_text(oid + " — статус: " + new_status)

async def contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Telegram: @InSilver_925\nТелефон: 0936931493\n"
        "Сайт: www.insilver.pp.ua\nГрупа: t.me/insilver_ua\nOLX: insilver.olx.ua"
    )

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Каталог InSilver (срібло 925):\n\nЛанцюжки — 15 видів\nБраслети — 14 видів\n"
        "Кулони, хрестики, ладанки\nПечатки та персні\nНабори\n\nФото: www.insilver.pp.ua"
    )

def make_status_keyboard(oid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ В роботі", callback_data="status:" + oid + ":in_progress"),
         InlineKeyboardButton("✅ Готово", callback_data="status:" + oid + ":ready")],
        [InlineKeyboardButton("📦 Відправлено", callback_data="status:" + oid + ":sent"),
         InlineKeyboardButton("⏸ Призупинено", callback_data="status:" + oid + ":paused")],
        [InlineKeyboardButton("🗃 Архів", callback_data="status:" + oid + ":archived"),
         InlineKeyboardButton("✏️ Редагувати", callback_data="edit:" + oid)],
        [InlineKeyboardButton("🗑 Видалити", callback_data="delete:" + oid)],
    ])

def make_edit_keyboard(oid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Контакт", callback_data="editfield:" + oid + ":contact"),
         InlineKeyboardButton("Розмір", callback_data="editfield:" + oid + ":size")],
        [InlineKeyboardButton("Маса", callback_data="editfield:" + oid + ":weight"),
         InlineKeyboardButton("Покриття", callback_data="editfield:" + oid + ":coating")],
        [InlineKeyboardButton("Застібка", callback_data="editfield:" + oid + ":clasp"),
         InlineKeyboardButton("Додатково", callback_data="editfield:" + oid + ":note")],
        [InlineKeyboardButton("Назад", callback_data="editfield:" + oid + ":cancel")],
    ])

def make_confirm_delete_keyboard(oid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Так, видалити", callback_data="confirmdelete:" + oid),
         InlineKeyboardButton("Скасувати", callback_data="editfield:" + oid + ":cancel")],
    ])

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = query.message.chat_id
    data = query.data

    if data == "order_back":
        state = get_state(chat_id)
        if state["step"] is None or state["step"] == 0:
            return
        steps = STEPS_ADMIN if state.get("is_admin_order") else STEPS_CLIENT
        state["step"] -= 1
        prev_key = steps[state["step"]]["key"]
        state["data"].pop(prev_key, None)
        state["waiting_text"] = False
        await send_step(chat_id, context, steps, state["step"], state["data"])
        return

    if data.startswith("edit:"):
        oid = data.split(":")[1]
        await query.message.reply_text("Що редагуємо в " + oid + "?", reply_markup=make_edit_keyboard(oid))
        return

    if data.startswith("editfield:"):
        parts = data.split(":")
        oid, field = parts[1], parts[2]
        if field == "cancel":
            await query.message.delete()
            return
        field_names = {"contact": "контакт", "size": "довжину", "weight": "масу", "coating": "покриття", "clasp": "застібку", "note": "додаткову інфо"}
        state = get_state(chat_id)
        state["editing"] = {"oid": oid, "field": field}
        await query.message.reply_text("Введіть " + field_names.get(field, field) + " для " + oid + ":")
        return

    if data.startswith("delete:"):
        oid = data.split(":")[1]
        await query.message.reply_text("Видалити " + oid + "?", reply_markup=make_confirm_delete_keyboard(oid))
        return

    if data.startswith("confirmdelete:"):
        oid = data.split(":")[1]
        orders = load_orders()
        orders = [o for o in orders if o["id"] != oid]
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        await query.message.edit_text(oid + " видалено.")
        return

    if data.startswith("filter:"):
        await show_orders(chat_id, context, data.split(":")[1])
        return

    if data.startswith("status:"):
        parts = data.split(":")
        oid, new_status = parts[1], parts[2]
        STATUS_NAMES = {"new": "Нове", "in_progress": "В роботі", "ready": "Готово", "sent": "Відправлено", "paused": "Призупинено", "archived": "Архів"}
        orders = load_orders()
        for o in orders:
            if o["id"] == oid:
                o["status"] = new_status
                break
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        name = STATUS_NAMES.get(new_status, new_status)
        try:
            base_text = re.sub(r"\n\nСтатус змінено:.*$", "", query.message.text, flags=re.DOTALL).strip()
            await query.edit_message_text(text=base_text + "\n\nСтатус змінено: " + name, reply_markup=make_status_keyboard(oid))
        except Exception:
            pass
        if new_status == "ready":
            state = get_state(chat_id)
            state["waiting_weight"] = oid
            await context.bot.send_message(chat_id=chat_id, text="Введіть вагу " + oid + " в грамах (наприклад: 23.5):")
        status_msgs = {
            "in_progress": "Ваше замовлення " + oid + " прийнято в роботу!",
            "ready": "Ваше замовлення " + oid + " готове! Владислав зв'яжеться для відправки.",
            "sent": "Ваше замовлення " + oid + " відправлено! Очікуйте на Новій Пошті.",
            "paused": "По замовленню " + oid + " є питання. Владислав зв'яжеться.",
        }
        if new_status in status_msgs:
            orders = load_orders()
            for o in orders:
                if o["id"] == oid:
                    cid = o.get("client_chat_id")
                    if cid:
                        try:
                            await context.bot.send_message(chat_id=cid, text=status_msgs[new_status])
                        except Exception as e:
                            logging.error("Сповіщення клієнта: " + str(e))
                    break
        return

    if not data.startswith("ans:"):
        return

    value = data[4:]
    state = get_state(chat_id)
    if state["step"] is None:
        return
    steps = STEPS_ADMIN if state.get("is_admin_order") else STEPS_CLIENT

    if value == "Інше":
        state["waiting_text"] = True
        hints = {
            "type": "Введіть тип виробу:", "style": "Введіть плетіння:",
            "size": "Введіть довжину в см:", "coating": "Введіть покриття:",
            "clasp": "Введіть застібку:", "note": "Введіть додатково:",
            "source": "Введіть звідки клієнт:"
        }
        await query.message.reply_text(hints.get(steps[state["step"]]["key"], "Введіть значення:"))
        return

    state["data"][steps[state["step"]]["key"]] = value
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass
    state["step"] += 1
    if state["step"] < len(steps):
        await send_step(chat_id, context, steps, state["step"], state["data"])
    else:
        username = query.from_user.username or query.from_user.first_name
        await finish_order(chat_id, username, context)

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    is_admin_chat = chat_id in ADMIN_IDS and admin_modes.get(chat_id, False)

    if not is_admin_chat:
        await update.message.reply_text("Дякуємо за фото! Якщо хочете замовити — напишіть /order")
        return

    photo = update.message.photo[-1]
    caption = update.message.caption or ""
    state = get_state(chat_id)
    last_kb_id = state.get("last_kb_entry")

    os.makedirs("channel_photos", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = "channel_photos/admin_photo_" + ts + ".jpg"
    tg_file = await context.bot.get_file(photo.file_id)
    await tg_file.download_to_drive(filename)

    if last_kb_id:
        attach_media(last_kb_id, {"type": "photo", "path": filename})
        await update.message.reply_text("Фото прикріплено до останнього запису бази знань.")
        return

    keywords = [w.lower() for w in caption.split() if len(w) > 2]
    type_map = {
        "браслет": ["браслет"], "ланцюжок": ["ланцюжок", "ланцюг"],
        "кулон": ["кулон"], "обручка": ["обручка", "перстень", "печатка"], "хрестик": ["хрестик"]
    }
    item_type = ""
    for t, words in type_map.items():
        if any(w in caption.lower() for w in words):
            item_type = t
            break

    if os.path.exists("photo_index.json"):
        with open("photo_index.json", "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = []
    index.append({
        "photo": filename, "date": datetime.now().strftime("%d.%m.%Y"),
        "original_text": caption, "type": item_type,
        "name": caption[:50], "keywords": keywords, "description": caption
    })
    with open("photo_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    await update.message.reply_text("Фото збережено в каталог.")

SILVER_FILE = "silver_balance.json"

def load_silver():
    if not os.path.exists(SILVER_FILE):
        return {"total": 0, "used": 0, "history": []}
    with open(SILVER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_silver(data):
    with open(SILVER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

async def silver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    s = load_silver()
    balance = round(s["total"] - s["used"], 1)
    await update.message.reply_text(
        "Баланс срібла\n\nКуплено: " + str(s["total"]) + " г\n"
        "Використано: " + str(s["used"]) + " г\nЗалишок: " + str(balance) + " г\n\n" +
        ("Мало! Час поповнити." if balance < 100 else "Запас є.")
    )

async def addsilver_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Використання: /addsilver 500")
        return
    try:
        grams = float(args[0].replace(",", "."))
        s = load_silver()
        s["total"] += grams
        s["history"].append({"date": datetime.now().strftime("%d.%m.%Y %H:%M"), "action": "купівля", "grams": grams})
        save_silver(s)
        balance = round(s["total"] - s["used"], 1)
        await update.message.reply_text("Додано " + str(grams) + " г\nЗалишок: " + str(balance) + " г")
    except ValueError:
        await update.message.reply_text("Введіть число: /addsilver 500")

async def web_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in ADMIN_IDS:
        return
    token = generate_token(chat_id)
    base_url = os.getenv("NGROK_URL", "http://192.168.72.210:8000")
    url = base_url + "/auth/" + token
    await update.message.reply_text("Ваша панель:\n" + url + "\n\nДіє 24 години.")

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    orders = load_orders()
    now = datetime.now()
    def in_period(o, days):
        try:
            return (now - datetime.strptime(o.get("created",""), "%d.%m.%Y %H:%M")).days <= days
        except:
            return False
    week = [o for o in orders if in_period(o, 7) and o.get("status") != "archived"]
    month = [o for o in orders if in_period(o, 30) and o.get("status") != "archived"]
    month_weight = sum(o.get("weight_actual", 0) for o in month)
    month_revenue = sum(o.get("total_price", 0) for o in month)
    sources = {}
    for o in month:
        s = o.get("source", "Не вказано")
        sources[s] = sources.get(s, 0) + 1
    styles = {}
    for o in month:
        s = o.get("style", "")
        if s:
            styles[s] = styles.get(s, 0) + 1
    top_styles = sorted(styles.items(), key=lambda x: -x[1])[:5]
    text = (
        "Статистика InSilver\n\n"
        "За місяць:\n  Вага: " + str(round(month_weight,1)) + " г\n"
        "  Виручка: " + str(month_revenue) + " грн\n"
        "  Замовлень: " + str(len(month)) + "\n\n"
        "За тиждень: " + str(len(week)) + "\n\n"
        "Звідки клієнти:\n" + "\n".join("  " + k + ": " + str(v) for k,v in sorted(sources.items(), key=lambda x:-x[1])) +
        "\n\nТоп плетінь:\n" + "\n".join("  " + k + ": " + str(v) for k,v in top_styles)
    )
    await update.message.reply_text(text)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    user = update.effective_user
    username = user.username or user.first_name or str(chat_id)
    log_conv(chat_id, username, "in", text)

    is_admin_chat = chat_id in ADMIN_IDS and admin_modes.get(chat_id, False)
    state = get_state(chat_id)
    steps = STEPS_ADMIN if state.get("is_admin_order") else STEPS_CLIENT

    if state.get("waiting_weight") and chat_id in ADMIN_IDS and state.get("step") is None:
        oid = state["waiting_weight"]
        try:
            weight_actual = float(text.replace(",", "."))
            orders = load_orders()
            for o in orders:
                if o["id"] == oid:
                    price_per_gram = get_price_per_gram(o.get("style", ""))
                    total_price = round(weight_actual * price_per_gram)
                    o["weight_actual"] = weight_actual
                    o["price_per_gram"] = price_per_gram
                    o["total_price"] = total_price
                    break
            with open(ORDERS_FILE, "w", encoding="utf-8") as f:
                json.dump(orders, f, ensure_ascii=False, indent=2)
            s = load_silver()
            s["used"] = round(s["used"] + weight_actual, 2)
            s["history"].append({"date": datetime.now().strftime("%d.%m.%Y %H:%M"), "action": "замовлення", "grams": -weight_actual, "order_id": oid})
            save_silver(s)
            balance = round(s["total"] - s["used"], 1)
            state["waiting_weight"] = None
            await update.message.reply_text(
                oid + " — вага " + str(weight_actual) + "г\n"
                "Ціна за грам: " + str(price_per_gram) + " грн\n"
                "Сума: " + str(total_price) + " грн\n\n"
                "Залишок срібла: " + str(balance) + " г"
            )
        except ValueError:
            await update.message.reply_text("Введіть число, наприклад: 23.5")
        return

    if state.get("editing"):
        ed = state["editing"]
        oid, field = ed["oid"], ed["field"]
        orders = load_orders()
        for o in orders:
            if o["id"] == oid:
                o[field] = text
                break
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        state["editing"] = None
        await update.message.reply_text(oid + " оновлено!")
        return

    if state["step"] is not None and (state.get("waiting_text") or steps[state["step"]]["options"] is None):
        state["data"][steps[state["step"]]["key"]] = text
        state["waiting_text"] = False
        state["step"] += 1
        if state["step"] < len(steps):
            await send_step(chat_id, context, steps, state["step"], state["data"])
        else:
            await finish_order(chat_id, username, context)
        return

    if is_admin_chat and any(w in text.lower() for w in ["що вивчили", "що знаєш", "база знань"]):
        summary = format_learned_summary()
        for i in range(0, len(summary), 4000):
            await update.message.reply_text(summary[i:i+4000])
        return
        return

    if needs_escalation(text) and not is_admin_chat:
        await update.message.reply_text("Передаю майстру Владиславу\nТел: 0936931493")
        await context.bot.send_message(chat_id=OWNER_ID, text="УВАГА\n@" + str(username) + ": " + text)
        return

    if not is_admin_chat:
        kb_match = find_response(text, client)
        if kb_match:
            media = kb_match.get("media", [])
            photos = [m for m in media if m.get("type") == "photo" and os.path.exists(m["path"])]
            for m in photos:
                await context.bot.send_photo(chat_id=chat_id, photo=open(m["path"], "rb"))
            await update.message.reply_text(kb_match["response_text"])
            log_conv(chat_id, "bot", "out", "[KB: " + kb_match["id"] + "]")
            await monitor_log(context, username, text, kb_match["response_text"], source="KB " + kb_match["id"])
            return

    if state["step"] is None:
        order_patterns = [
            r"хоч[уи]\s+(замовити|зробити|оформити)",
            r"замов(ити|ляю)",
            r"(зроби|оформи)\s+замовлення",
        ]
        if any(re.search(p, text.lower()) for p in order_patterns):
            state["step"] = 0
            state["data"] = {}
            state["waiting_text"] = False
            state["is_admin_order"] = False
            await send_step(chat_id, context, STEPS_CLIENT, 0, state["data"])
            return

    logging.info("Calling ask_ai for: " + repr(text[:50]))
    reply = ask_ai(chat_id, text, is_admin=is_admin_chat)
    logging.info("ask_ai returned: " + repr(str(reply)[:50]))
    if reply is None:
        reply = "Вибачте, технічна перерва. Телефонуйте: 0936931493"
        await context.bot.send_message(chat_id=OWNER_ID, text="OpenAI недоступний!\n@" + str(username) + ": " + text)
        await update.message.reply_text(reply)
        return

    monitor_reason = "AI"
    photo_request = None
    start_order = False
    clean_reply = reply

    if "REASON|||" in clean_reply:
        parts2 = clean_reply.split("REASON|||", 1)
        rest = parts2[1].split("\n", 1)
        monitor_reason = "AI | " + rest[0].strip()
        clean_reply = rest[1].strip() if len(rest) > 1 else ""

    if is_admin_chat and "LEARN|||" in clean_reply:
        parts = clean_reply.split("LEARN|||")
        clean_reply = parts[0].strip()
        fact = parts[1].split("\n")[0].strip()
        if fact:
            entry_id = add_entry(trigger=fact[:60], response_text=fact, source="admin_learn")
            state["last_kb_entry"] = entry_id
            if not clean_reply:
                clean_reply = "Зрозумів, запам'ятав!"

    lines = clean_reply.split("\n")
    clean_lines = []
    for line in lines:
        s = line.strip()
        if s == "START_ORDER":
            start_order = True
        elif s.startswith("PHOTO_REQUEST|||"):
            photo_request = s.split("|||", 1)[1].strip().split()[0] if s.split("|||", 1)[1].strip() else ""
        else:
            clean_lines.append(line)
    clean_reply = "\n".join(clean_lines).strip()

    if not start_order and clean_reply and not photo_request:
        await update.message.reply_text(clean_reply)
    log_conv(chat_id, "bot", "out", clean_reply)
    await monitor_log(context, username, text, clean_reply, source=monitor_reason, is_admin=is_admin_chat)

    if start_order and state["step"] is None:
        state["step"] = 0
        state["data"] = {}
        state["waiting_text"] = False
        state["is_admin_order"] = False
        await send_step(chat_id, context, STEPS_CLIENT, 0, state["data"])

    logging.info("PHOTO_REQUEST val: " + repr(photo_request) + " is_admin: " + str(is_admin_chat))
    if photo_request:
        if photo_request == "all":
            sent = 0
            for ptype in ["браслет", "ланцюжок", "кулон", "хрестик", "печатка"]:
                p = find_photo(ptype, chat_id)
                if p and os.path.exists(p):
                    await context.bot.send_photo(chat_id=chat_id, photo=open(p, "rb"), caption=get_photo_caption(p))
                    sent += 1
            if not sent:
                await context.bot.send_message(chat_id=chat_id, text="Фото поки немає. Скоро додамо!")
        else:
            p = find_photo(photo_request, chat_id)
            logging.info("PHOTO FOUND: " + repr(p))
            if p and os.path.exists(p):
                await context.bot.send_photo(chat_id=chat_id, photo=open(p, "rb"), caption=get_photo_caption(p))
            else:
                await context.bot.send_message(chat_id=chat_id, text="Фото " + photo_request + " поки немає.")

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("kb", kb_cmd))
    app.add_handler(CommandHandler("facts", facts_cmd))
    app.add_handler(CommandHandler("delfact", delfact_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("silver", silver_cmd))
    app.add_handler(CommandHandler("addsilver", addsilver_cmd))
    app.add_handler(CommandHandler("web", web_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("order", order_cmd))
    app.add_handler(CommandHandler("neworder", neworder_cmd))
    app.add_handler(CommandHandler("catalog", catalog))
    app.add_handler(CommandHandler("contacts", contacts))
    app.add_handler(CommandHandler("setstatus", setstatus_cmd))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("InSilver агент запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
