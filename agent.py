
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
from photo_search import find_photo, wants_photo
from learned_knowledge import load_facts, save_fact, get_facts_for_prompt

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_CHAT_ID"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", str(OWNER_ID)).split(",")]
ORDERS_FILE = "orders.json"
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

# ===== АНКЕТА =====
STEPS_CLIENT = [
    {"key": "type",    "text": "Що замовляємо?",
     "options": ["Ланцюжок", "Браслет", "Хрестик", "Кулон", "Печатка", "Набір", "Інше ✏️"]},
    {"key": "style",   "text": "Плетіння?",
     "options": ["Бісмарк", "Козацьке", "Рамзес", "Лисячий хвіст", "Візантія", "Водоспад", "Якірне", "Фараон", "Інше ✏️"]},
    {"key": "size",    "text": "Довжина (см)?",
     "options": ["40см", "45см", "50см", "55см", "60см", "17см", "18см", "20см", "Інше ✏️"]},
    {"key": "weight",  "text": "Маса виробу?",
     "options": ["Тонкий ~3-7г", "Середній ~8-15г", "Масивний ~20г+", "Не знаю ✏️"]},
    {"key": "coating", "text": "Покриття?",
     "options": ["Срібло біле", "Чорніння", "Родіювання +95грн/г", "Інше ✏️"]},
    {"key": "clasp",   "text": "Застібка?",
     "options": ["Карабін", "Коробочка 600грн", "Коробочка XL 1500грн", "Інше ✏️"]},
    {"key": "note",    "text": "Додатково?\n(дедлайн, гравіювання, побажання)",
     "options": ["Немає", "Гравіювання тексту 500грн", "Гравіювання малюнку 700грн", "Інше ✏️"]},
    {"key": "contact", "text": "Ваше імʼя та телефон або Telegram?", "options": None},
]

STEPS_ADMIN = [
    {"key": "source",  "text": "Звідки клієнт?",
     "options": ["Telegram", "Viber", "Телефон", "OLX", "Сайт", "Інше ✏️"]},
] + STEPS_CLIENT


