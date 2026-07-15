import asyncio
import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, Router, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.types import (
    Message, CallbackQuery, ChatJoinRequest,
    InlineKeyboardMarkup, InlineKeyboardButton,
    WebAppInfo, KeyboardButton, ReplyKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web
import aiohttp_cors
import json
import math

# ══════════════════════════════════════════════
#   CONFIG
# ══════════════════════════════════════════════

BOT_TOKEN = "8866643954:AAGXkP-eAHB3FN2My9XhBCUHeS63-Ea7JeE"
ADMIN_ID  = 8798002423

CHANNELS = [
    {"id": -1004448235804, "link": "https://t.me/+fHh_acfILthlYTcy",  "name": "🎬 Kanal 1"},
    {"id": -1004414514606, "link": "https://t.me/+CYPY_WK9et5mZTYy",  "name": "🎬 Kanal 2"},
    {"id": -1003927187223, "link": "https://t.me/+cjNt-W3sVNo2MGM6",  "name": "🎬 Kanal 3"},
    {"id": "@Kino_uz_ru_kz", "link": "https://t.me/Kino_uz_ru_kz",    "name": "🎬 Kino Kanal"},
]

REELS_CHANNEL_ID   = -1003770392904
REELS_CHANNEL_LINK = "https://t.me/Kino_uz_ru_kz"
MOVIE_CHANNEL_ID   = -1004436110123

# ── DO'KON (WebApp) SOZLAMALARI ──
WEBAPP_URL     = "https://username.github.io/bozor-webapp/"  # GitHub Pages manzili
API_HOST       = "0.0.0.0"
API_PORT       = 8080
PREPAY_PERCENT_CASH = 30  # naqd tanlansa ham MAJBURIY oldindan to'lov (%), qolgani kuryerga naqd
PREPAY_PERCENT_CARD = 60  # karta tanlansa oldindan to'lov (%)

# ── KUNLIK AVTOMATIK BILDIRISHNOMA ──
DAILY_BROADCAST_HOUR   = 10  # soat (server vaqti bo'yicha, 24-soatlik format)
DAILY_BROADCAST_MINUTE = 0

# ══════════════════════════════════════════════
#   DATABASE
# ══════════════════════════════════════════════

DB_PATH = "data/bot.db"

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                joined_at   TEXT DEFAULT (datetime('now')),
                is_approved INTEGER DEFAULT 0,
                is_vip      INTEGER DEFAULT 0,
                vip_until   TEXT
            );
            CREATE TABLE IF NOT EXISTS movies (
                code               TEXT PRIMARY KEY,
                title              TEXT,
                file_id            TEXT NOT NULL,
                channel_message_id INTEGER,
                added_at           TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS vip_requests (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER,
                full_name  TEXT,
                months     INTEGER,
                status     TEXT DEFAULT 'pending',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            CREATE TABLE IF NOT EXISTS products (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                price         INTEGER NOT NULL,
                category      TEXT NOT NULL,
                emoji         TEXT DEFAULT '📦',
                photo_file_id TEXT,
                stock         INTEGER DEFAULT 0,
                active        INTEGER DEFAULT 1,
                created_at    TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS shop_orders (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                order_code          TEXT UNIQUE NOT NULL,
                user_id             INTEGER NOT NULL,
                username            TEXT,
                items               TEXT NOT NULL,
                subtotal            INTEGER NOT NULL,
                delivery_fee        INTEGER NOT NULL,
                total               INTEGER NOT NULL,
                region_id           TEXT,
                district_id         TEXT,
                pay_type            TEXT NOT NULL,
                prepay_amount       INTEGER DEFAULT 0,
                status              TEXT DEFAULT 'yangi',
                receipt_photo_file_id TEXT,
                created_at          TEXT DEFAULT (datetime('now'))
            );
        """)
        # Default sozlamalar
        defaults = [
            ("vip_price_1",   "15000"),
            ("vip_price_3",   "35000"),
            ("vip_price_6",   "60000"),
            ("vip_price_12",  "100000"),
            ("vip_card",      "0000 0000 0000 0000"),
            ("vip_card_name", "Karta egasi"),
            ("shop_card",      "0000 0000 0000 0000"),
            ("shop_card_name", "Karta egasi"),
            ("daily_broadcast_text",
             "🛍 Do'konimizda yangi mahsulotlar bor! Ko'rish uchun pastdagi \"Do'konni ochish\" tugmasini bosing."),
            ("vip_text",
             "✅ Kanallarga a'zo bo'lmasdan ishlash\n"
             "✅ Kinolarni yuklab olish\n"
             "✅ Do'stlarga ulashish\n"
             "✅ Barcha VIP kinolar"),
        ]
        for k, v in defaults:
            conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))

        # ── MIGRATION: eski DB ga yangi ustunlar qo'shish ──
        existing = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "is_vip" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0")
            logging.info("Migration: is_vip ustuni qoshildi")
        if "vip_until" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN vip_until TEXT")
            logging.info("Migration: vip_until ustuni qoshildi")

        # MIGRATION: eski DB ga yangi ustunlar
        existing = [row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "is_vip" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN is_vip INTEGER DEFAULT 0")
        if "vip_until" not in existing:
            conn.execute("ALTER TABLE users ADD COLUMN vip_until TEXT")

        # ESKI KOD O'CHIRISH (duplikat loop oldini olish)
        if False:
            conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))

# ── SETTINGS ──────────────────────────────────

def get_setting(key):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""

def set_setting(key, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))

# ── USERS ─────────────────────────────────────

def add_user(user_id, username, full_name):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name) VALUES (?,?,?)",
            (user_id, username, full_name)
        )

def set_approved(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_approved=1 WHERE user_id=?", (user_id,))

def is_approved(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT is_approved FROM users WHERE user_id=?", (user_id,)).fetchone()
    return bool(row and row["is_approved"])

def set_vip(user_id, months):
    with get_conn() as conn:
        row = conn.execute("SELECT vip_until FROM users WHERE user_id=?", (user_id,)).fetchone()
        now = datetime.now()
        base = now
        if row and row["vip_until"]:
            try:
                cur = datetime.fromisoformat(row["vip_until"])
                if cur > now:
                    base = cur
            except Exception:
                pass
        until = base + timedelta(days=30 * months)
        conn.execute(
            "UPDATE users SET is_vip=1, vip_until=?, is_approved=1 WHERE user_id=?",
            (until.isoformat(), user_id)
        )
        return until

def remove_vip(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_vip=0, vip_until=NULL WHERE user_id=?", (user_id,))

def is_vip(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT is_vip, vip_until FROM users WHERE user_id=?", (user_id,)).fetchone()
    if not row or not row["is_vip"]:
        return False
    if row["vip_until"]:
        try:
            if datetime.fromisoformat(row["vip_until"]) < datetime.now():
                return False
        except Exception:
            pass
    return True

def get_vip_until(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT vip_until FROM users WHERE user_id=?", (user_id,)).fetchone()
    if row and row["vip_until"]:
        try:
            return datetime.fromisoformat(row["vip_until"])
        except Exception:
            pass
    return None

def get_all_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users").fetchall()

def get_approved_users():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE is_approved=1").fetchall()

def get_user_count():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as total, SUM(is_approved) as approved, SUM(is_vip) as vip FROM users").fetchone()
        return row["total"], row["approved"] or 0, row["vip"] or 0

# ── MOVIES ────────────────────────────────────

def add_movie(code, title, file_id, channel_message_id=None):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO movies (code,title,file_id,channel_message_id) VALUES (?,?,?,?)",
            (code, title, file_id, channel_message_id)
        )

def get_movie(code):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM movies WHERE code=?", (code,)).fetchone()

def delete_movie(code):
    with get_conn() as conn:
        conn.execute("DELETE FROM movies WHERE code=?", (code,))

def get_all_movies():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM movies ORDER BY added_at DESC").fetchall()

# ── VIP REQUESTS ──────────────────────────────

def add_vip_request(user_id, full_name, months):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO vip_requests (user_id, full_name, months) VALUES (?,?,?)",
            (user_id, full_name, months)
        )
        return conn.execute("SELECT last_insert_rowid() as id").fetchone()["id"]

def get_vip_request(req_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM vip_requests WHERE id=?", (req_id,)).fetchone()

def update_vip_request(req_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE vip_requests SET status=? WHERE id=?", (status, req_id))

# ── DO'KON: MAHSULOTLAR ───────────────────────

def add_product(name, price, category, emoji, photo_file_id, stock):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO products (name, price, category, emoji, photo_file_id, stock) VALUES (?,?,?,?,?,?)",
            (name, price, category, emoji, photo_file_id, stock)
        )
        return cur.lastrowid

def get_products(active_only=True):
    with get_conn() as conn:
        q = "SELECT * FROM products"
        if active_only:
            q += " WHERE active=1"
        q += " ORDER BY id DESC"
        return [dict(r) for r in conn.execute(q).fetchall()]

def get_product(product_id):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None

def deactivate_product(product_id):
    with get_conn() as conn:
        conn.execute("UPDATE products SET active=0 WHERE id=?", (product_id,))

# ── DO'KON: BUYURTMALAR ───────────────────────

def next_order_code():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM shop_orders").fetchone()
        return f"#A{row['c'] + 1042}"

def create_order(order_code, user_id, username, items, subtotal, delivery_fee,
                  total, region_id, district_id, pay_type, prepay_amount):
    with get_conn() as conn:
        conn.execute("""INSERT INTO shop_orders
            (order_code, user_id, username, items, subtotal, delivery_fee, total,
             region_id, district_id, pay_type, prepay_amount)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (order_code, user_id, username, json.dumps(items, ensure_ascii=False),
             subtotal, delivery_fee, total, region_id, district_id, pay_type, prepay_amount))

def get_order(order_code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM shop_orders WHERE order_code=?", (order_code,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["items"] = json.loads(d["items"])
        return d

def update_order_status(order_code, status):
    with get_conn() as conn:
        conn.execute("UPDATE shop_orders SET status=? WHERE order_code=?", (status, order_code))

def attach_receipt(order_code, file_id):
    with get_conn() as conn:
        conn.execute("UPDATE shop_orders SET receipt_photo_file_id=? WHERE order_code=?", (file_id, order_code))

def find_pending_order_for_user(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM shop_orders WHERE user_id=? AND status='yangi' ORDER BY id DESC LIMIT 1",
            (user_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["items"] = json.loads(d["items"])
        return d

# ── DO'KON: VILOYAT/TUMAN VA YETKAZIB BERISH ──

REGIONS = [
    {"id": "tsh_c", "name": "Toshkent shahri", "icon": "🏙️", "districts": [
        {"id": "chilonzor", "name": "Chilonzor tumani", "km": 12},
        {"id": "yunusobod", "name": "Yunusobod tumani", "km": 20},
        {"id": "sergeli", "name": "Sergeli tumani", "km": 28},
        {"id": "mirzo", "name": "Mirzo Ulug'bek tumani", "km": 16},
        {"id": "yakkasaroy", "name": "Yakkasaroy tumani", "km": 10},
        {"id": "shayxontohur", "name": "Shayxontohur tumani", "km": 8},
        {"id": "mirobod", "name": "Mirobod tumani", "km": 9},
        {"id": "olmazor", "name": "Olmazor tumani", "km": 18},
        {"id": "uchtepa", "name": "Uchtepa tumani", "km": 15},
        {"id": "bektemir", "name": "Bektemir tumani", "km": 22},
        {"id": "yashnobod", "name": "Yashnobod tumani", "km": 14},
        {"id": "yangihayot", "name": "Yangihayot tumani", "km": 25},
    ]},
    {"id": "tsh_v", "name": "Toshkent viloyati", "icon": "🌾", "districts": [
        {"id": "qibray", "name": "Qibray tumani", "km": 45},
        {"id": "chirchiq", "name": "Chirchiq shahri", "km": 55},
        {"id": "angren", "name": "Angren shahri", "km": 110},
        {"id": "ohangaron", "name": "Ohangaron tumani", "km": 95},
        {"id": "olmaliq", "name": "Olmaliq shahri", "km": 90},
        {"id": "bekobod", "name": "Bekobod tumani", "km": 130},
        {"id": "yangiyol", "name": "Yangiyo'l tumani", "km": 30},
        {"id": "boka", "name": "Bo'ka tumani", "km": 65},
        {"id": "parkent", "name": "Parkent tumani", "km": 40},
        {"id": "piskent", "name": "Piskent tumani", "km": 55},
        {"id": "chinoz", "name": "Chinoz tumani", "km": 60},
        {"id": "bostonliq", "name": "Bo'stonliq tumani", "km": 75},
    ]},
    {"id": "sir", "name": "Sirdaryo viloyati", "icon": "🌻", "districts": [
        {"id": "guliston", "name": "Guliston shahri", "km": 120},
        {"id": "boyovut", "name": "Boyovut tumani", "km": 160},
        {"id": "xovos", "name": "Xovos tumani", "km": 100},
        {"id": "mirzaobod", "name": "Mirzaobod tumani", "km": 140},
        {"id": "sardoba", "name": "Sardoba tumani", "km": 150},
    ]},
    {"id": "jiz", "name": "Jizzax viloyati", "icon": "🏔️", "districts": [
        {"id": "jizzax_c", "name": "Jizzax shahri", "km": 215},
        {"id": "gallaorol", "name": "G'allaorol tumani", "km": 260},
        {"id": "zomin", "name": "Zomin tumani", "km": 250},
        {"id": "paxtakor", "name": "Paxtakor tumani", "km": 230},
        {"id": "dostlik", "name": "Do'stlik tumani", "km": 200},
    ]},
    {"id": "sam", "name": "Samarqand viloyati", "icon": "🕌", "districts": [
        {"id": "samarqand_c", "name": "Samarqand shahri", "km": 300},
        {"id": "kattaqorgon", "name": "Kattaqo'rg'on shahri", "km": 360},
        {"id": "urgut", "name": "Urgut tumani", "km": 320},
        {"id": "ishtixon", "name": "Ishtixon tumani", "km": 340},
        {"id": "bulungur", "name": "Bulung'ur tumani", "km": 280},
        {"id": "payariq", "name": "Payariq tumani", "km": 310},
    ]},
    {"id": "qash", "name": "Qashqadaryo viloyati", "icon": "🏜️", "districts": [
        {"id": "qarshi", "name": "Qarshi shahri", "km": 520},
        {"id": "shahrisabz", "name": "Shahrisabz shahri", "km": 440},
        {"id": "kitob", "name": "Kitob tumani", "km": 450},
        {"id": "koson", "name": "Koson tumani", "km": 540},
        {"id": "guzor", "name": "G'uzor tumani", "km": 560},
        {"id": "muborak", "name": "Muborak tumani", "km": 570},
    ]},
    {"id": "sur", "name": "Surxondaryo viloyati", "icon": "⛰️", "districts": [
        {"id": "termiz", "name": "Termiz shahri", "km": 730},
        {"id": "denov", "name": "Denov tumani", "km": 660},
        {"id": "boysun", "name": "Boysun tumani", "km": 620},
        {"id": "sherobod", "name": "Sherobod tumani", "km": 700},
        {"id": "sariosiyo", "name": "Sariosiyo tumani", "km": 640},
    ]},
    {"id": "bux", "name": "Buxoro viloyati", "icon": "🏛️", "districts": [
        {"id": "buxoro_c", "name": "Buxoro shahri", "km": 570},
        {"id": "kogon", "name": "Kogon shahri", "km": 560},
        {"id": "gijduvon", "name": "G'ijduvon tumani", "km": 530},
        {"id": "qorakol", "name": "Qorako'l tumani", "km": 610},
        {"id": "romitan", "name": "Romitan tumani", "km": 550},
    ]},
    {"id": "nav", "name": "Navoiy viloyati", "icon": "⛏️", "districts": [
        {"id": "navoiy_c", "name": "Navoiy shahri", "km": 430},
        {"id": "zarafshon", "name": "Zarafshon shahri", "km": 500},
        {"id": "nurota", "name": "Nurota tumani", "km": 390},
        {"id": "qiziltepa", "name": "Qiziltepa tumani", "km": 460},
        {"id": "uchquduq", "name": "Uchquduq tumani", "km": 480},
    ]},
    {"id": "fer", "name": "Farg'ona viloyati", "icon": "🌸", "districts": [
        {"id": "fargona_c", "name": "Farg'ona shahri", "km": 420},
        {"id": "qoqon", "name": "Qo'qon shahri", "km": 360},
        {"id": "margilon", "name": "Marg'ilon shahri", "km": 400},
        {"id": "rishton", "name": "Rishton tumani", "km": 390},
        {"id": "quva", "name": "Quva tumani", "km": 440},
    ]},
    {"id": "and", "name": "Andijon viloyati", "icon": "🍇", "districts": [
        {"id": "andijon_c", "name": "Andijon shahri", "km": 450},
        {"id": "asaka", "name": "Asaka tumani", "km": 460},
        {"id": "xojaobod", "name": "Xo'jaobod tumani", "km": 470},
        {"id": "shahrixon", "name": "Shahrixon tumani", "km": 440},
        {"id": "qorgontepa", "name": "Qo'rg'ontepa tumani", "km": 465},
    ]},
    {"id": "nam", "name": "Namangan viloyati", "icon": "🍑", "districts": [
        {"id": "namangan_c", "name": "Namangan shahri", "km": 370},
        {"id": "chust", "name": "Chust tumani", "km": 350},
        {"id": "pop", "name": "Pop tumani", "km": 340},
        {"id": "kosonsoy", "name": "Kosonsoy tumani", "km": 380},
        {"id": "toraqorgon", "name": "To'raqo'rg'on tumani", "km": 400},
    ]},
    {"id": "xor", "name": "Xorazm viloyati", "icon": "🏺", "districts": [
        {"id": "urganch", "name": "Urganch shahri", "km": 1050},
        {"id": "xiva", "name": "Xiva shahri", "km": 1060},
        {"id": "shovot", "name": "Shovot tumani", "km": 1040},
        {"id": "gurlan", "name": "Gurlan tumani", "km": 1020},
        {"id": "xonqa", "name": "Xonqa tumani", "km": 1030},
    ]},
    {"id": "qor", "name": "Qoraqalpog'iston Respublikasi", "icon": "🐫", "districts": [
        {"id": "nukus", "name": "Nukus shahri", "km": 1200},
        {"id": "tortkol", "name": "To'rtko'l tumani", "km": 1150},
        {"id": "moynoq", "name": "Mo'ynoq tumani", "km": 1300},
        {"id": "xojayli", "name": "Xo'jayli tumani", "km": 1190},
        {"id": "beruniy", "name": "Beruniy tumani", "km": 1140},
    ]},
]

def find_district(region_id, district_id):
    for region in REGIONS:
        if region["id"] == region_id:
            for d in region["districts"]:
                if d["id"] == district_id:
                    return d
    return None

def delivery_fee(km: int) -> int:
    if km <= 100:
        return 30_000
    extra_steps = math.ceil((km - 100) / 100)
    return 30_000 + extra_steps * 20_000

# ══════════════════════════════════════════════
#   ROUTERS & STATES
# ══════════════════════════════════════════════

start_router = Router()
movie_router = Router()
admin_router = Router()
shop_router = Router()

class AddMovie(StatesGroup):
    waiting_code  = State()
    waiting_title = State()
    waiting_reels = State()
    waiting_file  = State()

class AddReels(StatesGroup):
    waiting_title = State()
    waiting_file  = State()

class Broadcast(StatesGroup):
    waiting_target  = State()
    waiting_message = State()

class DeleteMovie(StatesGroup):
    waiting_code = State()

class SendUser(StatesGroup):
    waiting_id      = State()
    waiting_message = State()

class VipSettings(StatesGroup):
    waiting_key   = State()
    waiting_value = State()

class AddProduct(StatesGroup):
    name     = State()
    price    = State()
    category = State()
    emoji    = State()
    photo    = State()
    stock    = State()

def is_admin(user_id):
    return user_id == ADMIN_ID

# ══════════════════════════════════════════════
#   KEYBOARDS
# ══════════════════════════════════════════════

def channels_keyboard():
    buttons = [[InlineKeyboardButton(text=ch["name"], url=ch["link"])] for ch in CHANNELS]
    buttons.append([InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_info")])
    buttons.append([InlineKeyboardButton(text="✅ A'zo bo'ldim, tekshir!", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def shop_reply_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="🛍 Do'konni ochish", web_app=WebAppInfo(url=WEBAPP_URL))]],
        resize_keyboard=True
    )

def vip_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"1 oy  — {get_setting('vip_price_1')} so'm",  callback_data="vip_buy_1")],
        [InlineKeyboardButton(text=f"3 oy  — {get_setting('vip_price_3')} so'm",  callback_data="vip_buy_3")],
        [InlineKeyboardButton(text=f"6 oy  — {get_setting('vip_price_6')} so'm",  callback_data="vip_buy_6")],
        [InlineKeyboardButton(text=f"1 yil — {get_setting('vip_price_12')} so'm", callback_data="vip_buy_12")],
        [InlineKeyboardButton(text="◀️ Orqaga", callback_data="vip_back")],
    ])

def admin_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🎬 Kino qo'shish",    callback_data="adm_add_movie"),
            InlineKeyboardButton(text="🎞 Reels qo'shish",   callback_data="adm_add_reels"),
        ],
        [
            InlineKeyboardButton(text="🗑 Kino o'chirish",   callback_data="adm_del_movie"),
            InlineKeyboardButton(text="📋 Kinolar ro'yxati", callback_data="adm_movie_list"),
        ],
        [
            InlineKeyboardButton(text="👑 VIP berish",       callback_data="adm_vip_add"),
            InlineKeyboardButton(text="👑 VIP olish",        callback_data="adm_vip_remove"),
        ],
        [
            InlineKeyboardButton(text="⚙️ VIP sozlamalar",  callback_data="adm_vip_settings"),
            InlineKeyboardButton(text="📊 Statistika",       callback_data="adm_stats"),
        ],
        [
            InlineKeyboardButton(text="👥 Foydalanuvchilar", callback_data="adm_user_list"),
            InlineKeyboardButton(text="📢 Hammaga xabar",    callback_data="adm_broadcast"),
        ],
        [
            InlineKeyboardButton(text="✉️ Bitta userga",     callback_data="adm_send_user"),
            InlineKeyboardButton(text="🔄 Yangilash",        callback_data="adm_refresh"),
        ],
    ])

