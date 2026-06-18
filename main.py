# main.py
import logging
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import requests
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os
from dotenv import load_dotenv

# Загружаем переменные из .env файла
load_dotenv()

# Отладка .env файла
print("YANDEX_API_KEY:", os.getenv("YANDEX_API_KEY"))
print("YANDEX_FOLDER_ID:", os.getenv("YANDEX_FOLDER_ID"))
print("ADMIN_USERNAME:", os.getenv("ADMIN_USERNAME"))
# ================== CONFIG ==================
YANDEX_API_KEY = os.getenv("YANDEX_API_KEY")
YANDEX_FOLDER_ID = os.getenv("YANDEX_FOLDER_ID")

# Админ-доступ (если в .env не задано, будут использоваться значения по умолчанию)
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
Ты — iHelen, нейро-коуч и помощник бизнес-психолога.
— один вопрос за раз
— не повторяйся
— помни всё, что сказал клиент
"""

# ================== SECURITY ==================
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()


def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password):
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Неверные учетные данные",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    if username != ADMIN_USERNAME:
        raise credentials_exception

    return username


# ================== DB ==================

db = sqlite3.connect("ihelen_web.db", check_same_thread=False)

# Таблица пользователей
db.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT UNIQUE,
        name TEXT,
        created_at TEXT
    )
""")

# Таблица логов
db.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time TEXT,
        session_id TEXT,
        user_msg TEXT,
        bot_msg TEXT
    )
