import os
import json
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from openai import OpenAI
from dotenv import load_dotenv
from knowledge import SYSTEM_PROMPT, ORDER_QUESTIONS, ESCALATION_KEYWORDS
from photo_search import find_photo, wants_photo
from learned_knowledge import load_facts, save_fact, get_facts_for_prompt

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
OWNER_ID = int(os.getenv("OWNER_CHAT_ID"))
ORDERS_FILE = "orders.json"
CHAT_LOG_FILE = "logs/conversations.log"

client = OpenAI(api_key=OPENAI_KEY)
logging.basicConfig(level=logging.INFO)
user_states = {}
chat_histories = {}
admin_mode = False  # глобальний стан адмін режиму

def get_state(chat_id):
    if chat_id not in user_states:
        user_states[chat_id] = {"step": None, "data": {}}
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

        extra_knowledge = get_facts_for_prompt()
        system = SYSTEM_PROMPT + extra_knowledge

        if is_admin:
            system += """

ТИ ЗАРАЗ В РЕЖИМІ АДМІНА — спілкуєшся з Владиславом (власником InSilver).

Твоя поведінка:
1. Якщо Владислав пише факт про виріб, ціну, умову роботи — перефразуй його коротко і запитай підтвердження. Додай в кінці: LEARN|||перефразований факт
2. Якщо Владислав пише "так" або "вірно" після твого перефразування — збережи факт. Додай: CONFIRM|||
3. Якщо Владислав пише як клієнт (питає ціну, про виріб) — просто відповідай як звичайний клієнт
4. Якщо Владислав виправляє — зрозумій виправлення і перефразуй знову

Завжди додавай в кінці відповіді: [🔧 Режим адміна]"""

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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global admin_mode
    chat_id = update.effective_chat.id
    chat_histories[chat_id] = []
    log_conv(chat_id, update.effective_user.username, "in", "/start")
    if chat_id == OWNER_ID and admin_mode:
        admin_mode = False
    await update.message.reply_text(
        "Вітаємо в InSilver! 🩶\n\nМи виготовляємо вироби зі срібла 925°\n"
        "Ланцюжки, браслети, кулони, печатки, набори\n\n"
        "Команди:\n/order — оформити замовлення\n/catalog — каталог\n/contacts — контакти"
    )

async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global admin_mode
    if update.effective_chat.id != OWNER_ID:
        await update.message.reply_text("Ця команда недоступна.")
        return
    admin_mode = not admin_mode
    chat_histories[OWNER_ID] = []
    if admin_mode:
        facts = load_facts()
        await update.message.reply_text(
            "🔧 Режим адміна увімкнено\n\n"
            "Що можеш робити:\n"
            "• Писати факти про вироби — я запам'ятаю\n"
            "• Тестувати бота як клієнт\n"
            "• Виправляти мої відповіді\n\n"
            f"Збережено фактів: {len(facts)}\n\n"
            "/admin — вимкнути режим\n"
            "/facts — переглянути збережені знання"
        )
    else:
        await update.message.reply_text("✅ Режим адміна вимкнено. Повертаємось до звичайного режиму.")

async def facts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_ID:
        return
    facts = load_facts()
    if not facts:
        await update.message.reply_text("Збережених знань поки немає.")
        return
    lines = [f"[{f['date']}] {f['fact']}" for f in facts]
    await update.message.reply_text(
        f"📚 Збережені знання ({len(facts)}):\n\n" + "\n\n".join(lines)
    )

async def contacts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Telegram: @InSilver_925\nТелефон: 0936931493\n"
        "Сайт: www.insilver.pp.ua\nГрупа: t.me/insilver_ua\nOLX: insilver.olx.ua"
    )

async def catalog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Каталог InSilver (срібло 925°):\n\n"
        "Ланцюжки — 15 видів\nБраслети — 14 видів\n"
        "Кулони, хрестики, ладанки\nПечатки та персні\nНабори\n\n"
        "Фото: www.insilver.pp.ua"
    )

async def order_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_conv(update.effective_chat.id, update.effective_user.username, "in", "/order")
    state = get_state(update.effective_chat.id)
    state["step"] = 0
    state["data"] = {}
    await update.message.reply_text("Оформлюємо замовлення! 📝\n\n" + ORDER_QUESTIONS[0][1])

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_ID:
        await update.message.reply_text("Ця команда тільки для адміна.")
        return
    orders = load_orders()
    if not orders:
        await update.message.reply_text("Замовлень поки немає.")
        return
    STATUS_EMOJI = {"new": "🆕", "in_progress": "⚙️", "ready": "✅", "sent": "📦"}
    lines = ["📋 Поточні замовлення:\n"]
    for o in reversed(orders[-10:]):
        emoji = STATUS_EMOJI.get(o.get("status", "new"), "🆕")
        lines.append(
            f"{emoji} {o['id']} — {o.get('type','?')} {o.get('style','')}\n"
            f"   Клієнт: {o.get('contact','—')}\n"
            f"   Дедлайн: {o.get('note','—')}\n"
            f"   Статус: {o.get('status','new')}\n"
        )
    await update.message.reply_text("\n".join(lines))