async def get_not_joined(bot: Bot, user_id: int):
    not_joined = []
    for ch in CHANNELS:
        try:
            member = await bot.get_chat_member(ch["id"], user_id)
            if member.status in ("left", "kicked"):
                not_joined.append(ch)
        except Exception:
            not_joined.append(ch)
    return not_joined

# ══════════════════════════════════════════════
#   JOIN REQUEST
# ══════════════════════════════════════════════

@start_router.chat_join_request()
async def on_join_request(update: ChatJoinRequest):
    try:
        await update.approve()
    except Exception as e:
        logging.warning(f"Join request approve xato: {e}")

# ══════════════════════════════════════════════
#   START
# ══════════════════════════════════════════════

@start_router.message(CommandStart())
async def cmd_start(message: Message):
    user = message.from_user
    add_user(user.id, user.username or "", user.full_name)

    if is_vip(user.id):
        until = get_vip_until(user.id)
        until_str = until.strftime("%d.%m.%Y") if until else "—"
        await message.answer(
            f"👑 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
            f"💎 Siz <b>VIP</b> foydalanuvchisiz!\n"
            f"📅 Muddati: <b>{until_str}</b> gacha\n\n"
            "🎬 Kino kodini yuboring!",
            parse_mode="HTML"
        )
        await message.answer("🛍 Do'konimiz ham bor — pastdagi tugma orqali oching:", reply_markup=shop_reply_keyboard())
        return

    if is_approved(user.id):
        await message.answer(
            f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
            "🎬 Kino kodini yuboring va filmni oling!\n\n"
            "💎 VIP orqali kinolarni yuklab olishingiz mumkin!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="💎 VIP olish", callback_data="vip_info")]
            ])
        )
        await message.answer("🛍 Do'konimiz ham bor — pastdagi tugma orqali oching:", reply_markup=shop_reply_keyboard())
        return

    await message.answer(
        f"👋 Xush kelibsiz, <b>{user.first_name}</b>!\n\n"
        "🔒 Botdan foydalanish uchun <b>4 ta kanalga</b> a'zo bo'ling.\n"
        "A'zo bo'lgach ✅ tugmasini bosing!\n\n"
        "💎 Yoki <b>VIP</b> oling — kanallarsiz ishlang!",
        parse_mode="HTML",
        reply_markup=channels_keyboard()
    )
    await message.answer("🛍 Do'konimiz ham bor — pastdagi tugma orqali oching:", reply_markup=shop_reply_keyboard())

