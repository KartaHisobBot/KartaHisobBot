
import os
import sqlite3
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

TOKEN = os.environ.get("BOT_TOKEN")
DB_PATH = os.environ.get("DB_PATH", "kartahisob.db")
LIMIT = 24
MAX_PLAYERS = 6


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
        )""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            score INTEGER DEFAULT 0,
            out INTEGER DEFAULT 0,
            UNIQUE(game_id, user_id)
        )""")
        con.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id INTEGER,
            player_id INTEGER,
            amount INTEGER,
            by_user INTEGER,
            created_at TEXT
        )""")


def active_game(chat_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM games WHERE chat_id=? AND status='active' "
            "ORDER BY id DESC LIMIT 1", (chat_id,)
        ).fetchone()


def get_players(game_id):
    with connect() as con:
        return con.execute(
            "SELECT * FROM players WHERE game_id=? ORDER BY id", (game_id,)
        ).fetchall()


def make_board(chat_id):
    game = active_game(chat_id)
    if not game:
        return "Hozir faol o‘yin yo‘q. /newgame yuboring.", None

    players = get_players(game["id"])
    lines = [f"🃏 KartaHisobBot — O‘yin #{game['id']}", ""]

    for p in players:
        if p["id"] == game["champion_id"]:
            lines.append(f"🏆 {p['name']} — {p['score']} ball · CHEMPION")
        elif p["out"] or p["score"] >= LIMIT:
            lines.append(f"🔴 {p['name']} — {p['score']} ball · SABIN")
        else:
            lines.append(f"🟢 {p['name']} — {p['score']} ball")

    if not players:
        lines.append("O‘yinchilar /join yuborishsin.")

    active = [
        p for p in players
        if not p["out"] and p["score"] < LIMIT
    ]

    if game["status"] == "finished":
        lines.extend(["", "🏁 O‘yin yakunlandi!"])
        if game["champion_id"]:
            lines.append("Tabriklaymiz, CHEMPION! 🏆")
    elif len(players) >= 2 and len(active) == 1:
        lines.extend(["", "G‘olib aniqlanmoqda..."])

    rows = []
    if game["status"] == "active":
        for p in active:
            rows.append([InlineKeyboardButton(
                f"➕ {p['name']}ga ball",
                callback_data=f"pick:{p['id']}"
            )])

    rows.append([InlineKeyboardButton(
        "🔄 Hisobni yangilash", callback_data="refresh"
    )])

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Salom! Men KartaHisobBotman 🃏\n\n"
        "/newgame — yangi o‘yin\n"
        "/join — o‘yinga qo‘shilish\n"
        "/score — hisobni ko‘rish\n"
        "/history — o‘yinlar tarixi"
    )


