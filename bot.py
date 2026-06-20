import os
import io
import logging

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
FONT_PATH = os.path.join(FONTS_DIR, "PlayfairDisplay-Italic.ttf")

# если точное имя не нашлось — берём любой .ttf/.otf файл из папки fonts/
if not os.path.exists(FONT_PATH) and os.path.isdir(FONTS_DIR):
    candidates = [f for f in os.listdir(FONTS_DIR) if f.lower().endswith((".ttf", ".otf"))]
    if candidates:
        FONT_PATH = os.path.join(FONTS_DIR, candidates[0])

# --- внешний вид вотермарки ---
FONT_SIZE_RATIO = 0.07
OPACITY = 170
SHADOW_OPACITY = 90
ROTATION_DEGREES = -30
TILE_SPACING_RATIO = 0.55

# --- состояния диалога ---
WAITING_TEXT, WAITING_PHOTO = range(2)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────
#  Логика наложения водяного знака
# ──────────────────────────────────────────────────────────

def apply_text_watermark(base_image_bytes: bytes, text: str) -> io.BytesIO:
    """Накладывает текст элегантным шрифтом по диагонали по всему фото."""
    base = Image.open(io.BytesIO(base_image_bytes)).convert("RGBA")
    w, h = base.size

    font_size = max(int(min(w, h) * FONT_SIZE_RATIO), 18)
    font = ImageFont.truetype(FONT_PATH, font_size)

    diag = int((w ** 2 + h ** 2) ** 0.5)
    tile_layer = Image.new("RGBA", (diag, diag), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile_layer)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    step_x = int(text_w + text_w * TILE_SPACING_RATIO)
    step_y = int(text_h + text_h * TILE_SPACING_RATIO * 4)

    y = 0
    row = 0
    while y < diag:
        offset_x = (step_x // 2) if row % 2 else 0
        x = -step_x + offset_x
        while x < diag:
            draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, SHADOW_OPACITY))
            draw.text((x, y), text, font=font, fill=(255, 255, 255, OPACITY))
            x += step_x
        y += step_y
        row += 1

    tile_layer = tile_layer.rotate(ROTATION_DEGREES, expand=False)

    left = (diag - w) // 2
    top = (diag - h) // 2
    pattern = tile_layer.crop((left, top, left + w, top + h))

    result = Image.alpha_composite(base, pattern).convert("RGB")
    buf = io.BytesIO()
    buf.name = "watermarked.jpg"
    result.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf


# ──────────────────────────────────────────────────────────
#  Вспомогательные клавиатуры
# ──────────────────────────────────────────────────────────

def after_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📸 Ещё фото с этим текстом", callback_data="more_photo")],
            [InlineKeyboardButton("✏️ Изменить текст", callback_data="change_text")],
        ]
    )


# ──────────────────────────────────────────────────────────
#  Хендлеры диалога
# ──────────────────────────────────────────────────────────

WELCOME = (
    "👋 *Привет! Я наношу водяной знак на твои фото.*\n\n"
    "Делаю это в два простых шага:\n"
    "1️⃣ Ты присылаешь текст — это и будет водяной знак\n"
    "2️⃣ Присылаешь фото — я аккуратно нанесу на него этот текст красивым шрифтом\n\n"
    "✍️ Для начала — *напиши, какой текст нанести* на фото:"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(WELCOME, parse_mode=ParseMode.MARKDOWN)
    return WAITING_TEXT


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ *Как пользоваться:*\n\n"
        "• /start — начать заново и задать текст водяного знака\n"
        "• Пришли текст → затем фото — получишь фото с водяным знаком\n"
        "• После результата можно сразу прислать ещё фото с тем же текстом\n"
        "• /cancel — отменить текущее действие\n",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("watermark_text", None)
    await update.message.reply_text(
        "❌ Окей, отменил. Когда будешь готов — жми /start."
    )
    return ConversationHandler.END


async def receive_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text:
        await update.message.reply_text("Пришли, пожалуйста, непустой текст 🙂")
        return WAITING_TEXT

    context.user_data["watermark_text"] = text
    await update.message.reply_text(
        f"✅ Отлично, водяной знак: «*{text}*»\n\n"
        "📸 Теперь пришли фото — и я нанесу на него этот текст.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return WAITING_PHOTO


async def wrong_input_in_photo_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "📸 Жду от тебя фото. Если хочешь сменить текст водяного знака — нажми "
        "«✏️ Изменить текст» под предыдущим результатом, либо отправь /start заново."
    )
    return WAITING_PHOTO


async def _process_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, file_bytes: bytes) -> int:
    text = context.user_data.get("watermark_text")
    if not text:
        await update.message.reply_text(
            "Сначала пришли текст для водяного знака — или нажми /start."
        )
        return WAITING_TEXT

    chat_id = update.effective_chat.id
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_PHOTO)
    status_msg = await update.message.reply_text("⏳ Наношу водяной знак…")

    try:
        result_buf = apply_text_watermark(file_bytes, text)
    except Exception as e:
        logger.exception("Ошибка при наложении вотермарки")
        await status_msg.edit_text(f"⚠️ Не получилось обработать фото: {e}")
        return WAITING_PHOTO

    await status_msg.delete()
    await update.message.reply_photo(
        photo=result_buf,
        caption=f"✨ Готово! Текст: «{text}»",
        reply_markup=after_result_keyboard(),
    )
    return WAITING_PHOTO


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo[-1]
    file = await photo.get_file()
    file_bytes = bytes(await file.download_as_bytearray())
    return await _process_and_reply(update, context, file_bytes)


async def handle_document_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text("Это не похоже на изображение 🤔 Пришли, пожалуйста, фото.")
        return WAITING_PHOTO
    file = await doc.get_file()
    file_bytes = bytes(await file.download_as_bytearray())
    return await _process_and_reply(update, context, file_bytes)


async def on_more_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    text = context.user_data.get("watermark_text", "—")
    await query.message.reply_text(
        f"📸 Жду следующее фото — нанесу на него текст «{text}»."
    )
    return WAITING_PHOTO


async def on_change_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.message.reply_text("✍️ Пришли новый текст для водяного знака:")
    return WAITING_TEXT


async def fallback_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Не совсем понял 🙂 Нажми /start, чтобы начать заново."
    )
    return ConversationHandler.END


# ──────────────────────────────────────────────────────────
#  Запуск
# ──────────────────────────────────────────────────────────

async def post_init(application: Application) -> None:
    await application.bot.set_my_commands(
        [
            BotCommand("start", "🚀 Начать / задать текст водяного знака"),
            BotCommand("help", "ℹ️ Как пользоваться ботом"),
            BotCommand("cancel", "❌ Отменить текущее действие"),
        ]
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в .env")

    if not os.path.exists(FONT_PATH):
        raise RuntimeError(
            f"Не найден файл шрифта: {FONT_PATH}\n"
            f"Проверь, что папка '{FONTS_DIR}' существует и содержит "
            ".ttf/.otf файл шрифта."
        )

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            WAITING_TEXT: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_text),
            ],
            WAITING_PHOTO: [
                CommandHandler("cancel", cancel),
                MessageHandler(filters.PHOTO, handle_photo),
                MessageHandler(filters.Document.IMAGE, handle_document_photo),
                CallbackQueryHandler(on_more_photo, pattern="^more_photo$"),
                CallbackQueryHandler(on_change_text, pattern="^change_text$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, wrong_input_in_photo_state),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), MessageHandler(filters.ALL, fallback_message)],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", help_command))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