@start_router.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery):
    user = callback.from_user

    if is_vip(user.id) or is_approved(user.id):
        await callback.message.edit_text(
            f"✅ <b>{user.first_name}</b>, siz allaqachon tasdiqlanganasiz!\n\n"
            "🎬 Kino kodini yuboring!",
            parse_mode="HTML"
        )
        return

    not_joined = await get_not_joined(callback.bot, user.id)

    if not not_joined:
        set_approved(user.id)
        try:
            total, approved, vip_cnt = get_user_count()
            uname = f"@{user.username}" if user.username else "username yo'q"
            await callback.bot.send_message(
                ADMIN_ID,
                f"🆕 <b>Yangi a'zo!</b>\n\n"
                f"👤 <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
                f"🔗 {uname}\n"
                f"🆔 <code>{user.id}</code>\n\n"
                f"📊 Jami: <b>{total}</b> | Tasdiqlangan: <b>{approved}</b>",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await callback.message.edit_text(
            f"✅ <b>Tabriklaymiz, {user.first_name}!</b>\n\n"
            "Siz barcha kanallarga a'zo bo'ldingiz!\n\n"
            "🎬 Endi kino kodini yuboring va filmni oling!",
            parse_mode="HTML"
        )
    else:
        names = "\n".join([f"• {ch['name']}" for ch in not_joined])
        await callback.answer(
            f"❌ Quyidagi kanallarga a'zo bo'lmadingiz:\n{names}",
            show_alert=True
        )

# ══════════════════════════════════════════════
#   VIP — INFO VA XARID
# ══════════════════════════════════════════════

@start_router.callback_query(F.data == "vip_info")
async def vip_info(callback: CallbackQuery):
    vip_text = get_setting("vip_text")
    await callback.message.answer(
        f"👑 <b>VIP OBUNA</b>\n\n"
        f"{vip_text}\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💳 To'lov kartasi:\n"
        f"<code>{get_setting('vip_card')}</code>\n"
        f"👤 {get_setting('vip_card_name')}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"📌 Muddatni tanlang:",
        parse_mode="HTML",
        reply_markup=vip_keyboard()
    )
    await callback.answer()

@start_router.callback_query(F.data == "vip_back")
async def vip_back(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer()

@start_router.callback_query(F.data.startswith("vip_buy_"))
async def vip_buy(callback: CallbackQuery):
    m_map     = {"1": "1 oy", "3": "3 oy", "6": "6 oy", "12": "1 yil"}
    price_map = {"1": "vip_price_1", "3": "vip_price_3", "6": "vip_price_6", "12": "vip_price_12"}
    m         = callback.data.split("_")[-1]
    months_label = m_map.get(m, m)
    price        = get_setting(price_map.get(m, "vip_price_1"))
    card         = get_setting("vip_card")
    card_name    = get_setting("vip_card_name")
    user         = callback.from_user

    # VIP so'rovini DB ga saqlaymiz
    req_id = add_vip_request(user.id, user.full_name, int(m))

    await callback.message.answer(
        f"💎 <b>VIP — {months_label}</b>\n\n"
        f"💰 Narx: <b>{price} so'm</b>\n\n"
        f"💳 Quyidagi kartaga o'tkazing:\n"
        f"<code>{card}</code>\n"
        f"👤 {card_name}\n\n"
        f"📸 To'lov qilgach <b>chek rasmini</b> shu botga yuboring!\n"
        f"Admin tekshirib VIP faollashtirib beradi.\n\n"
        f"🆔 So'rov ID: <code>{req_id}</code>",
        parse_mode="HTML"
    )

    # Adminga xabar
    try:
        await callback.bot.send_message(
            ADMIN_ID,
            f"💎 <b>Yangi VIP so'rovi!</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
            f"🆔 User ID: <code>{user.id}</code>\n"
            f"📦 Tarif: {months_label} — {price} so'm\n"
            f"🔖 So'rov ID: <code>{req_id}</code>\n\n"
            f"Chek kelgach tasdiqlash uchun:\n"
            f"/vip_add {user.id} {m}",
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer()

# ── CHEK SCREENSHOTI QABUL QILISH (VIP + DO'KON) ──

@start_router.message(StateFilter(None), F.photo)
async def receive_check_photo(message: Message):
    user = message.from_user
    if is_admin(user.id):
        return  # Admin yuborgan rasmni e'tiborsiz qoldirish

    # 1) Avval bu do'kon buyurtmasiga tegishli chekmi, tekshiramiz
    order_code = None
    if message.caption:
        for word in message.caption.split():
            if word.startswith("#A"):
                order_code = word
                break

    shop_order = get_order(order_code) if order_code else None
    if not shop_order:
        shop_order = find_pending_order_for_user(user.id)

    if shop_order:
        attach_receipt(shop_order["order_code"], message.photo[-1].file_id)
        caption = (
            f"🧾 Yangi to'lov cheki!\n\n"
            f"Buyurtma: {shop_order['order_code']}\n"
            f"Foydalanuvchi: @{user.username or user.id}\n"
            f"Oldindan to'lov: {shop_order['prepay_amount']:,} so'm\n"
            f"Umumiy summa: {shop_order['total']:,} so'm"
        )
        try:
            await message.bot.send_photo(
                ADMIN_ID, photo=message.photo[-1].file_id, caption=caption,
                reply_markup=order_status_keyboard(shop_order["order_code"])
            )
            await message.answer(f"✅ Chekingiz qabul qilindi, admin tez orada tekshiradi. Buyurtma: {shop_order['order_code']}")
        except Exception as e:
            logging.warning(f"Admin'ga do'kon cheki yuborilmadi: {e}")
        return

    # 2) Aks holda — VIP chek sifatida qabul qilamiz (eski xatti-harakat)
    try:
        await message.forward(ADMIN_ID)
        await message.bot.send_message(
            ADMIN_ID,
            f"📸 <b>VIP chek rasmi keldi!</b>\n\n"
            f"👤 <a href='tg://user?id={user.id}'>{user.full_name}</a>\n"
            f"🆔 <code>{user.id}</code>\n\n"
            f"Tasdiqlash: /vip_add {user.id} &lt;oy soni&gt;\n"
            f"Masalan: /vip_add {user.id} 1",
            parse_mode="HTML"
        )
        await message.answer(
            "✅ Chek rasmingiz adminga yuborildi!\n"
            "⏳ Admin tekshirib VIP faollashtirib beradi."
        )
    except Exception as e:
        logging.warning(f"Chek yuborishda xato: {e}")

# ══════════════════════════════════════════════
#   MOVIE HANDLER
# ══════════════════════════════════════════════

@movie_router.message(StateFilter(None), F.text.regexp(r"^\d+$"))
async def handle_code(message: Message):
    user_id = message.from_user.id
    code    = message.text.strip()

    vip      = is_vip(user_id)
    approved = is_approved(user_id)

    if not vip and not approved:
        await message.answer("🔒 Avval kanallarga a'zo bo'ling!\n/start")
        return

    movie = get_movie(code)
    if not movie:
        await message.answer(
            f"❌ <b>{code}</b> kodli kino topilmadi.",
            parse_mode="HTML"
        )
        return

    caption = (
        f"🎬 <b>{movie['title']}</b>\n"
        f"🔢 Kod: <code>{code}</code>\n\n"
        f"📢 Kanalimiz: {REELS_CHANNEL_LINK}"
    )
    if vip:
        caption += "\n\n👑 VIP"

    # VIP → himoyasiz (yuklab, ulasha oladi)
    # Oddiy → himoyalangan (yuklab, ulasha olmaydi)
    protect = not vip

    if movie["channel_message_id"]:
        try:
            await message.bot.copy_message(
                chat_id=message.chat.id,
                from_chat_id=MOVIE_CHANNEL_ID,
                message_id=movie["channel_message_id"],
                caption=caption,
                parse_mode="HTML",
                protect_content=protect
            )
            return
        except Exception as e:
            logging.warning(f"copy_message xato (code={code}): {e}")

    try:
        await message.answer_video(
            video=movie["file_id"],
            caption=caption,
            parse_mode="HTML",
            protect_content=protect
        )
    except Exception:
        try:
            await message.answer_document(
                document=movie["file_id"],
                caption=caption,
                parse_mode="HTML",
                protect_content=protect
            )
        except Exception as e:
            logging.error(f"Kino yuborishda xato (code={code}): {e}")
            await message.answer("⚠️ Kino yuborishda xato yuz berdi.")

# ══════════════════════════════════════════════
#   ADMIN PANEL
# ══════════════════════════════════════════════

@admin_router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id): return
    total, approved, vip_cnt = get_user_count()
    movies = get_all_movies()
    await message.answer(
        "🛠 <b>Admin Panel</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{total}</b>\n"
        f"✅ Tasdiqlangan: <b>{approved}</b>\n"
        f"👑 VIP: <b>{vip_cnt}</b>\n"
        f"🎬 Kinolar: <b>{len(movies)}</b>\n\n"
        "Quyidagi tugmalardan birini tanlang 👇",
        parse_mode="HTML",
        reply_markup=admin_keyboard()
    )

@admin_router.callback_query(F.data.startswith("adm_"))
async def admin_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ Ruxsat yo'q!", show_alert=True)
        return

    action = callback.data

    if action == "adm_refresh":
        total, approved, vip_cnt = get_user_count()
        movies = get_all_movies()
        await callback.message.edit_text(
            "🛠 <b>Admin Panel</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{total}</b>\n"
            f"✅ Tasdiqlangan: <b>{approved}</b>\n"
            f"👑 VIP: <b>{vip_cnt}</b>\n"
            f"🎬 Kinolar: <b>{len(movies)}</b>\n\n"
            "Quyidagi tugmalardan birini tanlang 👇",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        await callback.answer("✅ Yangilandi!")

    elif action == "adm_add_movie":
        await state.set_state(AddMovie.waiting_code)
        await callback.message.answer("🔢 Kino kodini kiriting (masalan: 234):")
        await callback.answer()

    elif action == "adm_add_reels":
        await state.set_state(AddReels.waiting_title)
        await callback.message.answer("📝 Reels uchun kino nomini kiriting:")
        await callback.answer()

    elif action == "adm_del_movie":
        await callback.message.answer(
            "🗑 O'chirish uchun kino kodini yuboring:\n"
            "<i>Masalan: <code>234</code></i>\n\n/cancel — bekor qilish",
            parse_mode="HTML"
        )
        await state.set_state(DeleteMovie.waiting_code)
        await callback.answer()

    elif action == "adm_movie_list":
        movies = get_all_movies()
        if not movies:
            await callback.answer("📭 Kinolar yo'q!", show_alert=True)
            return
        text = "🎬 <b>Kinolar ro'yxati:</b>\n\n"
        for m in movies:
            text += f"<code>{m['code']}</code> — {m['title']}\n"
        if len(text) > 4096:
            text = text[:4090] + "\n..."
        back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="adm_back")]])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
        await callback.answer()

    elif action == "adm_user_list":
        users = get_all_users()
        total = len(users)
        text  = f"👥 <b>Foydalanuvchilar ({total} ta):</b>\n\n"
        for u in users[:30]:
            icon = "👑" if u["is_vip"] else ("✅" if u["is_approved"] else "⏳")
            text += f"{icon} <a href='tg://user?id={u['user_id']}'>{u['full_name'] or 'Nomsiz'}</a> — <code>{u['user_id']}</code>\n"
        back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="adm_back")]])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
        await callback.answer()

    elif action == "adm_stats":
        total, approved, vip_cnt = get_user_count()
        movies = get_all_movies()
        text = (
            "📊 <b>Statistika</b>\n\n"
            f"👥 Jami: <b>{total}</b>\n"
            f"✅ Tasdiqlangan: <b>{approved}</b>\n"
            f"👑 VIP: <b>{vip_cnt}</b>\n"
            f"⏳ Tasdiqlanmagan: <b>{total - approved}</b>\n"
            f"🎬 Kinolar: <b>{len(movies)}</b>"
        )
        back_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Orqaga", callback_data="adm_back")]])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=back_kb)
        await callback.answer()

    elif action == "adm_broadcast":
        _, approved, _ = get_user_count()
        await state.set_state(Broadcast.waiting_message)
        await state.update_data(target="all")
        await callback.message.answer(
            f"📢 <b>Broadcast</b>\n\n"
            f"<b>{approved}</b> ta foydalanuvchiga xabar yuborasiz.\n\n"
            "Xabarni yozing:\n<i>/cancel — bekor qilish</i>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "adm_send_user":
        await callback.message.answer(
            "✉️ Foydalanuvchi ID sini yuboring:\n"
            "<i>Masalan: <code>123456789</code></i>\n\n/cancel — bekor qilish",
            parse_mode="HTML"
        )
        await state.set_state(SendUser.waiting_id)
        await callback.answer()

    elif action == "adm_vip_add":
        await callback.message.answer(
            "👑 VIP berish:\n"
            "/vip_add &lt;user_id&gt; &lt;oy&gt;\n\n"
            "Masalan: <code>/vip_add 123456 1</code>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "adm_vip_remove":
        await callback.message.answer(
            "👑 VIP olish:\n"
            "/vip_remove &lt;user_id&gt;\n\n"
            "Masalan: <code>/vip_remove 123456</code>",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "adm_vip_settings":
        await callback.message.answer(
            "⚙️ <b>VIP Sozlamalar</b>\n\n"
            f"💰 1 oy: <code>{get_setting('vip_price_1')}</code> so'm\n"
            f"💰 3 oy: <code>{get_setting('vip_price_3')}</code> so'm\n"
            f"💰 6 oy: <code>{get_setting('vip_price_6')}</code> so'm\n"
            f"💰 1 yil: <code>{get_setting('vip_price_12')}</code> so'm\n\n"
            f"💳 Karta: <code>{get_setting('vip_card')}</code>\n"
            f"👤 Egasi: {get_setting('vip_card_name')}\n\n"
            "📝 <b>O'zgartirish:</b>\n"
            "/set_vip_price_1 20000\n"
            "/set_vip_price_3 50000\n"
            "/set_vip_price_6 90000\n"
            "/set_vip_price_12 150000\n"
            "/set_vip_card 8600123412341234\n"
            "/set_vip_card_name Ism Familiya\n"
            "/set_vip_text — VIP matnini o'zgartirish",
            parse_mode="HTML"
        )
        await callback.answer()

    elif action == "adm_back":
        total, approved, vip_cnt = get_user_count()
        movies = get_all_movies()
        await callback.message.edit_text(
            "🛠 <b>Admin Panel</b>\n\n"
            f"👥 Foydalanuvchilar: <b>{total}</b>\n"
            f"✅ Tasdiqlangan: <b>{approved}</b>\n"
            f"👑 VIP: <b>{vip_cnt}</b>\n"
            f"🎬 Kinolar: <b>{len(movies)}</b>\n\n"
            "Quyidagi tugmalardan birini tanlang 👇",
            parse_mode="HTML",
            reply_markup=admin_keyboard()
        )
        await callback.answer()

# ══════════════════════════════════════════════
#   ADMIN — VIP BUYRUQLAR
# ══════════════════════════════════════════════

@admin_router.message(Command("vip_add"))
async def vip_add_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 3 or not parts[1].isdigit() or not parts[2].isdigit():
        await message.answer("❗ /vip_add <user_id> <oy>\nMasalan: /vip_add 123456 1")
        return
    uid    = int(parts[1])
    months = int(parts[2])
    until  = set_vip(uid, months)
    until_str = until.strftime("%d.%m.%Y")
    await message.answer(f"✅ VIP berildi!\n🆔 {uid}\n📅 {until_str} gacha")
    try:
        await message.bot.send_message(
            uid,
            f"👑 <b>Tabriklaymiz! Sizga VIP berildi!</b>\n\n"
            f"📅 Muddat: <b>{until_str}</b> gacha\n\n"
            f"✅ Endi kanallarsiz kino ko'rishingiz\n"
            f"✅ Kinolarni yuklab olishingiz\n"
            f"✅ Do'stlarga ulashishingiz mumkin!\n\n"
            f"🎬 Kino kodini yuboring!",
            parse_mode="HTML"
        )
    except Exception:
        pass

@admin_router.message(Command("vip_remove"))
async def vip_remove_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("❗ /vip_remove <user_id>")
        return
    remove_vip(int(parts[1]))
    await message.answer(f"✅ {parts[1]} dan VIP olindi.")

@admin_router.message(Command("vip_check"))
async def vip_check_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("❗ /vip_check <user_id>")
        return
    uid = int(parts[1])
    status = "👑 VIP ✅" if is_vip(uid) else "❌ VIP emas"
    until  = get_vip_until(uid)
    until_str = until.strftime("%d.%m.%Y") if until else "—"
    await message.answer(
        f"🆔 {uid}\n{status}\n📅 Muddat: {until_str}",
        parse_mode="HTML"
    )

# ── VIP SOZLAMALAR ────────────────────────────────

@admin_router.message(Command(
    "set_vip_price_1","set_vip_price_3",
    "set_vip_price_6","set_vip_price_12",
    "set_vip_card","set_vip_card_name"
))
async def set_vip_param(message: Message):
    if not is_admin(message.from_user.id): return
    cmd = message.text.split()[0].lstrip("/")
    key_map = {
        "set_vip_price_1":  "vip_price_1",
        "set_vip_price_3":  "vip_price_3",
        "set_vip_price_6":  "vip_price_6",
        "set_vip_price_12": "vip_price_12",
        "set_vip_card":     "vip_card",
        "set_vip_card_name":"vip_card_name",
    }
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❗ Qiymatni ham yozing.")
        return
    set_setting(key_map[cmd], parts[1].strip())
    await message.answer(f"✅ Saqlandi: <code>{parts[1].strip()}</code>", parse_mode="HTML")

@admin_router.message(Command("set_vip_text"))
async def set_vip_text_cmd(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(VipSettings.waiting_value)
    await message.answer(
        f"📝 Yangi VIP matnini yuboring:\n\n"
        f"Hozirgi:\n{get_setting('vip_text')}"
    )

@admin_router.message(VipSettings.waiting_value)
async def set_vip_text_save(message: Message, state: FSMContext):
    set_setting("vip_text", message.text)
    await state.clear()
    await message.answer("✅ VIP matni saqlandi!")

# ══════════════════════════════════════════════
#   ADMIN — KINO QO'SHISH
# ══════════════════════════════════════════════

@admin_router.message(Command("add_movie"))
async def add_movie_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddMovie.waiting_code)
    await message.answer("🔢 Kino kodini kiriting (masalan: 234):")

@admin_router.message(AddMovie.waiting_code)
async def add_movie_code(message: Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("❌ Faqat raqam kiriting:")
        return
    await state.update_data(code=message.text.strip())
    await state.set_state(AddMovie.waiting_title)
    await message.answer("📝 Kino nomini kiriting:")

@admin_router.message(AddMovie.waiting_title)
async def add_movie_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AddMovie.waiting_reels)
    await message.answer(
        "📸 <b>Ochiq kanal</b> uchun Reels yoki rasm yuboring:\n"
        "<i>(Bu ochiq kanalga joylashadi)</i>",
        parse_mode="HTML"
    )

@admin_router.message(AddMovie.waiting_reels, F.video | F.photo | F.document)
async def add_movie_reels(message: Message, state: FSMContext):
    data = await state.get_data()
    bot_info = await message.bot.get_me()
    bot_username = f"@{bot_info.username}"
    reels_caption = (
        f"🎬 <b>{data['title']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📥 To'liq kinoni olish uchun:\n"
        f"👉 {bot_username} ga <code>{data['code']}</code> kodini yuboring!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔔 Kanalga obuna bo'ling: {REELS_CHANNEL_LINK}"
    )
    reels_ok = False
    try:
        if message.photo:
            await message.bot.send_photo(chat_id=REELS_CHANNEL_ID, photo=message.photo[-1].file_id, caption=reels_caption, parse_mode="HTML")
        elif message.video:
            await message.bot.send_video(chat_id=REELS_CHANNEL_ID, video=message.video.file_id, caption=reels_caption, parse_mode="HTML")
        else:
            await message.bot.send_document(chat_id=REELS_CHANNEL_ID, document=message.document.file_id, caption=reels_caption, parse_mode="HTML")
        reels_ok = True
    except Exception as e:
        logging.warning(f"Reels yuborishda xato: {e}")

    await state.set_state(AddMovie.waiting_file)
    status = "✅ Reels ochiq kanalga yuborildi!" if reels_ok else "⚠️ Reels xato, lekin davom etamiz."
    await message.answer(
        f"{status}\n\n"
        "🎬 Endi <b>to'liq kinoni</b> yuboring:\n"
        "<i>(Bu maxfiy kanalga saqlanadi)</i>",
        parse_mode="HTML"
    )

@admin_router.message(AddMovie.waiting_file, F.video | F.document)
async def add_movie_file(message: Message, state: FSMContext):
    data    = await state.get_data()
    file_id = message.video.file_id if message.video else message.document.file_id
    channel_msg_id = None
    try:
        sent = await message.bot.copy_message(
            chat_id=MOVIE_CHANNEL_ID,
            from_chat_id=message.chat.id,
            message_id=message.message_id,
            caption=f"🎬 <b>{data['title']}</b>\n🔢 Kod: <code>{data['code']}</code>",
            parse_mode="HTML"
        )
        channel_msg_id = sent.message_id
    except Exception as e:
        logging.warning(f"Yopiq kanalga yuborishda xato: {e}")

    add_movie(data["code"], data["title"], file_id, channel_msg_id)
    await state.clear()
    await message.answer(
        f"✅ <b>Hammasi tayyor!</b>\n\n"
        f"📌 Kod: <code>{data['code']}</code>\n"
        f"🎬 {data['title']}\n\n"
        f"📢 Reels → ochiq kanal\n"
        f"🔒 To'liq kino → maxfiy kanal",
        parse_mode="HTML"
    )

    # Barcha userlarga bildirishnoma
    bot_info = await message.bot.get_me()
    notify_text = (
        f"🎬 <b>Yangi kino qo'shildi!</b>\n\n"
        f"🎞 <b>{data['title']}</b>\n\n"
        f"📥 Olish uchun @{bot_info.username} ga:\n"
        f"👉 <code>{data['code']}</code>"
    )
    notify_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="🎬 Kinoni olish", url=f"https://t.me/{bot_info.username}?start=movie_{data['code']}")
    ]])
    users = get_approved_users()
    notify_ok = 0
    for u in users:
        try:
            await message.bot.send_message(u["user_id"], notify_text, parse_mode="HTML", reply_markup=notify_kb)
            notify_ok += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await message.answer(f"📣 Bildirishnoma <b>{notify_ok}</b> ta foydalanuvchiga yuborildi!", parse_mode="HTML")