def make_keyboard(options):
    buttons, row = [], []
    for opt in options:
        row.append(InlineKeyboardButton(opt, callback_data=f"ans:{opt}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)


def get_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {"step": None, "data": {}, "waiting_text": False, "is_admin_order": False, "pending_fact": None, "pending_original": None}
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


def ask_ai(chat_id, text, is_admin=False):
    try:
        history = get_history(chat_id)
        history.append({"role": "user", "content": text})
        if len(history) > 10:
            history = history[-10:]
        chat_histories[chat_id] = history
        system = SYSTEM_PROMPT + get_facts_for_prompt()
        if is_admin:
            system += (
                "\n\nТИ ЗАРАЗ В РЕЖИМІ АДМІНА — спілкуєшся з Владиславом (власником InSilver).\n"
                "1. Якщо Владислав пише факт про вироби, ціни, умови — перефразуй коротко і запитай 'Вірно зрозумів?'. Додай в кінці: LEARN|||перефразований факт одним реченням\n"
                "2. Якщо питає як клієнт — відповідай нормально\n"
                "3. НІКОЛИ не додавай CONFIRM самостійно — тільки LEARN\n"
                "Завжди додавай в кінці відповіді рядок: [🔧 Режим адміна]"
            )
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


async def send_step(chat_id, context, steps, step_idx):
    step = steps[step_idx]
    text = f"[{step_idx+1}/{len(steps)}] {step['text']}"
    if step["options"]:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_keyboard(step["options"]))
    else:
        await context.bot.send_message(chat_id=chat_id, text=text)


async def finish_order(chat_id, username, context):
    state = get_state(chat_id)
    d = state["data"]
    d["client_chat_id"] = chat_id
    oid = save_order(d)
    state["step"] = None
    state["data"] = {}
    state["waiting_text"] = False

    source = f"Звідки: {d.get('source')}\n" if d.get("source") else ""
    client_msg = (
        f"✅ Замовлення прийнято! {oid}\n\n"
        f"Тип: {d.get('type','—')} — {d.get('style','—')}\n"
        f"Довжина: {d.get('size','—')} | Маса: {d.get('weight','—')}\n"
        f"Покриття: {d.get('coating','—')} | Застібка: {d.get('clasp','—')}\n"
        f"Додатково: {d.get('note','—')}\n"
        f"Контакт: {d.get('contact','—')}\n\n"
        f"Владислав зв'яжеться з вами 🩶"
    )
    await context.bot.send_message(chat_id=chat_id, text=client_msg)

    mention = f"\n@{username}" if username else ""
    owner_msg = (
        f"🆕 НОВЕ ЗАМОВЛЕННЯ {oid}{mention}\n\n"
        f"{source}"
        f"Тип: {d.get('type','—')} — {d.get('style','—')}\n"
        f"Довжина: {d.get('size','—')} | Маса: {d.get('weight','—')}\n"
        f"Покриття: {d.get('coating','—')} | Застібка: {d.get('clasp','—')}\n"
        f"Додатково: {d.get('note','—')}\n"
        f"Контакт: {d.get('contact','—')}\n\n"
        f"/setstatus {oid} in_progress"
    )
    await context.bot.send_message(chat_id=OWNER_ID, text=owner_msg, reply_markup=make_status_keyboard(oid))


# ===== КОМАНДИ =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    admin_modes[chat_id] = False
    save_admin_modes(admin_modes)
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
    save_admin_modes(admin_modes)
    chat_histories[chat_id] = []
    if admin_modes[chat_id]:
        facts = load_facts()
        await update.message.reply_text(
            f"🔧 Режим адміна увімкнено\n\n"
            f"• Пиши факти про вироби — запам'ятаю\n"
            f"• Тестуй бота як клієнт\n"
            f"• /neworder — нове замовлення вручну\n"
            f"• /orders — всі замовлення\n"
            f"• /facts — збережені знання\n"
            f"• /delfact N — видалити факт\n\n"
            f"Збережено фактів: {len(facts)}\n"
            f"/admin — вимкнути режим\n\n[🔧 Режим адміна]"
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
    lines = [f"#{i+1} [{f['date']}]\n{f['fact']}" for i, f in enumerate(facts)]
    await update.message.reply_text(f"📚 Збережені знання ({len(facts)}):\n\n" + "\n\n".join(lines))



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
        await update.message.reply_text(f"Не знайдено. Всього фактів: {len(facts)}")
        return
    removed = facts.pop(idx)
    with open("learned_facts.json", "w", encoding="utf-8") as f:
        json.dump(facts, f, ensure_ascii=False, indent=2)
    await update.message.reply_text(f"🗑 Видалено #{idx+1}:\n{removed['fact']}")


def make_filter_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆕 Нові", callback_data="filter:new"),
            InlineKeyboardButton("⚙️ В роботі", callback_data="filter:in_progress"),
        ],
        [
            InlineKeyboardButton("✅ Готові", callback_data="filter:ready"),
            InlineKeyboardButton("📦 Відправлені", callback_data="filter:sent"),
        ],
        [
            InlineKeyboardButton("⏸ Призупинені", callback_data="filter:paused"),
            InlineKeyboardButton("📋 Всі", callback_data="filter:all"),
        ],
        [
            InlineKeyboardButton("🗃 Архів", callback_data="filter:archived"),
        ],
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
        await context.bot.send_message(chat_id=chat_id, text="Замовлень з таким статусом немає.")
        return
    for o in reversed(filtered[-10:]):
        e = STATUS_EMOJI.get(o.get("status", "new"), "🆕")
        text = (
            f"{e} {o['id']} — {o.get('type','?')} {o.get('style','')}\n"
            f"Клієнт: {o.get('contact','—')}\n"
            f"Довжина: {o.get('size','—')} | Маса: {o.get('weight','—')}\n"
            f"Покриття: {o.get('coating','—')} | Застібка: {o.get('clasp','—')}\n"
            f"Додатково: {o.get('note','—')}\n"
            f"Створено: {o.get('created','—')}"
        )
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=make_status_keyboard(o['id']))

