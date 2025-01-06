import os
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup, CallbackQuery
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import sqlite3
from datetime import datetime, timedelta
from config import BOT_TOKEN  # BOT_TOKEN хранится в отдельном файле config.py
from sqlite3 import adapt, register_adapter

# --- Конфигурация БД ---
DB_PATH = "flashcards.db"

# Регистрация адаптера для работы с datetime
register_adapter(datetime, lambda val: val.isoformat())

intervals = [
    timedelta(seconds=0),  # Уровень 0: немедленно
    timedelta(minutes=15),  # Уровень 1: через 15 минут
    timedelta(hours=4),  # Уровень 2: через 4 часа
    timedelta(hours=8),  # Уровень 3: через 8 часов
    timedelta(days=1.5)  # Уровень 4: через 1.5 дня
]


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Включение поддержки внешних ключей
    cursor.execute('PRAGMA foreign_keys = ON;')

    # Таблица пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT,
        last_review DATETIME,
        status TEXT DEFAULT 'idle'
    )''')

    # Проверяем, есть ли столбец `status` в таблице `users`, и добавляем его, если его нет
    cursor.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cursor.fetchall()]
    if "status" not in columns:
        cursor.execute("ALTER TABLE users ADD COLUMN status TEXT DEFAULT 'idle'")

    # Таблица карточек
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS flashcards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_path TEXT UNIQUE
    )''')

    # Таблица статусов карточек для пользователей
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_flashcards (
        user_id INTEGER,
        card_id INTEGER,
        confidence INTEGER DEFAULT 0,
        review_date DATETIME,
        PRIMARY KEY (user_id, card_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (card_id) REFERENCES flashcards(id) ON DELETE CASCADE
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
            cursor.execute('''INSERT OR IGNORE INTO flashcards (image_path) VALUES (?)''',
                           (image_path,))

    conn.commit()
    conn.close()


# --- Методика промежуточного повторения ---
def calculate_next_review(confidence: int) -> datetime:
    # Получаем текущий момент времени
    now = datetime.now()

    # Возвращаем время следующего повторения для заданного уровня
    # Если confidence выходит за пределы массива, возвращаем текущее время
    return now + intervals[confidence] if 0 <= confidence < len(intervals) else now



def add_user_to_db(user_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''INSERT OR IGNORE INTO users (id, username, last_review) VALUES (?, ?, ?)''',
                   (user_id, username, datetime.now()))

    conn.commit()
    conn.close()


def get_user_status(user_id):
    """Возвращает текущий статус пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT status FROM users WHERE id = ?''', (user_id,))
    result = cursor.fetchone()
    conn.close()

    return result[0] if result else "idle"


def set_user_status(user_id, status):
    """Устанавливает текущий статус пользователя."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''UPDATE users SET status = ? WHERE id = ?''', (status, user_id))

    conn.commit()
    conn.close()


def get_due_flashcards(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT uf.card_id, f.image_path FROM user_flashcards uf
                      JOIN flashcards f ON uf.card_id = f.id
                      WHERE uf.user_id = ? AND uf.review_date <= ?''', (user_id, datetime.now()))

    flashcards = cursor.fetchall()
    conn.close()

    return flashcards


def get_new_flashcards(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT id, image_path FROM flashcards
                      WHERE id NOT IN (SELECT card_id FROM user_flashcards WHERE user_id = ?)''', (user_id,))

    flashcards = cursor.fetchall()
    conn.close()

    return flashcards


def assign_card_to_user(card_id: int, user_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''INSERT OR IGNORE INTO user_flashcards (user_id, card_id, review_date) VALUES (?, ?, ?)''',
                   (user_id, card_id, datetime.now()))

    conn.commit()
    conn.close()


def update_flashcard_review(user_id: int, card_id: int, success: bool):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute('''SELECT confidence FROM user_flashcards WHERE user_id = ? AND card_id = ?''', (user_id, card_id))
    result = cursor.fetchone()

    if result is None:
        conn.close()
        return

    confidence = result[0]

    if success:
        confidence = min(confidence + 1, 4)  # Увеличиваем уверенность, но не выше 4
    else:
        confidence = max(confidence - 1, 0)  # Уменьшаем уверенность, но не ниже 0

    next_review_date = calculate_next_review(confidence)

    cursor.execute('''UPDATE user_flashcards SET confidence = ?, review_date = ? WHERE user_id = ? AND card_id = ?''',
                   (confidence, next_review_date, user_id, card_id))

    conn.commit()
    conn.close()


# --- Основная логика бота ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user_to_db(user.id, user.username)

    # Устанавливаем статус пользователя на 'idle'
    set_user_status(user.id, "idle")

    # Удаляем кнопки у всех предыдущих сообщений, отправленных ботом
    if "bot_messages" in context.user_data:
        for message_id in context.user_data["bot_messages"]:
            try:
                await context.bot.edit_message_reply_markup(
                    chat_id=update.effective_chat.id,
                    message_id=message_id,
                    reply_markup=None
                )
            except Exception as e:
                # Игнорируем ошибки, если сообщение уже недоступно для редактирования
                print(f"Не удалось удалить кнопки у сообщения {message_id}: {e}")

        # Очищаем список сообщений
        context.user_data["bot_messages"] = []

    sent_message = await update.effective_message.reply_text(
        "Привет! Добро пожаловать в бота для изучения карточек с использованием методики интервального повторения.\n\n"
        "📋 **Доступные команды:**\n"
        "/learn - учить новые карточки\n"
        "/review - повторять карточки\n"
        "/statistic - посмотреть вашу статистику\n"
        "/about - узнать больше о боте и методике\n\n"
        "Начните обучение уже сейчас и улучшайте свои знания шаг за шагом!"
    )

    # Сохраняем ID нового сообщения, отправленного ботом
    if "bot_messages" not in context.user_data:
        context.user_data["bot_messages"] = []
    context.user_data["bot_messages"].append(sent_message.message_id)



async def check_user_status(user_id: int, message, required_status: str = "idle") -> bool:
    """
    Проверяет текущий статус пользователя и возвращает True, если он соответствует требуемому.
    В противном случае отправляет сообщение и возвращает False.

    :param user_id: ID пользователя.
    :param message: Сообщение для отправки ответа пользователю.
    :param required_status: Требуемый статус пользователя (по умолчанию "idle").
    :return: True, если статус соответствует, иначе False.
    """
    current_status = get_user_status(user_id)
    if current_status != required_status:
        await message.reply_text(
            "Вы уже выполняете другую задачу. Чтобы сменить состояние, введите /start."
        )
        return False
    return True


async def learn(update: Update | CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    # Если update — это CallbackQuery, извлекаем user_id из него
    if isinstance(update, CallbackQuery):
        user_id = update.from_user.id
        message = update.message
    else:  # Иначе это обычный Update
        user_id = update.effective_user.id
        message = update.message
        # Проверяем статус пользователя
        if not await check_user_status(user_id, message):
            return

    # Установить статус "learning"
    set_user_status(user_id, "learning")

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
    response = await message.reply_text(f"Карточка: {os.path.basename(image_path)}", reply_markup=reply_markup)

    # Сохраняем ID сообщения в контекст
    if "bot_messages" not in context.user_data:
        context.user_data["bot_messages"] = []
    context.user_data["bot_messages"].append(response.message_id)

async def review(update: Update | CallbackQuery, context: ContextTypes.DEFAULT_TYPE):
    # Если update — это CallbackQuery, извлекаем user_id из него
    if isinstance(update, CallbackQuery):
        user_id = update.from_user.id
        message = update.message
    else:  # Иначе это обычный Update
        user_id = update.effective_user.id
        message = update.message
        # Проверяем статус пользователя
        if not await check_user_status(user_id, message):
            return

    # Установить статус "reviewing"
    set_user_status(user_id, "reviewing")

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
    response = await message.reply_text(f"Карточка: {os.path.basename(image_path)}", reply_markup=reply_markup)

    # Сохраняем ID сообщения в контекст
    if "bot_messages" not in context.user_data:
        context.user_data["bot_messages"] = []
    context.user_data["bot_messages"].append(response.message_id)

async def show_next_card(query, user_id, context):
    """Определяет текущий статус пользователя и показывает следующую карточку."""
    # Проверяем статус пользователя (учим новые или повторяем)
    current_status = get_user_status(user_id)

    if current_status == "learning":
        flashcards = get_new_flashcards(user_id)
        if flashcards:
            await learn(query, context)
        else:
            # Если новых карточек нет, переключаем статус на "reviewing"
            set_user_status(user_id, "reviewing")
            await query.message.reply_text(
                "Вы завершили обучение новых карточек. Переходим к повторению."
            )
            flashcards = get_due_flashcards(user_id)
            if flashcards:
                await review(query, context)
            else:
                # Если карточек для повторения тоже нет
                set_user_status(user_id, "idle")
                await query.message.reply_text(
                    "На данный момент карточек для обучения и повторения больше нет. Хорошая работа!"
                )
    elif current_status == "reviewing":
        flashcards = get_due_flashcards(user_id)
        if flashcards:
            await review(query, context)
        else:
            # Если карточек для повторения нет, переключаем статус на "learning"
            set_user_status(user_id, "learning")
            await query.message.reply_text(
                "Вы завершили повторение карточек. Переходим к обучению новых."
            )
            flashcards = get_new_flashcards(user_id)
            if flashcards:
                await learn(query, context)
            else:
                # Если карточек для обучения тоже нет
                set_user_status(user_id, "idle")
                await query.message.reply_text(
                    "На данный момент карточек для обучения и повторения больше нет. Хорошая работа!"
                )
    else:
        # Если пользователь в статусе "idle", уведомляем его
        await query.message.reply_text(
            "На данный момент карточек для обучения и повторения больше нет. Хорошая работа!"
        )


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
            # Отправляем изображение как фото
            await query.message.reply_photo(photo=InputFile(img))

        with open(image_path, 'rb') as img:
            # Отправляем то же изображение как документ (без сжатия)
            await query.message.reply_document(document=InputFile(img))


    elif query.data == "know":
        update_flashcard_review(user_id, card_id, True)
        await query.message.edit_reply_markup(reply_markup=None)
        await show_next_card(query, user_id, context)

    elif query.data == "dont_know":
        update_flashcard_review(user_id, card_id, False)
        await query.message.edit_reply_markup(reply_markup=None)
        await show_next_card(query, user_id, context)

    elif query.data == "next_card":
        await show_next_card(query, user_id, context)


async def about(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Генерация текста для интервалов
    intervals_text = "\n".join(
        [f"{i}: Через {format_timedelta(interval)}" for i, interval in enumerate(intervals)]
    )

    about_text = (
        "Этот бот предназначен для изучения карточек с использованием методики интервального повторения.\n\n"
        "📌 **Как это работает?**\n"
        "Когда вы изучаете карточки, бот определяет ваш уровень уверенности в ответах:\n\n"
        f"{intervals_text}\n\n"
        "Карточки, которые вы знаете лучше, будут показываться реже, а те, которые сложнее, — чаще.\n\n"
        "Для начала работы используйте команды:\n"
        "/learn - учить новые карточки\n"
        "/review - повторять карточки\n"
        "/statistic - посмотреть вашу статистику\n"
    )

    await update.message.reply_text(about_text)


def format_timedelta(delta: timedelta) -> str:
    """Форматирует timedelta в читаемую строку."""
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts = []
    if days > 0:
        parts.append(f"{days} дн.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} мин.")
    if seconds > 0:
        parts.append(f"{seconds} сек.")

    return ", ".join(parts) if parts else "0 сек."


async def statistic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Подсчет общего количества карточек
    cursor.execute('''SELECT COUNT(*) FROM user_flashcards WHERE user_id = ?''', (user_id,))
    total_cards = cursor.fetchone()[0]

    # Подсчет карточек на каждом уровне уверенности
    cursor.execute('''
        SELECT confidence, COUNT(*)
        FROM user_flashcards
        WHERE user_id = ?
        GROUP BY confidence
        ORDER BY confidence ASC
    ''', (user_id,))
    level_stats = cursor.fetchall()

    conn.close()

    # Формируем сообщение со статистикой
    if total_cards == 0:
        stats_message = "У вас пока нет карточек. Начните с /learn, чтобы добавить новые!"
    else:
        stats_message = f"📊 Ваша статистика:\n\n"
        stats_message += f"Общее количество карточек: {total_cards}\n\n"
        stats_message += "Уровень уверенности:\n"

        # Добавляем статистику по уровням
        levels = {i: 0 for i in range(5)}  # Уровни от 0 до 4
        for level, count in level_stats:
            levels[level] = count

        for level, count in levels.items():
            stats_message += f"  Уровень {level}: {count} карточек\n"

    # Отправляем сообщение
    await update.message.reply_text(stats_message)


# --- Запуск бота ---
def main():
    init_db()
    add_existing_cards_to_db()  # Добавляем карточки из папки в базу данных

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("learn", learn))
    application.add_handler(CommandHandler("review", review))
    application.add_handler(CommandHandler("about", about))
    application.add_handler(CommandHandler("statistic", statistic))
    application.add_handler(CallbackQueryHandler(button_handler))

    application.run_polling()


if __name__ == "__main__":
    main()