# ── REELS QO'SHISH ────────────────────────────────

@admin_router.message(Command("add_reels"))
async def add_reels_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddReels.waiting_title)
    await message.answer("📝 Reels uchun kino nomini kiriting:")

@admin_router.message(AddReels.waiting_title)
async def add_reels_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text.strip())
    await state.set_state(AddReels.waiting_file)
    await message.answer("🎬 Reels/parcha videoni yuboring:")

@admin_router.message(AddReels.waiting_file, F.video | F.document)
async def add_reels_file(message: Message, state: FSMContext):
    data    = await state.get_data()
    file_id = message.video.file_id if message.video else message.document.file_id
    bot_info = await message.bot.get_me()
    caption = (
        f"🎬 <b>{data['title']}</b>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📥 To'liq kinoni olish uchun:\n"
        f"👉 @{bot_info.username} botiga kino kodini yuboring!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔔 Kanalga obuna bo'ling: {REELS_CHANNEL_LINK}"
    )
    try:
        if message.video:
            await message.bot.send_video(chat_id=REELS_CHANNEL_ID, video=file_id, caption=caption, parse_mode="HTML")
        else:
            await message.bot.send_document(chat_id=REELS_CHANNEL_ID, document=file_id, caption=caption, parse_mode="HTML")
        await state.clear()
        await message.answer(f"✅ Reels kanalga yuborildi!\n🎬 {data['title']}", parse_mode="HTML")
    except Exception as e:
        await state.clear()
        await message.answer(f"⚠️ Xato: {e}")