async def search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if not args:
        await update.message.reply_text("Використання: /search Сашок  або  /search IS-011")
        return
    query_str = " ".join(args).lower()
    orders = load_orders()
    results = []
    for o in orders:
        if (query_str in o.get("id", "").lower() or
            query_str in o.get("contact", "").lower() or
            query_str in o.get("type", "").lower() or
            query_str in o.get("style", "").lower()):
            results.append(o)

    if not results:
        await update.message.reply_text(f"Нічого не знайдено по запиту: {query_str}")
        return

    STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦", "paused": "⏸", "archived": "🗃"}
    await update.message.reply_text(f"Знайдено: {len(results)}")
    for o in results:
        e = STATUS_EMOJI.get(o.get("status", "new"), "🆕")
        text = (
            f"{e} {o['id']} — {o.get('type','?')} {o.get('style','')}\n"
            f"Клієнт: {o.get('contact','—')}\n"
            f"Довжина: {o.get('size','—')} | Маса: {o.get('weight','—')}\n"
            f"Покриття: {o.get('coating','—')} | Застібка: {o.get('clasp','—')}\n"
            f"Додатково: {o.get('note','—')}\n"
            f"Створено: {o.get('created','—')}"
        )
        await update.message.reply_text(text, reply_markup=make_status_keyboard(o['id']))

async def orders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    await update.message.reply_text("Показати замовлення:", reply_markup=make_filter_keyboard())


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
    await send_step(chat_id, context, STEPS_ADMIN, 0)


async def order_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    state["step"] = 0
    state["data"] = {}
    state["waiting_text"] = False
    state["is_admin_order"] = False
    await send_step(chat_id, context, STEPS_CLIENT, 0)