async def setstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != OWNER_ID:
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Використання:\n/setstatus IS-001 in_progress\n\n"
            "Статуси: new, in_progress, ready, sent"
        )
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
    await update.message.reply_text(
        f"{STATUS_EMOJI.get(new_status)} {oid} — статус змінено на {new_status}"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global admin_mode
    chat_id = update.effective_chat.id
    text = update.message.text
    user = update.effective_user
    username = user.username or user.first_name or str(chat_id)

    log_conv(chat_id, username, "in", text)

    is_admin_chat = (chat_id == OWNER_ID and admin_mode)

    state = get_state(chat_id)

    # анкета замовлення — тільки не в адмін режимі
    if state["step"] is not None and not is_admin_chat:
        state["data"][ORDER_QUESTIONS[state["step"]][0]] = text
        if state["step"] < len(ORDER_QUESTIONS) - 1:
            state["step"] += 1
            reply = ORDER_QUESTIONS[state["step"]][1]
            await update.message.reply_text(reply)
            log_conv(chat_id, "bot", "out", reply)
        else:
            oid = save_order(state["data"])
            d = state["data"]
            state["step"] = None
            state["data"] = {}
            summary = (
                f"✅ Замовлення прийнято! {oid}\n\n"
                f"Тип виробу: {d.get('type','—')}\n"
                f"Плетіння: {d.get('style','—')}\n"
                f"Довжина: {d.get('size','—')} см\n"
                f"Маса: {d.get('weight','—')} г\n"
                f"Покриття: {d.get('coating','—')}\n"
                f"Застібка: {d.get('clasp','—')}\n"
                f"Додатково: {d.get('note','—')}\n"
                f"Контакт: {d.get('contact','—')}\n\nВладислав зв'яжеться з вами 🩶"
            )
            await update.message.reply_text(summary)
            log_conv(chat_id, "bot", "out", summary)
            await context.bot.send_message(
                chat_id=OWNER_ID,
                text=f"🆕 НОВЕ ЗАМОВЛЕННЯ {oid}\n\n@{username}\n"
                     f"Тип: {d.get('type')} — {d.get('style')}\n"
                     f"Довжина: {d.get('size')} см | Маса: {d.get('weight')} г\n"
                     f"Покриття: {d.get('coating')} | Застібка: {d.get('clasp')}\n"
                     f"Додатково: {d.get('note')}\n"
                     f"Контакт: {d.get('contact')}\n\n"
                     f"Змінити статус: /setstatus {oid} in_progress"
            )
        return

    # ескалація — не в адмін режимі
    if needs_escalation(text) and not is_admin_chat:
        reply = "Передаю майстру Владиславу — він відповість найближчим часом 🙏\nТел: 0936931493"
        await update.message.reply_text(reply)
        log_conv(chat_id, "bot", "out", reply)
        await context.bot.send_message(
            chat_id=OWNER_ID, text=f"⚠️ КЛІЄНТ ПОТРЕБУЄ УВАГИ\n@{username}: {text}"
        )
        return

    # фото
    if wants_photo(text, chat_id):
        photo_path = find_photo(text, chat_id)
        if photo_path and os.path.exists(photo_path):
            await update.message.reply_photo(
                photo=open(photo_path, "rb"),
                caption="Ось приклад з нашої майстерні 🩶"
            )
            log_conv(chat_id, "bot", "out", f"[фото: {photo_path}]")
            if not is_admin_chat:
                return

    # AI відповідь
    reply = ask_ai(chat_id, text, is_admin=is_admin_chat)
    if reply is None:
        reply = "Вибачте, зараз технічна перерва. Спробуйте за кілька хвилин або зателефонуйте: 0936931493 🙏"
        await context.bot.send_message(
            chat_id=OWNER_ID, text=f"🔴 OpenAI недоступний!\n@{username}: {text}"
        )
        await update.message.reply_text(reply)
        return

    # обробка LEARN і CONFIRM в адмін режимі
    clean_reply = reply
    if is_admin_chat and "LEARN|||" in reply:
        parts = reply.split("LEARN|||")
        clean_reply = parts[0].strip()
        # зберігаємо очікуваний факт тимчасово в стані
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
    app.add_handler(CommandHandler("order", order_cmd))
    app.add_handler(CommandHandler("catalog", catalog))
    app.add_handler(CommandHandler("contacts", contacts))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("setstatus", setstatus_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("InSilver агент запущено!")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