# ── BOSHQA ADMIN ──────────────────────────────────

@admin_router.message(Command("del_movie"))
async def del_movie_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2:
        await message.answer("❗ /del_movie <kod>", parse_mode="HTML")
        return
    code = parts[1]
    if not get_movie(code):
        await message.answer(f"❌ <code>{code}</code> topilmadi.", parse_mode="HTML")
        return
    delete_movie(code)
    await message.answer(f"🗑 <code>{code}</code> o'chirildi.", parse_mode="HTML")

@admin_router.message(Command("movie_list"))
async def movie_list_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    movies = get_all_movies()
    if not movies:
        await message.answer("📭 Kinolar yo'q.")
        return
    text = "🎬 <b>Kinolar:</b>\n\n"
    for m in movies:
        text += f"<code>{m['code']}</code> — {m['title']}\n"
    if len(text) > 4096:
        text = text[:4090] + "\n..."
    await message.answer(text, parse_mode="HTML")

@admin_router.message(Command("user_list"))
async def user_list_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    users = get_all_users()
    if not users:
        await message.answer("📭 Foydalanuvchilar yo'q.")
        return
    total = len(users)
    text  = f"👥 <b>Foydalanuvchilar ({total} ta):</b>\n\n"
    for u in users[:30]:
        icon = "👑" if u["is_vip"] else ("✅" if u["is_approved"] else "⏳")
        text += f"{icon} <a href='tg://user?id={u['user_id']}'>{u['full_name'] or 'Nomsiz'}</a>\n"
    await message.answer(text, parse_mode="HTML")