async def setstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Використання:\n/setstatus IS-001 in_progress\n\nСтатуси: new, in_progress, ready, sent")
        return
    oid, new_status = args[0].upper(), args[1].lower()
    valid = ["new", "in_progress", "ready", "sent", "paused", "archived"]
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

    if data.startswith("edit:"):
        oid = data.split(":")[1]
        await query.message.reply_text(f"Що редагуємо в {oid}?", reply_markup=make_edit_keyboard(oid))
        await query.answer()
        return

    if data.startswith("editfield:"):
        parts = data.split(":")
        oid, field = parts[1], parts[2]
        if field == "cancel":
            await query.message.delete()
            await query.answer()
            return
        field_names = {
            "contact": "контакт клієнта",
            "size": "довжину (см)",
            "weight": "масу (г)",
            "coating": "покриття",
            "clasp": "застібку",
            "note": "додаткову інформацію",
        }
        state = get_state(chat_id)
        state["editing"] = {"oid": oid, "field": field}
        await query.message.reply_text(f"Введіть новий {field_names.get(field, field)} для {oid}:")
        await query.answer()
        return

    if data.startswith("delete:"):
        oid = data.split(":")[1]
        await query.message.reply_text(
            f"Видалити замовлення {oid}?",
            reply_markup=make_confirm_delete_keyboard(oid)
        )
        await query.answer()
        return

    if data.startswith("confirmdelete:"):
        oid = data.split(":")[1]
        orders = load_orders()
        orders = [o for o in orders if o["id"] != oid]
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        await query.message.edit_text(f"🗑 Замовлення {oid} видалено.")
        await query.answer()
        return

    if data.startswith("filter:"):
        filter_status = data.split(":")[1]
        await query.answer()
        await show_orders(chat_id, context, filter_status)
        return

    if data.startswith("status:"):
        parts = data.split(":")
        oid, new_status = parts[1], parts[2]
        STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦", "paused": "⏸", "archived": "🗃"}
        STATUS_NAMES = {"new": "Нове", "in_progress": "В роботі", "ready": "Готово", "sent": "Відправлено", "paused": "Призупинено", "archived": "Архів"}
        orders = load_orders()
        for o in orders:
            if o["id"] == oid:
                o["status"] = new_status
                break
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(orders, f, ensure_ascii=False, indent=2)
        emoji = STATUS_EMOJI.get(new_status)
        name = STATUS_NAMES.get(new_status)
        try:
            import re as _re
            base_text = _re.sub(r"\n\n[^\n]+ Статус змінено:.*$", "", query.message.text, flags=_re.DOTALL).strip()
            await query.edit_message_text(
                text=base_text + f"\n\n{emoji} Статус змінено: {name}",
                reply_markup=make_status_keyboard(oid)
            )
        except Exception:
            pass
        await query.answer(f"{emoji} {oid} — {name}")

        # якщо статус ready — питаємо вагу
        if new_status == "ready":
            state = get_state(chat_id)
            state["waiting_weight"] = oid
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Введіть точну вагу виробу {oid} в грамах (наприклад: 23.5):"
            )

        # сповіщення клієнту
        status_msgs = {
            "in_progress": f"⚙️ Ваше замовлення {oid} прийнято в роботу! Майстер розпочав виготовлення 🩶",
            "ready": f"✅ Ваше замовлення {oid} готове! Владислав зв'яжеться з вами для відправки.",
            "sent": f"📦 Ваше замовлення {oid} відправлено! Очікуйте на Новій Пошті.",
            "paused": f"⏸ По вашому замовленню {oid} є питання. Владислав зв'яжеться з вами найближчим часом.",
        }
        if new_status in status_msgs:
            orders = load_orders()
            for o in orders:
                if o["id"] == oid:
                    client_chat_id = o.get("client_chat_id")
                    if client_chat_id:
                        try:
                            await context.bot.send_message(
                                chat_id=client_chat_id,
                                text=status_msgs[new_status]
                            )
                        except Exception as e:
                            logging.error(f"Не вдалось сповістити клієнта: {e}")
                    break
        return

    if not data.startswith("ans:"):
        return

    value = data[4:]
    state = get_state(chat_id)

    if state["step"] is None:
        return

    steps = STEPS_ADMIN if state.get("is_admin_order") else STEPS_CLIENT

    if "✏️" in value:
        state["waiting_text"] = True
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
        key = steps[state["step"]]["key"]
        await query.message.reply_text(hints.get(key, "Введіть значення:"))
        return

    key = steps[state["step"]]["key"]
    state["data"][key] = value
    await query.message.edit_reply_markup(reply_markup=None)

    state["step"] += 1
    if state["step"] < len(steps):
        await send_step(chat_id, context, steps, state["step"])
    else:
        username = query.from_user.username or query.from_user.first_name
        await finish_order(chat_id, username, context)