async def newgame(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with connect() as con:
        con.execute(
            "UPDATE games SET status='finished' "
            "WHERE chat_id=? AND status='active'",
            (update.effective_chat.id,)
        )
        cur = con.execute(
            "INSERT INTO games(chat_id,status,created_at) VALUES(?,?,?)",
            (update.effective_chat.id, "active",
             datetime.now().isoformat(timespec="seconds"))
        )
        game_id = cur.lastrowid

    await update.message.reply_text(
        f"🃏 Yangi o‘yin #{game_id} boshlandi!\n"
        "Har bir o‘yinchi /join yuborsin. Eng ko‘pi 6 kishi."
    )


async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    game = active_game(chat_id)

    if not game:
        await update.message.reply_text("Avval /newgame yuboring.")
        return

    with connect() as con:
        existing = con.execute(
            "SELECT id FROM players WHERE game_id=? AND user_id=?",
            (game["id"], user.id)
        ).fetchone()

        if existing:
            await update.message.reply_text("Siz allaqachon o‘yindasiz.")
            return

        count = con.execute(
            "SELECT COUNT(*) AS n FROM players WHERE game_id=?",
            (game["id"],)
        ).fetchone()["n"]

        if count >= MAX_PLAYERS:
            await update.message.reply_text("O‘yinda 6 kishi bor.")
            return

        con.execute(
            "INSERT INTO players(game_id,user_id,name) VALUES(?,?,?)",
            (game["id"], user.id, user.full_name or user.first_name)
        )

    text, markup = make_board(chat_id)
    await update.message.reply_text(text, reply_markup=markup)


async def score(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text, markup = make_board(update.effective_chat.id)
    await update.message.reply_text(text, reply_markup=markup)


async def history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    with connect() as con:
        games = con.execute(
            "SELECT * FROM games WHERE chat_id=? ORDER BY id DESC LIMIT 10",
            (chat_id,)
        ).fetchall()

        if not games:
            await update.message.reply_text("Hali o‘yinlar tarixi yo‘q.")
            return

        lines = ["📚 O‘yinlar tarixi:"]
        for game in games:
            players = con.execute(
                "SELECT * FROM players WHERE game_id=? ORDER BY score DESC",
                (game["id"],)
            ).fetchall()
            lines.append(f"\nO‘yin #{game['id']} — {game['status']}")
            for p in players:
                if p["id"] == game["champion_id"]:
                    tag = " 🏆 CHEMPION"
                elif p["out"] or p["score"] >= LIMIT:
                    tag = " · SABIN"
                else:
                    tag = ""
                lines.append(f"• {p['name']}: {p['score']} ball{tag}")

    await update.message.reply_text("\n".join(lines))


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    chat_id = q.message.chat_id

    if q.data == "refresh":
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
                "SELECT * FROM players WHERE id=? AND game_id=?",
                (player_id, game["id"])
            ).fetchone()

        if not p or p["out"] or p["score"] >= LIMIT:
            await q.answer("Bu o‘yinchi SABIN bo‘lgan.", show_alert=True)
            return

        amounts = [1, 2, 3, 5, 10]
        rows = [[InlineKeyboardButton(
            f"+{n}", callback_data=f"add:{player_id}:{n}"
        ) for n in amounts[:3]]]
        rows.append([InlineKeyboardButton(
            f"+{n}", callback_data=f"add:{player_id}:{n}"
        ) for n in amounts[3:]])

        await q.message.reply_text(
            f"{p['name']}ga nechta ball qo‘shamiz?",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    if q.data.startswith("add:"):
        _, pid, amount = q.data.split(":")
        pid, amount = int(pid), int(amount)
        game = active_game(chat_id)

        if not game:
            await q.answer("Faol o‘yin yo‘q.", show_alert=True)
            return

        with connect() as con:
            p = con.execute(
                "SELECT * FROM players WHERE id=? AND game_id=?",
                (pid, game["id"])
            ).fetchone()

            if not p or p["out"] or p["score"] >= LIMIT:
                await q.answer("Bu o‘yinchi o‘yindan chiqqan.", show_alert=True)
                return

            new_score = p["score"] + amount
            is_out = int(new_score >= LIMIT)

            con.execute(
                "UPDATE players SET score=?, out=? WHERE id=?",
                (new_score, is_out, pid)
            )
            con.execute(
                "INSERT INTO events(game_id,player_id,amount,by_user,created_at) "
                "VALUES(?,?,?,?,?)",
                (game["id"], pid, amount, q.from_user.id,
                 datetime.now().isoformat(timespec="seconds"))
            )

            remaining = con.execute(
                "SELECT * FROM players WHERE game_id=? AND out=0 AND score<?",
                (game["id"], LIMIT)
            ).fetchall()

            total = con.execute(
                "SELECT COUNT(*) AS n FROM players WHERE game_id=?",
                (game["id"],)
            ).fetchone()["n"]

            if total >= 2 and len(remaining) == 1:
                con.execute(
                    "UPDATE games SET status='finished', champion_id=? WHERE id=?",
                    (remaining[0]["id"], game["id"])
                )
            elif len(remaining) == 0:
                con.execute(
                    "UPDATE games SET status='finished' WHERE id=?",
                    (game["id"],)
                )

        text, markup = make_board(chat_id)
        await q.message.reply_text(text, reply_markup=markup)


def main():
    if not TOKEN:
        raise RuntimeError("Hosting sozlamalarida BOT_TOKEN kiritilmagan.")

    setup()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("newgame", newgame))
    app.add_handler(CommandHandler("join", join))
    app.add_handler(CommandHandler("score", score))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CallbackQueryHandler(buttons))
    app.run_polling()


if __name__ == "__main__":
    main()
          