@admin_router.message(Command("stats"))
async def stats_cmd(message: Message):
    if not is_admin(message.from_user.id): return
    total, approved, vip_cnt = get_user_count()
    movies = get_all_movies()
    await message.answer(
        "📊 <b>Statistika</b>\n\n"
        f"👥 Jami: <b>{total}</b>\n"
        f"✅ Tasdiqlangan: <b>{approved}</b>\n"
        f"👑 VIP: <b>{vip_cnt}</b>\n"
        f"⏳ Tasdiqlanmagan: <b>{total - approved}</b>\n"
        f"🎬 Kinolar: <b>{len(movies)}</b>",
        parse_mode="HTML"
    )

@admin_router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    _, approved, _ = get_user_count()
    await state.set_state(Broadcast.waiting_message)
    await state.update_data(target="all")
    await message.answer(
        f"📢 <b>{approved}</b> ta foydalanuvchiga xabar yuborasiz.\n\n"
        "Xabarni yozing:\n<i>/cancel — bekor qilish</i>",
        parse_mode="HTML"
    )

@admin_router.message(Command("cancel"), StateFilter("*"))
async def cancel_cmd(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.clear()
    await message.answer("❌ Bekor qilindi.")

@admin_router.message(Broadcast.waiting_message)
async def broadcast_send(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    target = data.get("target", "all")
    users = get_approved_users() if target == "all" else [{"user_id": int(target)}]
    ok, fail = 0, 0
    progress = await message.answer(f"⏳ Yuborilmoqda... 0/{len(users)}")
    for i, u in enumerate(users):
        try:
            await message.copy_to(chat_id=u["user_id"])
            ok += 1
        except Exception:
            fail += 1
        if (i + 1) % 20 == 0:
            try:
                await progress.edit_text(f"⏳ Yuborilmoqda... {i+1}/{len(users)}")
            except Exception:
                pass
        await asyncio.sleep(0.05)
    await progress.edit_text(
        f"✅ <b>Broadcast tugadi!</b>\n\n📨 Yuborildi: <b>{ok}</b>\n❌ Yuborilmadi: <b>{fail}</b>",
        parse_mode="HTML"
    )

@admin_router.message(Command("send_user"))
async def send_user_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.answer("❗ /send_user <user_id>", parse_mode="HTML")
        return
    await state.set_state(Broadcast.waiting_message)
    await state.update_data(target=parts[1])
    await message.answer(
        f"✉️ <code>{parts[1]}</code> ga xabarni yozing:\n<i>/cancel — bekor qilish</i>",
        parse_mode="HTML"
    )

@admin_router.message(DeleteMovie.waiting_code)
async def delete_movie_fsm(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    code = message.text.strip()
    if not code.isdigit():
        await message.answer("❌ Faqat raqam kiriting yoki /cancel:")
        return
    if not get_movie(code):
        await message.answer(f"❌ <code>{code}</code> topilmadi. Qayta kiriting yoki /cancel:", parse_mode="HTML")
        return
    delete_movie(code)
    await state.clear()
    await message.answer(
        f"🗑 <code>{code}</code> o'chirildi!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="◀️ Admin Panel", callback_data="adm_back")
        ]])
    )

