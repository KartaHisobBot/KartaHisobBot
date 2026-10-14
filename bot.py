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
DB_NAME = "karta_hisob.db"
MAX_PLAYERS = 6
OUT_SCORE = 24


# Render uchun HTTP server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("KartaHisobBot ishlayapti!".encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    server.serve_forever()


# Ma'lumotlar bazasi
def setup():
    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                started TEXT NOT NULL,
                finished TEXT,
                champion TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS players (
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (game_id, user_id)
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                points INTEGER NOT NULL,
                added_by TEXT NOT NULL,
                created TEXT NOT NULL
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS current_games (
                chat_id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL
            )
        """)


def db_execute(query, params=()):
    with sqlite3.connect(DB_NAME) as con:
        return con.execute(query, params)


def get_current_game(chat_id):
    row = db_execute(
        "SELECT game_id FROM current_games WHERE chat_id = ?",
        (chat_id,),
    ).fetchone()
    return row[0] if row else None


def get_players(game_id):
    return db_execute(
        """
        SELECT user_id, name, score, active
        FROM players
        WHERE game_id = ?
        ORDER BY score, name
        """,
        (game_id,),
    ).fetchall()


def make_keyboard(game_id):
    players = get_players(game_id)
    rows = []

    for user_id, name, score, active in players:
        if active:
            rows.append([
                InlineKeyboardButton(
                    f"➕ {name} ({score})",
                    callback_data=f"add:{game_id}:{user_id}",
                )
            ])

    rows.append([
        InlineKeyboardButton("🔄 Yangilash", callback_data=f"refresh:{game_id}")
    ])

    return InlineKeyboardMarkup(rows)


def make_score_text(game_id):
    players = get_players(game_id)
    lines = ["🏆 KARTA HISOBI", ""]

    active_players = []
    out_players = []

    for user_id, name, score, active in players:
        if active:
            active_players.append((name, score))
        else:
            out_players.append((name, score))

    if active_players:
        lines.append("🟢 O‘yindagilar:")
        for name, score in active_players:
            lines.append(f"• {name}: {score} ball")

    if out_players:
        lines.append("")
        lines.append("❌ SABIN:")
        for name, score in out_players:
            lines.append(f"• {name}: {score} ball")

    if len(active_players) == 1 and len(players) >= 2:
        lines.append("")
        lines.append(f"🎉 CHEMPION 🏆: {active_players[0][0]}")

    lines.append("")
    lines.append("Ball qo‘shish uchun pastdagi tugmani bosing.")
    return "\n".join(lines)


async def show_score(chat_id, game_id, context):
    text = make_score_text(game_id)
    keyboard = make_keyboard(game_id)

    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=keyboard,
    )


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 KartaHisobBot'ga xush kelibsiz!\n\n"
        "Buyruqlar:\n"
        "/newgame — yangi o‘yin boshlash\n"
        "/join — o‘yinga qo‘shilish\n"
        "/score — hisobni ko‘rish\n"
        "/history — o‘yinlar tarixini ko‘rish\n\n"
        "Botni guruhga qo‘shib, buyruqlarni guruhda ishlating."
    )
    await update.effective_message.reply_text(text)


# /newgame
async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_NAME) as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO games (chat_id, started) VALUES (?, ?)",
            (chat_id, now),
        )
        game_id = cur.lastrowid

        cur.execute(
            """
            INSERT OR REPLACE INTO current_games (chat_id, game_id)
            VALUES (?, ?)
            """,
            (chat_id, game_id),
        )

    user = update.effective_user
    name = user.full_name or user.first_name or str(user.id)

    with sqlite3.connect(DB_NAME) as con:
        con.execute(
            """
            INSERT OR IGNORE INTO players (game_id, user_id, name, score, active)
            VALUES (?, ?, ?, 0, 1)
            """,
            (game_id, user.id, name),
        )

    await update.effective_message.reply_text(
        "🃏 Yangi o‘yin boshlandi!\n"
        f"Boshlovchi: {name}\n\n"
        "Boshqa o‘yinchilar /join buyrug‘i bilan qo‘shilsin."
    )
    await show_score(chat_id, game_id, context)


# /join
async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game_id = get_current_game(chat_id)

    if not game_id:
        await update.effective_message.reply_text(
            "Avval /newgame buyrug‘i bilan yangi o‘yin boshlang."
        )
        return

    user = update.effective_user
    name = user.full_name or user.first_name or str(user.id)

    players = get_players(game_id)

    if any(p[0] == user.id for p in players):
        await update.effective_message.reply_text(
            f"{name}, siz allaqachon o‘yindasiz."
        )
        return

    if len(players) >= MAX_PLAYERS:
        await update.effective_message.reply_text(
            "O‘yinda eng ko‘pi bilan 6 kishi qatnashadi."
        )
        return

    with sqlite3.connect(DB_NAME) as con:
        con.execute(
            """
            INSERT INTO players (game_id, user_id, name, score, active)
            VALUES (?, ?, ?, 0, 1)
            """,
            (game_id, user.id, name),
        )

    await update.effective_message.reply_text(
        f"✅ {name} o‘yinga qo‘shildi!"
    )
    await show_score(chat_id, game_id, context)


# /score
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game_id = get_current_game(chat_id)

    if not game_id:
        await update.effective_message.reply_text(
            "Hozir faol o‘yin yo‘q. /newgame bilan boshlang."
        )
        return

    await show_score(chat_id, game_id, context)


# /history
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    rows = db_execute(
        """
        SELECT id, started, finished, champion
        FROM games
        WHERE chat_id = ?
        ORDER BY id DESC
        LIMIT 10
        """,
        (chat_id,),
    ).fetchall()

    if not rows:
        await update.effective_message.reply_text(
            "Hozircha o‘yinlar tarixi yo‘q."
        )
        return

    lines = ["📚 O‘YINLAR TARIXI", ""]

    for game_id, started, finished, champion in rows:
        status = f"Chempion: {champion}" if champion else "Yakunlanmagan"
        lines.append(f"🃏 O‘yin #{game_id}")
        lines.append(f"Boshlangan: {started}")
        if finished:
            lines.append(f"Tugagan: {finished}")
        lines.append(status)
        lines.append("")

    await update.effective_message.reply_text("\n".join(lines))


# Tugmalar
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[0]
    game_id = int(data[1])

    chat_id = query.message.chat_id
    current_game = get_current_game(chat_id)

    if current_game != game_id:
        await query.message.reply_text(
            "Bu eski o‘yinning hisobidir. Yangi hisobni /score orqali ko‘ring."
        )
        return

    if action == "refresh":
        await query.message.reply_text(
            make_score_text(game_id),
            reply_markup=make_keyboard(game_id),
        )
        return

    if action != "add":
        return

    target_id = int(data[2])
    players = get_players(game_id)

    target = next((p for p in players if p[0] == target_id), None)
    if not target:
        await query.message.reply_text("O‘yinchi topilmadi.")
        return

    user_id, name, score_value, active = target

    if not active:
        await query.message.reply_text(
            f"{name} allaqachon SABIN bo‘lgan."
        )
        return

    new_score = score_value + 1
    is_out = new_score >= OUT_SCORE

    with sqlite3.connect(DB_NAME) as con:
        con.execute(
            """
            UPDATE players
            SET score = ?, active = ?
            WHERE game_id = ? AND user_id = ?
            """,
            (new_score, 0 if is_out else 1, game_id, target_id),
        )

        con.execute(
            """
            INSERT INTO history
            (game_id, player_name, points, added_by, created)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                game_id,
                name,
                1,
                query.from_user.full_name,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    updated_players = get_players(game_id)
    active_players = [p for p in updated_players if p[3] == 1]

    if is_out:
        await query.message.reply_text(
            f"❌ SABIN! {name} {new_score} ballga yetdi."
        )

    if len(updated_players) >= 2 and len(active_players) == 1:
        champion = active_players[0][1]
        finished = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB_NAME) as con:
            con.execute(
                "UPDATE games SET finished = ?, champion = ? WHERE id = ?",
                (finished, champion, game_id),
            )

        await query.message.reply_text(
            f"🎉 CHEMPION 🏆: {champion}!"
        )

    await show_score(chat_id, game_id, context)


def main():
    if not TOKEN:
        raise RuntimeError(
            "Render Environment Variables bo‘limiga BOT_TOKEN kiriting."
        )

    setup()

    threading.Thread(
        target=start_web_server,
        daemon=True,
    ).start()

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
