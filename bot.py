import os
import sqlite3
import asyncio
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "kartahisob.db")
LIMIT = 24
MAX_PLAYERS = 6


# Render uchun HTTP port
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"KartaHisobBot is running!")

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# Ma'lumotlar bazasi
def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def setup():
    with connect() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                champion_id INTEGER
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                score INTEGER DEFAULT 0,
                out INTEGER DEFAULT 0,
                UNIQUE(game_id, user_id)
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER,
                player_id INTEGER,
                amount INTEGER,
                by_user INTEGER,
                created_at TEXT
            )
        """)


def active_game(chat_id):
    with connect() as con:
        return con.execute(
            """
            SELECT * FROM games
            WHERE chat_id=? AND status='active'
            ORDER BY id DESC LIMIT 1
            """,
            (chat_id,),
        ).fetchone()


def latest_game(chat_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM games WHERE chat_id=? ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()


def get_players(game_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM players WHERE game_id=? ORDER BY id",
            (game_id,),
        ).fetchall()


# Umumiy hisob jadvali
def make_board(chat_id):
    game = latest_game(chat_id)

    if not game:
        return "Hozircha o‘yin yo‘q. Yangi o‘yin uchun /newgame yuboring.", None

    players = get_players(game["id"])
    lines = [
        f"🃏 KartaHisobBot — O‘yin #{game['id']}",
        "",
    ]

    for p in players:
        if p["id"] == game["champion_id"]:
            lines.append(
                f"🏆 {p['name']} — {p['score']} ball · CHEMPION"
            )
        elif p["out"] or p["score"] >= LIMIT:
            lines.append(
                f"🔴 {p['name']} — {p['score']} ball · SABIN"
            )
        else:
            lines.append(
                f"🟢 {p['name']} — {p['score']} ball"
            )

    if not players:
        lines.append("O‘yinchilar /join yuborishsin.")

    if game["status"] == "finished":
        lines.extend(["", "🏁 O‘yin yakunlandi!"])
        if game["champion_id"]:
            lines.append("Tabriklaymiz, CHEMPION! 🏆")

    elif len(players) >= 2:
        lines.extend(["", "Ball qo‘shish uchun o‘yinchini tanlang."])

    rows = []

    if game["status"] == "active":
        for p in players:
            if not p["out"] and p["score"] < LIMIT:
                rows.append([
                    InlineKeyboardButton(
                        f"➕ {p['name']}ga ball",
                        callback_data=f"pick:{p['id']}",
                    )
                ])

    rows.append([
        InlineKeyboardButton(
            "🔄 Hisobni yangilash",
            callback_data="refresh",
        )
    ])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Salom! Men KartaHisobBotman 🃏\n\n"
        "/newgame — yangi o‘yin\n"
        "/join — o‘yinga qo‘shilish\n"
        "/score — hisobni ko‘rish\n"
        "/history — o‘yinlar tarixi\n\n"
        "O‘yin 24 ballgacha davom etadi. "
        "24 ballga yetgan o‘yinchi SABIN bo‘ladi!"
    )


# /newgame
async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with connect() as con:
        con.execute(
            """
            UPDATE games SET status='finished'
            WHERE chat_id=? AND status='active'
            """,
            (chat_id,),
        )

        cur = con.execute(
            """
            INSERT INTO games(chat_id, status, created_at)
            VALUES(?, 'active', ?)
            """,
            (chat_id, datetime.now().isoformat(timespec="seconds")),
        )
        game_id = cur.lastrowid

    await update.effective_message.reply_text(
        f"🃏 Yangi o‘yin #{game_id} boshlandi!\n\n"
        "Har bir o‘yinchi /join yuborsin.\n"
        "Eng ko‘pi bilan 6 kishi qatnashadi."
    )


# /join
async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    game = active_game(chat_id)

    if not game:
        await update.effective_message.reply_text(
            "Avval /newgame yuboring."
        )
        return

    with connect() as con:
        existing = con.execute(
            "SELECT id FROM players WHERE game_id=? AND user_id=?",
            (game["id"], user.id),
        ).fetchone()

        if existing:
            await update.effective_message.reply_text(
                "Siz allaqachon o‘yindasiz."
            )
            return

        count = con.execute(
            "SELECT COUNT(*) AS n FROM players WHERE game_id=?",
            (game["id"],),
        ).fetchone()["n"]

        if count >= MAX_PLAYERS:
            await update.effective_message.reply_text(
                "O‘yinda 6 kishi bor. Yangi o‘yinchini qo‘shib bo‘lmaydi."
            )
            return

        con.execute(
            """
            INSERT INTO players(game_id, user_id, name)
            VALUES(?, ?, ?)
            """,
            (game["id"], user.id, user.full_name or user.first_name),
        )

    text, markup = make_board(chat_id)
    await update.effective_message.reply_text(text, reply_markup=markup)


# /score
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = make_board(update.effective_chat.id)
    await update.effective_message.reply_text(text, reply_markup=markup)


# /history
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with connect() as con:
        games = con.execute(
            """
            SELECT * FROM games
            WHERE chat_id=?
            ORDER BY id DESC LIMIT 10
            """,
            (chat_id,),
        ).fetchall()

        if not games:
            await update.effective_message.reply_text(
                "Hali o‘yinlar tarixi yo‘q."
            )
            return

        lines = ["📚 O‘yinlar tarixi:"]

        for game in games:
            players = con.execute(
                """
                SELECT * FROM players
                WHERE game_id=?
                ORDER BY score DESC
                """,
                (game["id"],),
            ).fetchall()

            lines.append(
                f"\nO‘yin #{game['id']} — {game['status']}"
            )

            for p in players:
                if p["id"] == game["champion_id"]:
                    tag = " 🏆 CHEMPION"
                elif p["out"] or p["score"] >= LIMIT:
                    tag = " · SABIN"
                else:
                    tag = ""

                lines.append(
                    f"• {p['name']}: {p['score']} ball{tag}"
                )

    await update.effective_message.reply_text("\n".join(lines))


# Tugmalar
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    chat_id = q.message.chat_id

    if q.data == "refresh":
        await q.answer()
        text, markup = make_board(chat_id)
        await q.edit_message_text(text, reply_markup=markup)
        return

    if q.data.startswith("pick:"):
        player_id = int(q.data.split(":")[1])
        game = active_game(chat_id)

        if not game:
            await q.answer("Faol o‘yin yo‘q.", show_alert=True)
            return

        with connect() as con:
            p = con.execute(
                """
                SELECT * FROM players
                WHERE id=? AND game_id=?
                """,
                (player_id, game["id"]),
            ).fetchone()

        if not p or p["out"] or p["score"] >= LIMIT:
            await q.answer(
                "Bu o‘yinchi SABIN bo‘lgan.",
                show_alert=True,
            )
            return

        await q.answer()

        amounts = [1, 2, 3, 5, 10]
        rows = [
            [
                InlineKeyboardButton(
                    f"+{n}",
                    callback_data=f"add:{player_id}:{n}",
                )
                for n in amounts[:3]
            ],
            [
                InlineKeyboardButton(
                    f"+{n}",
                    callback_data=f"add:{player_id}:{n}",
                )
                for n in amounts[3:]
            ],
        ]

        await q.message.reply_text(
            f"{p['name']}ga nechta ball qo‘shamiz?",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if q.data.startswith("add:"):
        _, pid, amount = q.data.split(":")
        pid = int(pid)
        amount = int(amount)
        game = active_game(chat_id)

        if not game:
            await q.answer("Faol o‘yin yo‘q.", show_alert=True)
            return

        with connect() as con:
            p = con.execute(
                """
                SELECT * FROM players
                WHERE id=? AND game_id=?
                """,
                (pid, game["id"]),
            ).fetchone()

            if not p or p["out"] or p["score"] >= LIMIT:
                await q.answer(
                    "Bu o‘yinchi o‘yindan chiqqan.",
                    show_alert=True,
                )
                return

            new_score = p["score"] + amount
            is_out = int(new_score >= LIMIT)

            con.execute(
                "UPDATE players SET score=?, out=? WHERE id=?",
                (new_score, is_out, pid),
            )

            con.execute(
                """
                INSERT INTO events(
                    game_id, player_id, amount, by_user, created_at
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (
                    game["id"],
                    pid,
                    amount,
                    q.from_user.id,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

            remaining = con.execute(
                """
                SELECT * FROM players
                WHERE game_id=? AND out=0 AND score<?
                """,
                (game["id"], LIMIT),
            ).fetchall()

            total = con.execute(
                "SELECT COUNT(*) AS n FROM players WHERE game_id=?",
                (game["id"],),
            ).fetchone()["n"]

            if total >= 2 and len(remaining) == 1:
                con.execute(
                    """
                    UPDATE games
                    SET status='finished', champion_id=?
                    WHERE id=?
                    """,
                    (remaining[0]["id"], game["id"]),
                )
            elif total >= 2 and len(remaining) == 0:
                con.execute(
                    """
                    UPDATE games SET status='finished'
                    WHERE id=?
                    """,
                    (game["id"],),
                )

        await q.answer("Ball qo‘shildi!")
        text, markup = make_board(chat_id)
        await q.message.reply_text(text, reply_markup=markup)
        return

    await q.answer()


# Botni ishga tushirish
def main():
    if not TOKEN:
        raise RuntimeError(
            "Render Environment Variables bo‘limiga BOT_TOKEN kiriting."
        )

    setup()

    # Render Web Service uchun portni ishga tushirish
    threading.Thread(
        target=start_web_server,
        daemon=True,
    ).start()

    # Python 3.14 uchun event loop yaratish
    asyncio.set_event_loop(asyncio.new_event_loop())

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CallbackQueryHandler(buttons))

    print("KartaHisobBot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