@admin_router.message(SendUser.waiting_id)
async def send_user_id_fsm(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    uid = message.text.strip()
    if not uid.isdigit():
        await message.answer("❌ Faqat raqam kiriting yoki /cancel:")
        return
    await state.update_data(target=uid)
    await state.set_state(SendUser.waiting_message)
    await message.answer(
        f"✉️ <code>{uid}</code> ga xabarni yozing:\n<i>/cancel — bekor qilish</i>",
        parse_mode="HTML"
    )

@admin_router.message(SendUser.waiting_message)
async def send_user_msg_fsm(message: Message, state: FSMContext):
    data = await state.get_data()
    uid = int(data["target"])
    await state.clear()
    try:
        await message.copy_to(chat_id=uid)
        await message.answer(f"✅ <code>{uid}</code> ga yuborildi!", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"❌ Xato: {e}")

# ══════════════════════════════════════════════
#   DO'KON (SHOP) — mahsulot qo'shish, buyurtmalar
# ══════════════════════════════════════════════

SHOP_CATEGORIES = ["Oziq-ovqat", "Maishiy", "Elektronika"]  # kerak bo'lsa kengaytiring

STATUS_LABELS = {
    "yangi": "🆕 Yangi",
    "qabul_qilindi": "✅ Qabul qilindi",
    "yolda": "🚚 Yo'lda",
    "yetkazildi": "📦 Yetkazildi",
    "bekor": "❌ Bekor qilindi",
}

def order_status_keyboard(order_code):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Qabul qilindi", callback_data=f"ost:{order_code}:qabul_qilindi")],
        [InlineKeyboardButton(text="🚚 Yo'lda", callback_data=f"ost:{order_code}:yolda")],
        [InlineKeyboardButton(text="📦 Yetkazildi", callback_data=f"ost:{order_code}:yetkazildi")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data=f"ost:{order_code}:bekor")],
    ])

# ── Mahsulot qo'shish (FSM) ──

@shop_router.message(Command("addproduct"), StateFilter(None))
async def add_product_start(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id): return
    await state.set_state(AddProduct.name)
    await message.answer("📦 Mahsulot nomini kiriting:\n\n/cancel — bekor qilish")

@shop_router.message(Command("cancel"), StateFilter(
    AddProduct.name, AddProduct.price, AddProduct.emoji, AddProduct.photo, AddProduct.stock
))
async def add_product_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.")

@shop_router.message(AddProduct.name, F.text)
async def add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text.strip())
    await state.set_state(AddProduct.price)
    await message.answer("💰 Narxini so'mda kiriting (masalan: 25000):")

@shop_router.message(AddProduct.price, F.text)
async def add_product_price(message: Message, state: FSMContext):
    text = message.text.strip().replace(" ", "")
    if not text.isdigit():
        await message.answer("❌ Faqat raqam kiriting. Masalan: 25000")
        return
    await state.update_data(price=int(text))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=c, callback_data=f"cat:{c}")] for c in SHOP_CATEGORIES
    ])
    await state.set_state(AddProduct.category)
    await message.answer("📂 Kategoriyani tanlang:", reply_markup=kb)

@shop_router.callback_query(AddProduct.category, F.data.startswith("cat:"))
async def add_product_category(callback: CallbackQuery, state: FSMContext):
    category = callback.data.split(":", 1)[1]
    await state.update_data(category=category)
    await state.set_state(AddProduct.emoji)
    await callback.message.answer("🙂 Mahsulot uchun bitta emoji yuboring (masalan: 🍬):")
    await callback.answer()

@shop_router.message(AddProduct.emoji, F.text)
async def add_product_emoji(message: Message, state: FSMContext):
    await state.update_data(emoji=message.text.strip()[:4])
    await state.set_state(AddProduct.photo)
    await message.answer("🖼 Mahsulot rasmini yuboring (yoki /skip — o'tkazib yuborish):")

@shop_router.message(AddProduct.photo, F.photo)
async def add_product_photo(message: Message, state: FSMContext):
    await state.update_data(photo_file_id=message.photo[-1].file_id)
    await state.set_state(AddProduct.stock)
    await message.answer("📊 Omborda nechta dona bor?")

@shop_router.message(AddProduct.photo, Command("skip"))
async def add_product_photo_skip(message: Message, state: FSMContext):
    await state.update_data(photo_file_id=None)
    await state.set_state(AddProduct.stock)
    await message.answer("📊 Omborda nechta dona bor?")

@shop_router.message(AddProduct.stock, F.text)
async def add_product_stock(message: Message, state: FSMContext):
    text = message.text.strip()
    if not text.isdigit():
        await message.answer("❌ Faqat raqam kiriting.")
        return
    data = await state.update_data(stock=int(text))
    product_id = add_product(
        name=data["name"], price=data["price"], category=data["category"],
        emoji=data.get("emoji", "📦"), photo_file_id=data.get("photo_file_id"), stock=data["stock"]
    )
    await state.clear()
    await message.answer(
        f"✅ Mahsulot qo'shildi!\n\n"
        f"{data.get('emoji', '📦')} {data['name']}\n"
        f"💰 {data['price']:,} so'm\n"
        f"📂 {data['category']}\n"
        f"📊 {data['stock']} dona\n\n"
        f"ID: {product_id}"
    )

@shop_router.message(Command("products"))
async def list_products(message: Message):
    if not is_admin(message.from_user.id): return
    products = get_products(active_only=True)
    if not products:
        await message.answer("Hozircha mahsulot yo'q. /addproduct orqali qo'shing.")
        return
    for p in products:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 O'chirish", callback_data=f"delprod:{p['id']}")
        ]])
        await message.answer(
            f"{p['emoji']} {p['name']}\n💰 {p['price']:,} so'm | 📊 {p['stock']} dona\nID: {p['id']}",
            reply_markup=kb
        )

