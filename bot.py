import os
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import sqlite3
from datetime import datetime, timedelta
from config import BOT_TOKEN  # BOT_TOKEN хранится в отдельном файле config.py
from sqlite3 import adapt, register_adapter

# --- Конфигурация БД ---
DB_PATH = "flashcards.db"

# Регистрация адаптера для работы с datetime
register_adapter(datetime, lambda val: val.isoformat())

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        last_review DATETIME
    )''')

    # Таблица карточек
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS flashcards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_path TEXT UNIQUE,
        interval INTEGER DEFAULT 1,
        review_date DATETIME,
        user_id INTEGER,
        FOREIGN KEY(user_id) REFERENCES users(id)
    )''')

    conn.commit()
    conn.close()

def add_existing_cards_to_db():
    """Добавляет карточки из папки output_images в базу данных, если их там еще нет."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    image_folder = "output_images"
    if not os.path.exists(image_folder):
        print(f"Папка {image_folder} не найдена.")
        return

    for image_file in os.listdir(image_folder):
        image_path = os.path.join(image_folder, image_file)

        if os.path.isfile(image_path):
            # Пытаемся добавить изображение в базу данных
            cursor.execute('''INSERT OR IGNORE INTO flashcards (image_path, review_date) VALUES (?, ?)''',
                           (image_path, datetime.now()))

    conn.commit()
    conn.close()

# --- Методика промежуточного повторения ---
def calculate_next_review(interval: int) -> datetime:
    return datetime.now() + timedelta(days=interval)

def add_user_to_db(user_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''INSERT OR IGNORE INTO users (id, username, last_review) VALUES (?, ?, ?)''',
                   (user_id, username, datetime.now()))

    conn.commit()
    conn.close()

def get_due_flashcards(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT id, image_path FROM flashcards
                      WHERE user_id = ? AND review_date <= ?''', (user_id, datetime.now()))

    flashcards = cursor.fetchall()
    conn.close()

    return flashcards

def get_new_flashcards(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT id, image_path FROM flashcards
                      WHERE user_id IS NULL OR user_id != ?''', (user_id,))

    flashcards = cursor.fetchall()
    conn.close()

    return flashcards

def assign_card_to_user(card_id: int, user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''UPDATE flashcards SET user_id = ?, review_date = ? WHERE id = ?''',
                   (user_id, datetime.now(), card_id))

    conn.commit()
    conn.close()

def update_flashcard_review(card_id: int, success: bool):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT interval FROM flashcards WHERE id = ?''', (card_id,))
    interval = cursor.fetchone()[0]

    if success:
        interval *= 2  # Удваиваем интервал при успешном повторении
    else:
        interval = 1  # Сбрасываем интервал при ошибке

    next_review_date = calculate_next_review(interval)

    cursor.execute('''UPDATE flashcards SET interval = ?, review_date = ? WHERE id = ?''',
                   (interval, next_review_date, card_id))

    conn.commit()
    conn.close()

# --- Основная логика бота ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user_to_db(user.id, user.username)
    await update.message.reply_text("Привет! Я помогу тебе учить карточки. Используй /review, чтобы начать повторение, или /learn, чтобы начать обучение.")

async def learn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    flashcards = get_new_flashcards(user_id)

    if not flashcards:
        message = update.message if update.message else update.callback_query.message
        await message.reply_text("Все карточки уже были просмотрены. Попробуйте /review для повторения.")
        return

    # Отправляем первую новую карточку
    card_id, image_path = flashcards[0]
    context.user_data['current_card'] = card_id
    assign_card_to_user(card_id, user_id)

    keyboard = [
        [InlineKeyboardButton("Посмотреть изображение", callback_data="view_image")],
        [InlineKeyboardButton("Знаю", callback_data="know")],
        [InlineKeyboardButton("Не знаю", callback_data="dont_know")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    message = update.message if update.message else update.callback_query.message
    await message.reply_text(f"Карточка: {os.path.basename(image_path)}", reply_markup=reply_markup)

async def review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id if update.effective_user else update.callback_query.from_user.id
    flashcards = get_due_flashcards(user_id)

    if not flashcards:
        message = update.message if update.message else update.callback_query.message
        await message.reply_text("На сегодня карточек для повторения нет. Возвращайся завтра!")
        return

    # Отправляем первую карточку
    card_id, image_path = flashcards[0]
    context.user_data['current_card'] = card_id

    keyboard = [
        [InlineKeyboardButton("Посмотреть изображение", callback_data="view_image")],
        [InlineKeyboardButton("Знаю", callback_data="know")],
        [InlineKeyboardButton("Не знаю", callback_data="dont_know")],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)

    message = update.message if update.message else update.callback_query.message
    await message.reply_text(f"Карточка: {os.path.basename(image_path)}", reply_markup=reply_markup)



async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    card_id = context.user_data.get('current_card')

    if not card_id:
        await query.message.reply_text("Сначала начните с /review или /learn.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT image_path FROM flashcards WHERE id = ?''', (card_id,))
    image_path = cursor.fetchone()[0]
    conn.close()

    if query.data == "view_image":
        with open(image_path, 'rb') as img:
            await query.message.reply_photo(photo=InputFile(img))

    elif query.data == "know":
        update_flashcard_review(card_id, True)
        keyboard = [[InlineKeyboardButton("Продолжить обучение", callback_data="next_card")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Правильно! Перейдем к следующей карточке.", reply_markup=reply_markup)

    elif query.data == "dont_know":
        update_flashcard_review(card_id, False)
        keyboard = [[InlineKeyboardButton("Продолжить обучение", callback_data="next_card")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("Неправильно. Попробуй снова позже.", reply_markup=reply_markup)

    elif query.data == "next_card":
        # Здесь мы вызываем review для следующей карточки
        await learn(update, context)


# --- Запуск бота ---
def main():
    init_db()
    add_existing_cards_to_db()  # Добавляем карточки из папки в базу данных

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("learn", learn))
    application.add_handler(CommandHandler("review", review))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling()

if __name__ == "__main__":
    main()