""")
db.commit()


def get_or_create_user(session_id: str):
    """Получить или создать пользователя"""
    cursor = db.execute("SELECT id, name FROM users WHERE session_id = ?", (session_id,))
    user = cursor.fetchone()

    if user:
        return {"id": user[0], "name": user[1], "session_id": session_id}

    # Создаём нового пользователя
    created_at = datetime.now().isoformat()
    db.execute(
        "INSERT INTO users (session_id, name, created_at) VALUES (?, ?, ?)",
        (session_id, None, created_at)
    )
    db.commit()

    cursor = db.execute("SELECT id FROM users WHERE session_id = ?", (session_id,))
    user_id = cursor.fetchone()[0]

    return {"id": user_id, "name": None, "session_id": session_id}


def update_user_name(session_id: str, name: str):
    """Обновить имя пользователя"""
    db.execute("UPDATE users SET name = ? WHERE session_id = ?", (name, session_id))
    db.commit()


# ================== AI ==================
class YandexGPT:
    URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    def ask(self, messages: List[Dict[str, str]]) -> str:
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


ai = YandexGPT()

# ================== SESSION STORAGE ==================
sessions: Dict[str, List[Dict[str, str]]] = {}


# ================== MODELS ==================

class ChatMessage(BaseModel):
    session_id: str
    message: str
    user_name: Optional[str] = None  # Опциональное имя


class ChatResponse(BaseModel):
    reply: str
    user_id: Optional[int] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


class LogEntry(BaseModel):
    id: int
    time: str
    session_id: str
    user_id: Optional[int]
    user_name: Optional[str]
    user_msg: str
    bot_msg: str


class UserInfo(BaseModel):
    id: int
    session_id: str
    name: Optional[str]
    created_at: str
    message_count: int


# ================== APP ==================
app = FastAPI()
app.mount("/static", StaticFiles(directory="."), name="static")

@app.get("/")
async def root():
    return FileResponse("index.html")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ================== ENDPOINTS ==================

@app.post("/chat", response_model=ChatResponse)
def chat(msg: ChatMessage):
    session_id = msg.session_id
    user_text = msg.message.strip()

    # Получаем или создаём пользователя
    user = get_or_create_user(session_id)
    user_id = user["id"]

    # Обновляем имя, если оно передано
    if msg.user_name and not user["name"]:
        update_user_name(session_id, msg.user_name)
        user["name"] = msg.user_name

    # Инициализация новой сессии
    if session_id not in sessions:
        sessions[session_id] = [
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "assistant", "text": "Привет! Я — iHelen, нейро-коуч.\nКак тебя зовут?"}
        ]
        reply = sessions[session_id][-1]["text"]
        db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
                   (datetime.now().isoformat(), session_id, "[START]", reply))
        db.commit()
        return ChatResponse(reply=reply, user_id=user_id)

    # Обработка кнопок меню
    if user_text.startswith('[MENU:'):
        action = user_text.replace('[MENU:', '').replace(']', '')

        if action == 'stop':
            reply = "Я рядом 💫\nНапиши что-нибудь, чтобы вернуться"
            sessions[session_id].append({"role": "assistant", "text": reply})
            db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
                       (datetime.now().isoformat(), session_id, f"[MENU:{action}]", reply))
            db.commit()
            return ChatResponse(reply=reply, user_id=user_id)

        elif action == 'summary':
            user_messages = [m["text"] for m in sessions[session_id] if m["role"] == "user"]
            text = "\n".join(user_messages)[-3000:]

            reply = f"📊 Вот саммари нашей беседы:\n\n{text}\n\nХочешь продолжить?"
            sessions[session_id].append({"role": "assistant", "text": reply})
            db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
                       (datetime.now().isoformat(), session_id, f"[MENU:{action}]", reply))
            db.commit()
            return ChatResponse(reply=reply, user_id=user_id)

        else:
            technique_names = {
                'goals': '🌠 Цели',
                'fear': '🎭 Страх'
            }
            technique = technique_names.get(action, action)
            reply = f"Выбрана техника: {technique}\n\nРасскажи, что сейчас для тебя самое важное?"
            sessions[session_id].append({"role": "assistant", "text": reply})
            db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
                       (datetime.now().isoformat(), session_id, f"[MENU:{action}]", reply))
            db.commit()
            return ChatResponse(reply=reply, user_id=user_id)

    # Проверяем, ждём ли имя пользователя
    # len == 2 значит только system + assistant приветствие
    if len(sessions[session_id]) == 2 and not user["name"]:
        name = user_text
        sessions[session_id].append({"role": "user", "text": name})

        # Сохраняем имя в БД
        update_user_name(session_id, name)

        reply = f"Рада знакомству, {name}! 😊\n\nВыбери тему для работы:"
        sessions[session_id].append({"role": "assistant", "text": reply})

        db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
                   (datetime.now().isoformat(), session_id, f"Имя: {name}", reply))
        db.commit()
        return ChatResponse(reply=reply, user_id=user_id)

        # Сохраняем имя в БД
        update_user_name(session_id, name)

        reply = f"Рада знакомству, {name}! 😊\n\nВыбери тему для работы:"
        sessions[session_id].append({"role": "assistant", "text": reply})

        db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
                   (datetime.now().isoformat(), session_id, f"Имя: {name}", reply))
        db.commit()
        return ChatResponse(reply=reply, user_id=user_id)

    # Обычный диалог
    sessions[session_id].append({"role": "user", "text": user_text})

    try:
        bot_reply = ai.ask(sessions[session_id])
    except Exception as e:
        logger.error(f"AI error: {e}")
        raise HTTPException(status_code=500, detail="Ошибка нейросети")

    sessions[session_id].append({"role": "assistant", "text": bot_reply})

    db.execute("INSERT INTO logs (time, session_id, user_msg, bot_msg) VALUES (?,?,?,?)",
               (datetime.now().isoformat(), session_id, user_text, bot_reply))
    db.commit()

    return ChatResponse(reply=bot_reply, user_id=user_id)


@app.post("/login", response_model=Token)
async def login(form_data: LoginRequest):
    # Простое сравнение паролей (без хэширования)
    if form_data.username != ADMIN_USERNAME or form_data.password != ADMIN_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверные учетные данные",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}


@app.get("/logs", response_model=List[LogEntry])
async def get_logs(current_user: str = Depends(get_current_user)):
    cursor = db.execute("""
        SELECT l.id, l.time, l.session_id, u.id, u.name, l.user_msg, l.bot_msg 
        FROM logs l
        LEFT JOIN users u ON l.session_id = u.session_id
        ORDER BY l.id DESC 
        LIMIT 100
    """)
    logs = []
    for row in cursor:
        logs.append(LogEntry(
            id=row[0],
            time=row[1],
            session_id=row[2],
            user_id=row[3],
            user_name=row[4],
            user_msg=row[5],
            bot_msg=row[6]
        ))
    return logs


@app.get("/admin")
async def admin():
    return FileResponse("admin.html")

@app.get("/users", response_model=List[UserInfo])
async def get_users(current_user: str = Depends(get_current_user)):
    cursor = db.execute("""
        SELECT u.id, u.session_id, u.name, u.created_at, COUNT(l.id) as message_count
        FROM users u
        LEFT JOIN logs l ON u.session_id = l.session_id AND l.user_msg != '[START]'
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """)
    users = []
    for row in cursor:
        users.append(UserInfo(
            id=row[0],
            session_id=row[1],
            name=row[2],
            created_at=row[3],
            message_count=row[4]
        ))
    return users