@shop_router.callback_query(F.data.startswith("delprod:"))
async def delete_product_cb(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    product_id = int(callback.data.split(":", 1)[1])
    deactivate_product(product_id)
    await callback.message.edit_text(callback.message.text + "\n\n❌ O'CHIRILDI")
    await callback.answer("O'chirildi")

# ── Kunlik bildirishnoma matnini sozlash ──

@shop_router.message(Command("dailymsg"))
async def set_daily_broadcast_text(message: Message):
    if not is_admin(message.from_user.id): return
    text = message.text.replace("/dailymsg", "", 1).strip()
    if not text:
        current = get_setting("daily_broadcast_text")
        await message.answer(
            f"📢 Joriy kunlik xabar matni:\n\n{current}\n\n"
            f"O'zgartirish uchun: <code>/dailymsg Yangi matn</code>\n"
            f"Har kuni soat <b>{DAILY_BROADCAST_HOUR:02d}:{DAILY_BROADCAST_MINUTE:02d}</b> da barcha userlarga yuboriladi.",
            parse_mode="HTML"
        )
        return
    set_setting("daily_broadcast_text", text)
    await message.answer(f"✅ Kunlik xabar matni yangilandi:\n\n{text}")

# ── Buyurtma holati (admin boshqaradi) ──

@shop_router.callback_query(F.data.startswith("ost:"))
async def order_status_change(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("Ruxsat yo'q", show_alert=True)
        return
    _, order_code, new_status = callback.data.split(":", 2)
    update_order_status(order_code, new_status)
    order = get_order(order_code)
    await callback.message.edit_reply_markup(reply_markup=order_status_keyboard(order_code))
    await callback.answer(f"Holat: {STATUS_LABELS[new_status]}")
    try:
        await callback.bot.send_message(
            order["user_id"],
            f"📦 Buyurtmangiz {order_code} holati o'zgardi:\n{STATUS_LABELS[new_status]}"
        )
    except Exception as e:
        logging.warning(f"User'ga xabar yuborib bo'lmadi: {e}")

# Eslatma: to'lov chekini qabul qilish logikasi start_router'dagi
# receive_check_photo funksiyasiga birlashtirilgan (VIP chek bilan bitta
# handlerda, chunki bir xil filtr — F.photo, StateFilter(None) — ikkita
# alohida routerda ishlaganda faqat birinchisi ishga tushar edi).


# ══════════════════════════════════════════════
#   BACKEND API — WebApp shu yerdan mahsulot/hudud
#   oladi va buyurtma yuboradi
# ══════════════════════════════════════════════

async def api_get_products(request):
    return web.json_response(get_products(active_only=True))

async def api_get_regions(request):
    return web.json_response(REGIONS)

async def api_create_order(request):
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Noto'g'ri so'rov"}, status=400)

    user_id = data.get("user_id")
    username = data.get("username")
    items = data.get("items", [])
    region_id = data.get("region_id")
    district_id = data.get("district_id")
    pay_type = data.get("pay_type")

    if not (user_id and items and region_id and district_id and pay_type in ("cash", "card")):
        return web.json_response({"error": "Ma'lumotlar yetarli emas"}, status=400)

    subtotal = 0
    order_items = []
    for it in items:
        product = get_product(it.get("product_id"))
        if not product:
            continue
        qty = max(1, int(it.get("qty", 1)))
        subtotal += product["price"] * qty
        order_items.append({
            "product_id": product["id"], "name": product["name"],
            "price": product["price"], "qty": qty
        })

    if not order_items:
        return web.json_response({"error": "Savat bo'sh"}, status=400)

    district = find_district(region_id, district_id)
    if not district:
        return web.json_response({"error": "Hudud topilmadi"}, status=400)

    fee = delivery_fee(district["km"])
    total = subtotal + fee
    percent = PREPAY_PERCENT_CARD if pay_type == "card" else PREPAY_PERCENT_CASH
    prepay = round(total * percent / 100)  # endi HAR IKKI to'lov turida ham majburiy
    remain = total - prepay

    order_code = next_order_code()
    create_order(order_code, user_id, username, order_items, subtotal, fee,
                 total, region_id, district_id, pay_type, prepay)

    remain_label = "Naqd (kuryerga)" if pay_type == "cash" else "Karta orqali"
    items_text = "\n".join(f"• {i['name']} ×{i['qty']} — {i['price']*i['qty']:,} so'm" for i in order_items)
    text = (
        f"🆕 Yangi buyurtma {order_code}\n\n"
        f"{items_text}\n\n"
        f"Mahsulotlar: {subtotal:,} so'm\n"
        f"Yetkazib berish: {fee:,} so'm ({district['name']})\n"
        f"Jami: {total:,} so'm\n\n"
        f"💳 Oldindan to'lov ({percent}%, chek kutilmoqda): {prepay:,} so'm\n"
        f"Qolgan qism to'lovi: {remain_label} — {remain:,} so'm\n"
    )
    text += f"\nFoydalanuvchi: @{username or user_id} (ID: {user_id})"

    try:
        await request.app["bot"].send_message(ADMIN_ID, text, reply_markup=order_status_keyboard(order_code))
    except Exception as e:
        logging.warning(f"Admin'ga buyurtma xabari yuborilmadi: {e}")

    return web.json_response({
        "order_code": order_code,
        "subtotal": subtotal,
        "delivery_fee": fee,
        "total": total,
        "prepay_amount": prepay,
        "prepay_percent": percent,
        "remain_amount": remain,
        "district_name": district["name"],
        "payment_card": get_setting("shop_card"),
        "payment_owner": get_setting("shop_card_name"),
    })

async def api_get_order_status(request):
    order_code = request.match_info.get("code")
    order = get_order(order_code)
    if not order:
        return web.json_response({"error": "Topilmadi"}, status=404)
    return web.json_response({"order_code": order["order_code"], "status": order["status"]})

def build_shop_api(bot: Bot):
    app = web.Application()
    app["bot"] = bot
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True, expose_headers="*", allow_headers="*", allow_methods="*"
        )
    })
    routes = [
        web.get("/api/products", api_get_products),
        web.get("/api/regions", api_get_regions),
        web.post("/api/orders", api_create_order),
        web.get("/api/orders/{code}", api_get_order_status),
    ]
    for route in routes:
        cors.add(app.router.add_route(route.method, route.path, route.handler))
    return app

# ══════════════════════════════════════════════
#   KUNLIK AVTOMATIK BILDIRISHNOMA (VIP ham, oddiy ham)
# ══════════════════════════════════════════════

async def daily_broadcast_task(bot: Bot):
    while True:
        now = datetime.now()
        target = now.replace(hour=DAILY_BROADCAST_HOUR, minute=DAILY_BROADCAST_MINUTE, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        wait_seconds = (target - now).total_seconds()
        logging.info(f"📢 Kunlik bildirishnoma keyingi safar: {target.strftime('%Y-%m-%d %H:%M')}")
        await asyncio.sleep(wait_seconds)

        text = get_setting("daily_broadcast_text")
        users = get_all_users()
        sent, failed = 0, 0
        for u in users:
            try:
                await bot.send_message(
                    u["user_id"], text, parse_mode="HTML",
                    reply_markup=shop_reply_keyboard()
                )
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)  # flood limitga tushmaslik uchun
        logging.info(f"📢 Kunlik bildirishnoma yuborildi: {sent} ta muvaffaqiyatli, {failed} ta xato")

# ══════════════════════════════════════════════
#   MAIN
# ══════════════════════════════════════════════

async def main():
    os.makedirs("data", exist_ok=True)
    init_db()

    bot = Bot(token=BOT_TOKEN)
    dp  = Dispatcher(storage=MemoryStorage())

    dp.include_router(shop_router)   # AddProduct FSM va boshqalar (o'ziga xos state'lar) — birinchi
    dp.include_router(start_router)  # /start, chek qabul qilish (VIP + do'kon birlashgan)
    dp.include_router(admin_router)
    dp.include_router(movie_router)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    logging.info("🚀 Bot ishga tushdi!")

    # Do'kon backend API'sini ishga tushirish (WebApp shu yerdan mahsulot/hudud oladi)
    api_app = build_shop_api(bot)
    runner = web.AppRunner(api_app)
    await runner.setup()
    site = web.TCPSite(runner, API_HOST, API_PORT)
    await site.start()
    logging.info(f"🛍 Do'kon API ishga tushdi: http://{API_HOST}:{API_PORT}")

    # Kunlik avtomatik bildirishnoma (background'da)
    asyncio.create_task(daily_broadcast_task(bot))

    await dp.start_polling(
        bot,
        allowed_updates=["message", "callback_query", "chat_join_request"]
    )

if __name__ == "__main__":
    asyncio.run(main())