async def save_admin_photo(photo_file, caption, chat_id, context):
    os.makedirs("channel_photos", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"channel_photos/admin_photo_{ts}.jpg"
    file = await context.bot.get_file(photo_file.file_id)
    await file.download_to_drive(filename)

    # витягуємо ключові слова з підпису
    caption = caption or ""
    keywords = [w.lower() for w in caption.split() if len(w) > 2]

    # визначаємо тип виробу
    type_map = {
        "браслет": ["браслет"], "ланцюжок": ["ланцюжок", "ланцюг", "цепочка"],
        "кулон": ["кулон"], "обручка": ["обручка", "перстень", "печатка"],
        "хрестик": ["хрестик"], "сережки": ["сережки"],
    }
    item_type = ""
    for t, words in type_map.items():
        if any(w in caption.lower() for w in words):
            item_type = t
            break

    # додаємо в індекс
    if os.path.exists("photo_index.json"):
        with open("photo_index.json", "r", encoding="utf-8") as f:
            index = json.load(f)
    else:
        index = []

    index.append({
        "photo": filename,
        "date": datetime.now().strftime("%d.%m.%Y"),
        "original_text": caption,
        "type": item_type,
        "name": caption[:50],
        "keywords": keywords,
        "description": caption,
    })

    with open("photo_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return filename


# ===== ТЕКСТ =====

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text
    user = update.effective_user
    username = user.username or user.first_name or str(chat_id)
    log_conv(chat_id, username, "in", text)

    is_admin_chat = chat_id in ADMIN_IDS and admin_modes.get(chat_id, False)
    state = get_state(chat_id)
    steps = STEPS_ADMIN if state.get("is_admin_order") else STEPS_CLIENT

    # введення ваги виробу — тільки якщо не в анкеті
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
            state["waiting_weight"] = None
            await update.message.reply_text(
                f"✅ {oid} — вага {weight_actual}г\n"
                f"Ціна за грам: {price_per_gram} грн\n"
                f"Сума: {total_price} грн"
            )
        except ValueError:
            await update.message.reply_text("Введіть число, наприклад: 23.5")
        return

    # редагування замовлення
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
        await update.message.reply_text(f"✅ {oid} оновлено!")
        return

    # вільний текст в анкеті
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

    # підтвердження факту від адміна
    confirm_words = ["так", "вірно", "правильно", "ок", "ok", "👍", "точно", "саме так"]
    if is_admin_chat and any(w in text.lower() for w in confirm_words):
        pending = state.get("pending_fact")
        if pending:
            save_fact(pending, state.get("pending_original", ""))
            state["pending_fact"] = None
            await update.message.reply_text("✅ Збережено в базу знань! [🔧 Режим адміна]")
            return

    # AI
    reply = ask_ai(chat_id, text, is_admin=is_admin_chat)
    if reply is None:
        reply = "Вибачте, технічна перерва. Телефонуйте: 0936931493 🙏"
        await context.bot.send_message(chat_id=OWNER_ID, text=f"🔴 OpenAI недоступний!\n@{username}: {text}")
        await update.message.reply_text(reply)
        return

    # обробка LEARN
    clean_reply = reply
    if is_admin_chat and "LEARN|||" in reply:
        parts = reply.split("LEARN|||")
        clean_reply = parts[0].strip()
        pending_fact = parts[1].split("\n")[0].strip()
        state["pending_fact"] = pending_fact
        state["pending_original"] = text

    await update.message.reply_text(clean_reply)
    log_conv(chat_id, "bot", "out", clean_reply)



async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    is_admin_chat = chat_id in ADMIN_IDS and admin_modes.get(chat_id, False)

    if not is_admin_chat:
        await update.message.reply_text("Дякуємо за фото! Якщо хочете замовити — напишіть /order 🩶")
        return

    photo = update.message.photo[-1]
    caption = update.message.caption or ""

    if not caption:
        await update.message.reply_text(
            "Додайте підпис до фото з описом виробу\n"
            "Наприклад: браслет Бісмарк чорніння 25г\n\n[🔧 Режим адміна]"
        )
        return

    filename = await save_admin_photo(photo, caption, chat_id, context)
    await update.message.reply_text(
        f"✅ Фото збережено!\n"
        f"Опис: {caption}\n"
        f"Файл: {filename}\n\n"
        f"Клієнти знайдуть його по запиту 🩶\n\n[🔧 Режим адміна]"
    )


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    is_admin_chat = chat_id in ADMIN_IDS and admin_modes.get(chat_id, False)

    if not is_admin_chat:
        await update.message.reply_text("Дякуємо за фото! Якщо хочете замовити - напишіть /order")
        return

    photo = update.message.photo[-1]
    caption = update.message.caption or ""

    if not caption:
        await update.message.reply_text("Додайте підпис до фото з описом виробу. Наприклад: браслет Бісмарк чорніння 25г [R] Режим адміна]")
        return

    os.makedirs("channel_photos", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"channel_photos/admin_photo_{ts}.jpg"
    tg_file = await context.bot.get_file(photo.file_id)
    await tg_file.download_to_drive(filename)

    keywords = [w.lower() for w in caption.split() if len(w) > 2]
    type_map = {
        "браслет": ["браслет"],
        "ланцюжок": ["ланцюжок", "ланцюг", "цепочка"],
        "кулон": ["кулон"],
        "обручка": ["обручка", "перстень", "печатка"],
        "хрестик": ["хрестик"],
        "сережки": ["сережки"],
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
        "photo": filename,
        "date": datetime.now().strftime("%d.%m.%Y"),
        "original_text": caption,
        "type": item_type,
        "name": caption[:50],
        "keywords": keywords,
        "description": caption,
    })

    with open("photo_index.json", "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    await update.message.reply_text(
        "Фото збережено! Опис: " + caption + " [R] Режим адміна]"
    )


def make_status_keyboard(oid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚙️ В роботі", callback_data=f"status:{oid}:in_progress"),
            InlineKeyboardButton("✅ Готово", callback_data=f"status:{oid}:ready"),
        ],
        [
            InlineKeyboardButton("📦 Відправлено", callback_data=f"status:{oid}:sent"),
            InlineKeyboardButton("⏸ Призупинено", callback_data=f"status:{oid}:paused"),
        ],
        [
            InlineKeyboardButton("🗃 Архівувати", callback_data=f"status:{oid}:archived"),
            InlineKeyboardButton("✏️ Редагувати", callback_data=f"edit:{oid}"),
        ],
        [
            InlineKeyboardButton("🗑 Видалити", callback_data=f"delete:{oid}"),
        ]
    ])

