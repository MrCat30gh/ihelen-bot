import logging, sqlite3, requests, pandas as pd
from io import BytesIO
from datetime import datetime
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

# ================== CONFIG ==================

TELEGRAM_TOKEN = "8399483266:AAHhPsLhrvD5P3z9pjN6FA07pnWA0niDe2M"
YANDEX_API_KEY = "AQVNx7Jv_4NFqwZ7Vug4JdEkDbfEpyC0qAXTdA18"
YANDEX_FOLDER_ID = "b1g8qkpr63i65lhjri5g"
LOG_CHAT_ID = -1003304529148

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

INTRO, MAIN = range(2)

SYSTEM_PROMPT = """
Ты — iHelen, нейро-коуч и помощник бизнес-психолога.
— один вопрос за раз
— не повторяйся
— помни всё, что сказал клиент
"""

# ================== AI ==================

class YandexGPT:
    URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    async def ask(self, messages):
        r = requests.post(self.URL, headers={
            "Authorization": f"Bearer {YANDEX_API_KEY}",
            "Content-Type": "application/json"
        }, json={
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {"temperature": 0.6, "maxTokens": 700},
            "messages": messages[-20:]
        })
        r.raise_for_status()
        return r.json()["result"]["alternatives"][0]["message"]["text"]

# ================== BOT ==================

class IHelenBot:
    def __init__(self):
        self.ai = YandexGPT()
        self.db = sqlite3.connect("ihelen.db", check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS logs (time TEXT, user INTEGER, text TEXT)"
        )
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.setup()

    # ---------- LOGGING ----------

    async def log(self, context, user, text):
        self.db.execute(
            "INSERT INTO logs VALUES (?,?,?)",
            (datetime.now().isoformat(), user.id, text)
        )
        self.db.commit()

        await context.bot.send_message(
            LOG_CHAT_ID,
            f"👤 {user.first_name} ({user.id})\n{text}"
        )

    # ---------- UI ----------

    def menu(self):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🌠 Цели", callback_data="goals"),
             InlineKeyboardButton("🎭 Страх", callback_data="fear")],
            [InlineKeyboardButton("📊 Саммари", callback_data="summary"),
             InlineKeyboardButton("🛑 Стоп", callback_data="stop")]
        ])

    # ---------- START ----------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        context.user_data["history"] = [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "assistant", "text":
             "Привет! Я — iHelen, нейро-коуч.\n"
             "Как тебя зовут?"}
        ]
        await update.message.reply_text(context.user_data["history"][-1]["text"])
        await self.log(context, update.effective_user, "START")
        return INTRO

    async def intro(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        name = update.message.text.strip()
        context.user_data["history"].append({"role": "user", "text": name})

        await self.log(context, update.effective_user, f"Имя: {name}")

        await update.message.reply_text(
            f"Рада знакомству, {name} 😊",
            reply_markup=self.menu()
        )
        return MAIN

    # ---------- MENU ACTIONS ----------

    async def menu_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        await q.answer()

        await self.log(context, q.from_user, f"Кнопка: {q.data}")

        if q.data == "stop":
            await q.edit_message_text("Я рядом 💫\n/start — вернуться")
            return ConversationHandler.END

        if q.data == "summary":
            text = "\n".join(
                m["text"] for m in context.user_data["history"]
                if m["role"] == "user"
            )[-3000:]

            context.user_data["history"].append({
                "role": "assistant",
                "text": f"Саммари клиента: {text}"
            })

            await q.edit_message_text(
                "Я собрала саммари. Хочешь продолжить?",
                reply_markup=self.menu()
            )
            return MAIN

        # обычная техника
        context.user_data["history"].append({
            "role": "assistant",
            "text": f"Выбрана техника: {q.data}"
        })

        await q.edit_message_text(
            "Расскажи, что сейчас для тебя самое важное?"
        )
        return MAIN

    # ---------- DIALOG ----------

    async def dialog(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.log(context, update.effective_user, update.message.text)

        context.user_data["history"].append(
            {"role": "user", "text": update.message.text}
        )

        answer = await self.ai.ask(context.user_data["history"])
        context.user_data["history"].append(
            {"role": "assistant", "text": answer}
        )

        await update.message.reply_text(answer)

    # ---------- FILE ----------

    async def excel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        file = await update.message.document.get_file()
        data = BytesIO(await file.download_as_bytearray())

        try:
            df = pd.read_excel(data)
        except:
            data.seek(0)
            df = pd.read_csv(data)

        context.user_data["history"].append({
            "role": "system",
            "text": f"Файл клиента: {df.head(3).to_dict()}"
        })

        await self.log(context, update.effective_user, "Загружен файл")

        await update.message.reply_text(
            "Файл учтён 👌",
            reply_markup=self.menu()
        )

    # ---------- SETUP ----------

    def setup(self):
        conv = ConversationHandler(
            entry_points=[CommandHandler("start", self.start)],
            states={
                INTRO: [MessageHandler(filters.TEXT & ~filters.COMMAND, self.intro)],
                MAIN: [
                    CallbackQueryHandler(self.menu_action),
                    MessageHandler(filters.Document.ALL, self.excel),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.dialog)
                ]
            },
            fallbacks=[CommandHandler("start", self.start)],
            allow_reentry=True
        )
        self.app.add_handler(conv)

    def run(self):
        self.app.run_polling()

# ================== RUN ==================

if __name__ == "__main__":
    IHelenBot().run()
