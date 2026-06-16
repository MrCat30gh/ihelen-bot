import logging
import requests
import pandas as pd
from io import BytesIO
from datetime import datetime

from telegram import Update, Document
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    ConversationHandler,
    filters
)

# ================== НАСТРОЙКИ ==================

TELEGRAM_TOKEN = "8399483266:AAHhPsLhrvD5P3z9pjN6FA07pnWA0niDe2M"
YANDEX_API_KEY = "AQVNx7Jv_4NFqwZ7Vug4JdEkDbfEpyC0qAXTdA18"
YANDEX_FOLDER_ID = "b1g8qkpr63i65lhjri5g"

PRIVATE_LOG_GROUP_ID = -1003304529148

# ================== ЛОГИ ==================

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== СОСТОЯНИЯ ==================

INTRO, WORKING = range(2)

# ================== КОНСТАНТЫ ==================

LIFE_SPHERES = [
    "Карьера", "Финансы", "Здоровье", "Семья",
    "Друзья/Окружение", "Личностный рост", "Хобби/Отдых", "Духовность/Вклад"
]

SYSTEM_PROMPT = """
Ты — iHelen, нейро-коуч и помощник бизнес-психолога Елены Аникиной.

Правила:
— веди диалог от первого лица
— один вопрос за раз
— помни всё, что сказал клиент
— не повторяй приветствие
— если клиент загрузил файл — учитывай его

Начни диалог со знакомства.
"""

# ================== AI ==================

class YandexGPT:
    def __init__(self):
        self.url = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    async def ask(self, history):
        headers = {
            "Authorization": f"Bearer {YANDEX_API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
            "completionOptions": {
                "temperature": 0.6,
                "maxTokens": 700
            },
            "messages": history[-20:]
        }

        response = requests.post(self.url, headers=headers, json=payload)
        response.raise_for_status()

        return response.json()["result"]["alternatives"][0]["message"]["text"]

# ================== БОТ ==================

class IHelenBot:

    def __init__(self):
        self.ai = YandexGPT()
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self.setup()

    # ---------- ЛОГИ ----------

    async def log_event(self, context, user, text):
        username = f"@{user.username}" if user.username else "без username"
        log_text = (
            f"👤 Пользователь: {user.first_name}\n"
            f"🔗 {username}\n"
            f"🆔 ID: {user.id}\n"
            f"🕒 {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"{text}"
        )
        await context.bot.send_message(PRIVATE_LOG_GROUP_ID, log_text)

    # ---------- START ----------

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()

        context.user_data["history"] = [
            {"role": "system", "text": SYSTEM_PROMPT},
            {
                "role": "assistant",
                "text": (
                    "Привет! Я — iHelen, нейро-коуч.\n"
                    "Давай познакомимся 🙂\n"
                    "Как тебя зовут и чем ты занимаешься?"
                )
            }
        ]

        await update.message.reply_text(context.user_data["history"][-1]["text"])
        await self.log_event(context, update.message.from_user, "▶️ START")

        return INTRO

    # ---------- ОБРАБОТКА EXCEL ----------

    async def excel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        document: Document = update.message.document

        if not document.file_name.lower().endswith((".xlsx", ".xls")):
            await update.message.reply_text(
                "Я могу работать только с Excel-файлами (.xlsx, .xls)."
            )
            return WORKING

        try:
            telegram_file = await document.get_file()
            file_bytes = await telegram_file.download_as_bytearray()
            data = BytesIO(file_bytes)

            df = pd.read_excel(data)

            preview = df.head(5).to_dict(orient="records")

            context.user_data["history"].append({
                "role": "system",
                "text": f"Клиент загрузил Excel-файл. Первые строки: {preview}"
            })

            await update.message.reply_text(
                "Я посмотрела файл и учту его в нашей работе.\n"
                "Давай начнём со сферы *Карьера*. Какие у тебя здесь цели?",
                parse_mode="Markdown"
            )

        except Exception as e:
            logger.exception("Ошибка чтения Excel")
            await update.message.reply_text(
                "Не получилось прочитать файл 😔\n"
                "Проверь, пожалуйста, что это корректный Excel."
            )

        return WORKING

    # ---------- ОСНОВНОЙ ЦИКЛ ----------

    async def working(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data["history"].append(
            {"role": "user", "text": update.message.text}
        )

        await self.log_event(context, update.message.from_user, update.message.text)

        answer = await self.ai.ask(context.user_data["history"])
        context.user_data["history"].append(
            {"role": "assistant", "text": answer}
        )

        await update.message.reply_text(answer)
        return WORKING

    # ---------- SETUP ----------

    def setup(self):
        conv = ConversationHandler(
            entry_points=[CommandHandler("start", self.start)],
            states={
                INTRO: [
                    MessageHandler(filters.Document.ALL, self.excel),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.working),
                ],
                WORKING: [
                    MessageHandler(filters.Document.ALL, self.excel),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.working),
                ],
            },
            fallbacks=[CommandHandler("start", self.start)],
        )

        self.app.add_handler(conv)

    def run(self):
        self.app.run_polling()

# ================== MAIN ==================

if __name__ == "__main__":
    IHelenBot().run()
