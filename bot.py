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
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_NAME = "karta_hisob.db"
MAX_PLAYERS = 6
OUT_SCORE = 24


# Render uchun web server
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("KartaHisobBot ishlayapti!".encode("utf-8"))

    def log_message(self, format, *args):
        pass


def start_web_server():
    port = int(os.environ.get("PORT", "10000"))
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()


# Ma'lumotlar bazasi
def setup():
    with sqlite3.connect(DB_NAME) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                started TEXT NOT NULL,
                finished TEXT,
                champion TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS players (
                game_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (game_id, user_id)
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                points INTEGER NOT NULL,
                added_by TEXT NOT NULL,
                created TEXT NOT NULL
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS current_games (
                chat_id INTEGER PRIMARY KEY,
                game_id INTEGER NOT NULL
            )
        """)


def current_game(chat_id):
    with sqlite3.connect(DB_NAME) as con:
        row = con.execute(
            "SELECT game_id FROM current_games WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()
    return row[0] if row else None


def get_players(game_id):
    with sqlite3.connect(DB_NAME) as con:
        return con.execute("""
            SELECT user_id, name, score, active
            FROM players
            WHERE game_id = ?
            ORDER BY rowid
        """, (game_id,)).fetchall()


def score_text(game_id):
    players = get_players(game_id)
    lines = [f"🃏 KARTA HISOBI — O‘yin #{game_id}", ""]

    active = [p for p in players if p[3] == 1]
    out = [p for p in players if p[3] == 0]

    for _, name, score, _ in players:
        if score >= OUT_SCORE:
            lines.append(f"❌ {name}: {score} ball — SABIN")
        else:
            lines.append(f"🟢 {name}: {score}/{OUT_SCORE} ball")

    lines.append("")
    lines.append(f"O‘yinda qolganlar: {len(active)}")

    if len(players) >= 2 and len(active) == 1:
        lines.append(f"\n🏆 CHEMPION: {active[0][1]}!")

    return "\n".join(lines)


def game_keyboard(game_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "✍️ Raund ballarini kiritish",
            callback_data=f"round:{game_id}",
        )],
        [InlineKeyboardButton(
            "🔄 Hisobni yangilash",
            callback_data=f"refresh:{game_id}",
        )],
        [InlineKeyboardButton(
            "📚 O‘yinlar tarixi",
            callback_data="history",
        )],
    ])


async def show_score(chat_id, game_id, context):
    await context.bot.send_message(
        chat_id=chat_id,
        text=score_text(game_id),
        reply_markup=game_keyboard(game_id),
    )


# /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "👋 KartaHisobBot!\n\n"
        "Buyruqlar:\n"
        "/newgame Ali, Vali, Sardor — yangi o‘yin\n"
        "/score — hisobni ko‘rish\n"
        "/history — o‘yinlar tarixi\n\n"
        "Raundda ballarni bitta xabarda kiritasiz.\n"
        "Masalan: 4 6 2"
    )


# /newgame Ali, Vali, Sardor
async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.effective_message.text or ""
    names_text = text.partition(" ")[2].strip()

    if not names_text:
        await update.effective_message.reply_text(
            "O‘yinchilar ismini vergul bilan yozing:\n\n"
            "/newgame Ali, Vali, Sardor, Jasur"
        )
        return

    names = [name.strip() for name in names_text.split(",") if name.strip()]

    if not 2 <= len(names) <= MAX_PLAYERS:
        await update.effective_message.reply_text(
            "O‘yinchilar soni 2 tadan 6 tagacha bo‘lishi kerak."
        )
        return

    if len({name.casefold() for name in names}) != len(names):
        await update.effective_message.reply_text(
            "Bir xil ism ikki marta yozilgan. Ismlarni tekshiring."
        )
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(DB_NAME) as con:
        cur = con.execute(
            "INSERT INTO games (chat_id, started) VALUES (?, ?)",
            (chat_id, now),
        )
        game_id = cur.lastrowid

        con.execute("""
            INSERT OR REPLACE INTO current_games (chat_id, game_id)
            VALUES (?, ?)
        """, (chat_id, game_id))

        for index, name in enumerate(names, start=1):
            con.execute("""
                INSERT INTO players
                (game_id, user_id, name, score, active)
                VALUES (?, ?, ?, 0, 1)
            """, (game_id, -index, name))

    await update.effective_message.reply_text(
        "✅ Yangi o‘yin boshlandi!\n"
        "O‘yinchilar: " + ", ".join(names)
    )
    await show_score(chat_id, game_id, context)


# /score
async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game_id = current_game(chat_id)

    if not game_id:
        await update.effective_message.reply_text(
            "Faol o‘yin yo‘q. /newgame bilan boshlang."
        )
        return

    await show_score(chat_id, game_id, context)


# Raund ballarini qabul qilish
async def receive_scores(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    pending = context.user_data.get("pending_round")

    if not pending:
        return

    if update.effective_chat.id != pending["chat_id"]:
        return

    text = (update.effective_message.text or "").strip()

    try:
        scores = [int(value) for value in text.replace(",", " ").split()]
    except ValueError:
        await update.effective_message.reply_text(
            "Faqat raqamlarni yozing. Masalan: 4 6 2 0"
        )
        return

    players = get_players(pending["game_id"])
    active_players = [p for p in players if p[3] == 1]

    if len(scores) != len(active_players):
        await update.effective_message.reply_text(
            f"Hozir {len(active_players)} ta o‘yinchi faol.\n"
            f"Aynan {len(active_players)} ta raqam kiriting.\n"
            "Masalan: 4 6 2 0"
        )
        return

    if any(value < 0 for value in scores):
        await update.effective_message.reply_text(
            "Ball manfiy bo‘lishi mumkin emas. Qayta kiriting."
        )
        return

    pending["scores"] = scores
    pending["players"] = [
        [p[0], p[1], p[2], p[3]] for p in active_players
    ]

    lines = ["📝 Raundni tekshiring:", ""]
    for player, points in zip(active_players, scores):
        lines.append(
            f"• {player[1]}: {points} ball "
            f"(jami {player[2] + points})"
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Raundni saqlash",
                callback_data=f"save:{pending['game_id']}",
            ),
            InlineKeyboardButton(
                "❌ Bekor qilish",
                callback_data=f"cancel:{pending['game_id']}",
            ),
        ]
    ])

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
    )


# /history
async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with sqlite3.connect(DB_NAME) as con:
        rows = con.execute("""
            SELECT id, started, finished, champion
            FROM games
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (chat_id,)).fetchall()

    if not rows:
        await update.effective_message.reply_text(
            "Hozircha o‘yinlar tarixi yo‘q."
        )
        return

    lines = ["📚 O‘YINLAR TARIXI", ""]

    for game_id, started, finished, champion in rows:
        lines.append(f"🃏 O‘yin #{game_id} — {started}")
        if champion:
            lines.append(f"🏆 Chempion: {champion}")
        elif finished:
            lines.append("O‘yin yakunlangan.")
        else:
            lines.append("O‘yin yakunlanmagan.")
        lines.append("")

    await update.effective_message.reply_text("\n".join(lines))


