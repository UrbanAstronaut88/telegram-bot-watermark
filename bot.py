import os
import io
import json
import logging

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONTS_DIR = os.path.join(BASE_DIR, "fonts")
FONT_PATH = os.path.join(FONTS_DIR, "PlayfairDisplay-Italic.ttf")  # путь к шрифту

# если точное имя не нашлось — берём любой .ttf/.otf файл из папки fonts/,
# чтобы опечатка в имени файла не валила бота
if not os.path.exists(FONT_PATH) and os.path.isdir(FONTS_DIR):
    candidates = [
        f for f in os.listdir(FONTS_DIR) if f.lower().endswith((".ttf", ".otf"))
    ]
    if candidates:
        FONT_PATH = os.path.join(FONTS_DIR, candidates[0])

CONFIG_PATH = os.path.join(BASE_DIR, "watermark_config.json")
DEFAULT_TEXT = "My Studio"

# --- внешний вид вотермарки ---
FONT_SIZE_RATIO = 0.07     # размер шрифта = 7% от меньшей стороны фото
OPACITY = 170               # 0..255 — прозрачность текста
SHADOW_OPACITY = 90          # прозрачность тени под текстом
ROTATION_DEGREES = -30      # наклон надписи (по диагонали)
TILE_SPACING_RATIO = 0.55   # насколько часто повторять надпись по полю (доля от шрифта)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# user_id владельца, ожидающего ввод текста после /setwatermark
waiting_for_watermark_text: set[int] = set()


def load_watermark_text() -> str:
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("text", DEFAULT_TEXT)
        except Exception:
            return DEFAULT_TEXT
    return DEFAULT_TEXT


def save_watermark_text(text: str) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"text": text}, f, ensure_ascii=False)


def apply_text_watermark(base_image_bytes: bytes, text: str) -> io.BytesIO:
    """Накладывает текст элегантным шрифтом по диагонали по всему фото."""
    base = Image.open(io.BytesIO(base_image_bytes)).convert("RGBA")
    w, h = base.size

    font_size = max(int(min(w, h) * FONT_SIZE_RATIO), 18)
    font = ImageFont.truetype(FONT_PATH, font_size)

    # отдельный слой побольше диагонали, чтобы после поворота не было обрезов по краям
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
            draw.text(
                (x + 2, y + 2),
                text,
                font=font,
                fill=(0, 0, 0, SHADOW_OPACITY),
            )
            draw.text(
                (x, y),
                text,
                font=font,
                fill=(255, 255, 255, OPACITY),
            )
            x += step_x
        y += step_y
        row += 1

    tile_layer = tile_layer.rotate(ROTATION_DEGREES, expand=False)

    # вырезаем по центру кусок размером с фото и накладываем
    left = (diag - w) // 2
    top = (diag - h) // 2
    pattern = tile_layer.crop((left, top, left + w, top + h))

    result = Image.alpha_composite(base, pattern).convert("RGB")
    buf = io.BytesIO()
    buf.name = "watermarked.jpg"
    result.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Пришли мне фото, и я верну его с водяным знаком."
    )


async def set_watermark_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        await update.message.reply_text("Эта команда доступна только владельцу бота.")
        return

    # можно сразу указать текст: /setwatermark Мой текст
    args_text = " ".join(context.args).strip() if context.args else ""
    if args_text:
        save_watermark_text(args_text)
        await update.message.reply_text(f"Готово! Новый водяной знак: «{args_text}»")
        return

    waiting_for_watermark_text.add(user_id)
    await update.message.reply_text(
        "Пришли текст, который станет новым водяным знаком."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id == OWNER_ID and user_id in waiting_for_watermark_text:
        waiting_for_watermark_text.discard(user_id)
        new_text = update.message.text.strip()
        save_watermark_text(new_text)
        await update.message.reply_text(f"Готово! Новый водяной знак: «{new_text}»")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    photo = update.message.photo[-1]  # самое большое разрешение
    file = await photo.get_file()
    file_bytes = await file.download_as_bytearray()

    text = load_watermark_text()
    try:
        result_buf = apply_text_watermark(bytes(file_bytes), text)
    except Exception as e:
        logger.exception("Ошибка при наложении вотермарки")
        await update.message.reply_text(f"Не получилось обработать фото: {e}")
        return

    await update.message.reply_photo(photo=result_buf)


async def handle_document_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        return
    file = await doc.get_file()
    file_bytes = await file.download_as_bytearray()

    text = load_watermark_text()
    try:
        result_buf = apply_text_watermark(bytes(file_bytes), text)
    except Exception as e:
        logger.exception("Ошибка при наложении вотермарки")
        await update.message.reply_text(f"Не получилось обработать фото: {e}")
        return

    await update.message.reply_photo(photo=result_buf)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Не задан BOT_TOKEN в .env")

    if not os.path.exists(FONT_PATH):
        raise RuntimeError(
            f"Не найден файл шрифта: {FONT_PATH}\n"
            f"Проверь, что папка '{FONTS_DIR}' существует и содержит "
            ".ttf/.otf файл шрифта (например, PlayfairDisplay-Italic.ttf)."
        )

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("setwatermark", set_watermark_command))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Бот запущен")
    app.run_polling()


if __name__ == "__main__":
    main()
