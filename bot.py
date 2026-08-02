"""Telegram bot: receives a food photo and replies with a calorie estimate.

Run:  python bot.py
"""

from __future__ import annotations

import io
import logging
import os
from datetime import date, timedelta

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

import ai_vision
import diary
import nufacts

load_dotenv()

log = logging.getLogger("caloriebot")

# Keys for the inline buttons after a photo is analysed.
BTN_KEEP = "keep"
BTN_DELETE = "delete_last"


def _setup_logging() -> None:
    """Log to both console and a file (bot.log) so the bot works well on a VPS."""
    log_file = os.path.join(os.path.dirname(__file__), "bot.log")
    handlers = [
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


async def start(update: Update, _ctx) -> None:
    await update.message.reply_text(
        "Привет! 👋 Пришли мне фото еды — я оценю калории и КБЖУ.\n\n"
        "Это оценка по фото, а не точный расчёт."
    )


async def help_cmd(update: Update, _ctx) -> None:
    await start(update, _ctx)


async def handle_photo(update: Update, ctx) -> None:
    msg = update.message
    # Take the largest available size for best recognition.
    photo = max(msg.photo, key=lambda p: p.width * p.height)
    file = await ctx.bot.get_file(photo.file_id)
    data = io.BytesIO()
    await file.download_to_memory(data)
    data = data.getvalue()

    sent = await msg.reply_text("🔍 Анализирую фото…")

    try:
        result = ai_vision.analyze_photo(data)
        text = ai_vision.summarize(result)
        # Save to the diary if something was recognised.
        dishes = result.get("dishes") or []
        if dishes:
            diary.add_meal(
                user_id=update.effective_user.id,
                dishes=dishes,
                note=result.get("note"),
            )
        # Cross-check each dish's macros against OpenFoodFacts and append a
        # real-food reference line when a match is found.
        reference = _openfoodfacts_reference(dishes)
        if reference:
            text += "\n\n" + reference
        # Markdown V1, and the summary already uses *bold* / _italic_.
        reply_markup = _confirm_buttons()
        await sent.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    except Exception as exc:  # noqa: BLE001 - show user a friendly error
        log.exception("analysis failed")
        err = "Не удалось распознать еду. Попробуй другое фото или более чёткий кадр."
        await sent.edit_text(f"{err}\n(Технически: {exc})")


def _confirm_buttons() -> InlineKeyboardMarkup:
    """Inline buttons shown under a calorie estimate."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Верно", callback_data=BTN_KEEP),
                InlineKeyboardButton("🗑 Это не еда", callback_data=BTN_DELETE),
            ]
        ]
    )


async def _confirm_callback(update: Update, ctx) -> None:
    """Handle presses on the confirm/delete buttons under a calorie estimate."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    if data == BTN_DELETE:
        removed = diary.delete_last(update.effective_user.id)
        # The estimate is gone either way; remove the buttons.
        await query.edit_message_text(
            "🗑 Запись удалена из дневника."
            if removed
            else "Удалять нечего — записей пока нет."
        )
        if removed:
            log.info("user %s deleted meal of %s kcal", update.effective_user.id, removed["kcal"])
    else:  # BTN_KEEP - just collapse the buttons
        base = query.message.text or ""
        await query.edit_message_text(base, parse_mode=ParseMode.MARKDOWN)


def _openfoodfacts_reference(dishes: list[dict]) -> str:
    """Look up each dish on OpenFoodFacts and build a real-macro reference block.

    Returns an empty string if nothing matches, so callers can safely skip.
    """
    lines = ["🔎 *Сверка с базой OpenFoodFacts:*", ""]
    found = False
    for d in dishes:
        grams = round(float(d.get("grams", 0)))
        macro = nufacts.lookup(d.get("name", ""), grams)
        if macro is None:
            continue
        found = True
        name = ai_vision.escape_md(macro["name"])
        lines.append(
            f"• _{name}_ — {macro['kcal']} ккал, БЖУ "
            f"{macro['protein_g']}/{macro['fat_g']}/{macro['carbs_g']}"
        )
    return "\n".join(lines) if found else ""


async def today_cmd(update: Update, _ctx) -> None:
    total = diary.today_total(update.effective_user.id)
    await update.message.reply_text(f"📅 Калорий за сегодня: *{total} ккал*", parse_mode=ParseMode.MARKDOWN)


async def diary_cmd(update: Update, _ctx) -> None:
    user_id = update.effective_user.id
    today = diary.today_total(user_id)
    week = diary.week_total(user_id)
    target = diary.get_target(user_id)
    recent = diary.recent(user_id, 5)

    lines = [
        f"📔 *Дневник:*",
        f"Сегодня: *{today} ккал* · 7 дней: *{week} ккал*",
    ]
    if target:
        left = target - today
        arrow = "✅ в норме" if left >= 0 else "❌ превышено"
        lines.append(f"Цель: *{target} ккал* → осталось {left} ккал ({arrow})")
    lines.append("")
    if not recent:
        lines += ["Пока нет записей. Пришли фото еды!"]
    else:
        lines += ["*Последние приёмы:*", ""]
        for m in recent:
            when = m["created_at"].replace("T", " ")[:16]
            lines.append(f"• {when} — {m['total_kcal']} ккал")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def target_cmd(update: Update, ctx) -> None:
    """Set a daily kcal goal: /target 2000. With no number, /target 0 clears it."""
    args = ctx.args
    if not args:
        current = diary.get_target(update.effective_user.id)
        text = (
            f"Текущая цель: *{current} ккал* в день."
            if current
            else "Цель не задана. Пример: /target 2000"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        return

    raw = args[0].strip()
    if not raw.isdigit():
        await update.message.reply_text("Напиши число, например: /target 2000")
        return
    kcal = int(raw)
    if not kcal:
        diary.set_target(update.effective_user.id, 0)
        await update.message.reply_text("Цель удалена.")
        return
    if not (500 <= kcal <= 10000):
        await update.message.reply_text("Цель должна быть разумной (500–10000 ккал).")
        return
    diary.set_target(update.effective_user.id, kcal)
    await update.message.reply_text(f"🎯 Цель задана: *{kcal} ккал* в день.", parse_mode=ParseMode.MARKDOWN)


async def week_cmd(update: Update, _ctx) -> None:
    """Show last 7 days of kcal with a simple bar chart."""
    user_id = update.effective_user.id
    target = diary.get_target(user_id)
    totals = diary._day_totals(user_id, 7)
    labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    # The 7-day window starts 6 days before today (dates correspond to totals).
    lines = ["📊 *Последние 7 дней:*", ""]
    maxv = max(totals) if totals else 0
    width = 20
    start = date.today() - timedelta(days=6)
    for i, kcal in enumerate(totals):
        day_label = labels[(start + timedelta(days=i)).weekday()]
        bar = "█" * (round(kcal / maxv * width) if maxv else 0)
        lines.append(f"_{day_label}_ {kcal:>5} │{bar}")
    lines.append("")
    avg = round(sum(totals) / 7) if totals else 0
    lines.append(f"Среднее в день: *{avg} ккал*")
    lines.append(f"Всего за 7 дней: *{sum(totals)} ккал*")
    if target:
        lines.append(f"Цель: *{target} ккал/день*")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN", "")
    if not token or token == "replace_me":
        raise SystemExit(
            "TELEGRAM_TOKEN is not set. Create .env from .env.example and paste your "
            "token from @BotFather."
        )

    diary.init()
    _setup_logging()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("diary", diary_cmd))
    app.add_handler(CommandHandler("target", target_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CallbackQueryHandler(_confirm_callback, pattern=rf"^({BTN_KEEP}|{BTN_DELETE})$"))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    log.info("Calorie bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