# Tugmalar
async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split(":")
    action = data[0]

    if action == "history":
        await history_from_button(query)
        return

    game_id = int(data[1])
    chat_id = query.message.chat_id

    if current_game(chat_id) != game_id:
        await query.message.reply_text(
            "Bu eski o‘yin. Joriy hisob uchun /score yuboring."
        )
        return

    if action == "refresh":
        await query.message.reply_text(
            score_text(game_id),
            reply_markup=game_keyboard(game_id),
        )
        return

    if action == "round":
        players = [p for p in get_players(game_id) if p[3] == 1]

        if len(players) < 2:
            await query.message.reply_text(
                "Raund kiritish uchun kamida 2 faol o‘yinchi kerak."
            )
            return

        context.user_data["pending_round"] = {
            "game_id": game_id,
            "chat_id": chat_id,
        }

        lines = [
            "✍️ Har bir faol o‘yinchining shu raunddagi ballini yozing.",
            "",
        ]
        for index, player in enumerate(players, start=1):
            lines.append(f"{index}. {player[1]}")

        lines.extend([
            "",
            f"{len(players)} ta raqamni shu tartibda yuboring.",
            "Masalan: 4 6 2 0",
            "",
            "Hozircha ballar saqlanmaydi. Avval tekshirib olasiz.",
        ])

        await query.message.reply_text("\n".join(lines))
        return

    if action == "cancel":
        context.user_data.pop("pending_round", None)
        await query.message.reply_text("Raund bekor qilindi.")
        return

    if action == "save":
        pending = context.user_data.get("pending_round")

        if not pending or pending["game_id"] != game_id:
            await query.message.reply_text(
                "Saqlanadigan raund topilmadi. Qaytadan boshlang."
            )
            return

        if current_game(chat_id) != game_id:
            context.user_data.pop("pending_round", None)
            await query.message.reply_text(
                "Bu o‘yin endi faol emas. Raund saqlanmadi."
            )
            return

        scores = pending["scores"]
        players = pending["players"]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with sqlite3.connect(DB_NAME) as con:
            for player, points in zip(players, scores):
                user_id, name, old_score, _ = player
                new_score = old_score + points
                active = 0 if new_score >= OUT_SCORE else 1

                con.execute("""
                    UPDATE players
                    SET score = ?, active = ?
                    WHERE game_id = ? AND user_id = ?
                """, (new_score, active, game_id, user_id))

                con.execute("""
                    INSERT INTO history
                    (game_id, player_name, points, added_by, created)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    game_id,
                    name,
                    points,
                    query.from_user.full_name,
                    now,
                ))

            updated = con.execute("""
                SELECT name, score, active
                FROM players
                WHERE game_id = ?
            """, (game_id,)).fetchall()

            active_players = [p for p in updated if p[2] == 1]
            champion = None

            if len(updated) >= 2 and len(active_players) == 1:
                champion = active_players[0][0]
                con.execute("""
                    UPDATE games
                    SET finished = ?, champion = ?
                    WHERE id = ?
                """, (now, champion, game_id))

        context.user_data.pop("pending_round", None)

        out_names = [
            p[0] for p in updated if p[1] >= OUT_SCORE
        ]

        if out_names:
            await query.message.reply_text(
                "❌ SABIN: " + ", ".join(out_names)
            )

        if champion:
            await query.message.reply_text(
                f"🎉 CHEMPION 🏆: {champion}!"
            )

        await show_score(chat_id, game_id, context)


async def history_from_button(query):
    chat_id = query.message.chat_id

    with sqlite3.connect(DB_NAME) as con:
        rows = con.execute("""
            SELECT id, started, champion
            FROM games
            WHERE chat_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (chat_id,)).fetchall()

    if not rows:
        await query.message.reply_text("Hozircha tarix yo‘q.")
        return

    lines = ["📚 O‘YINLAR TARIXI", ""]
    for game_id, started, champion in rows:
        lines.append(f"O‘yin #{game_id} — {started}")
        lines.append(f"Chempion: {champion}" if champion else "Yakunlanmagan")
        lines.append("")

    await query.message.reply_text("\n".join(lines))


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
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, receive_scores)
    )

    print("KartaHisobBot ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