def make_edit_keyboard(oid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👤 Контакт", callback_data=f"editfield:{oid}:contact"),
            InlineKeyboardButton("📏 Розмір", callback_data=f"editfield:{oid}:size"),
        ],
        [
            InlineKeyboardButton("⚖️ Маса", callback_data=f"editfield:{oid}:weight"),
            InlineKeyboardButton("🎨 Покриття", callback_data=f"editfield:{oid}:coating"),
        ],
        [
            InlineKeyboardButton("🔗 Застібка", callback_data=f"editfield:{oid}:clasp"),
            InlineKeyboardButton("📝 Додатково", callback_data=f"editfield:{oid}:note"),
        ],
        [
            InlineKeyboardButton("◀️ Назад", callback_data=f"editfield:{oid}:cancel"),
        ]
    ])

def make_confirm_delete_keyboard(oid):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Так, видалити", callback_data=f"confirmdelete:{oid}"),
            InlineKeyboardButton("❌ Скасувати", callback_data=f"editfield:{oid}:cancel"),
        ]
    ])


async def auto_archive(context):
    orders = load_orders()
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
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=f"🗃 Автоархів: {changed} замовлень переміщено в архів"
        )

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id not in ADMIN_IDS:
        return
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

    # вага і виручка
    month_weight = sum(o.get("weight_actual", 0) for o in month)
    month_revenue = sum(o.get("total_price", 0) for o in month)

    # звідки клієнти
    sources = {}
    for o in month:
        s = o.get("source", "Не вказано")
        sources[s] = sources.get(s, 0) + 1
    sources_text = "\n".join(f"  {k}: {v}" for k, v in sorted(sources.items(), key=lambda x: -x[1]))

    # топ плетінь
    styles = {}
    for o in month:
        s = o.get("style", "Не вказано")
        if s:
            styles[s] = styles.get(s, 0) + 1
    top_styles = sorted(styles.items(), key=lambda x: -x[1])[:5]
    styles_text = "\n".join(f"  {k}: {v}" for k, v in top_styles)

    text = (
        f"📊 *Статистика InSilver*\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"💰 *Фінанси за місяць*\n"
        f"  Вага виготовлено: *{month_weight:.1f} г*\n"
        f"  Виручка: *{month_revenue:,} грн*\n\n"
        f"📦 *Замовлення*\n"
        f"  За тиждень: *{len(week)}*\n"
        f"  За місяць: *{len(month)}*\n\n"
        f"📡 *Звідки клієнти*\n{sources_text or '  немає даних'}\n\n"
        f"🔗 *Топ плетінь*\n{styles_text or '  немає даних'}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("facts", facts_cmd))
    app.add_handler(CommandHandler("delfact", delfact_cmd))
    app.add_handler(CommandHandler("orders", orders_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("search", search_cmd))
    app.add_handler(CommandHandler("order", order_cmd))
    app.add_handler(CommandHandler("neworder", neworder_cmd))
    app.add_handler(CommandHandler("catalog", catalog))
    app.add_handler(CommandHandler("contacts", contacts))
    app.add_handler(CommandHandler("setstatus", setstatus_cmd))
    # auto_archive запускається через cron окремо
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("InSilver агент запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
