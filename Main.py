import os
import sqlite3
import threading
import time
import datetime
from telebot import types, TeleBot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from dotenv import load_dotenv
from datetime import timedelta
import pytz
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def log_user_action(user_id, action):
    with open("user_actions.log", "a") as log_file:
        log_file.write(f"{datetime.datetime.now()} - Пользователь {user_id}: {action}\n")

load_dotenv()
TOKEN = os.getenv("API_TOKEN")
if not TOKEN:
    raise ValueError("API_TOKEN не найден. Проверьте .env файл или переменные окружения.")
bot = TeleBot(TOKEN)

with sqlite3.connect('reminders.db') as conn:
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            alert TEXT,
            date TEXT,
            time TEXT,
            repeat_day TEXT
        )
    ''')
    conn.commit()

user_states = {}


def get_db_connection():
    return sqlite3.connect('reminders.db', check_same_thread=False)


def send_reminder():
    while True:
        try:
            timezone = pytz.timezone("Europe/Moscow")
            now = datetime.datetime.now(timezone)
            ten_minutes_before = now + timedelta(minutes=10)
            with get_db_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT user_id, alert FROM reminders WHERE date = ? AND time = ?", (ten_minutes_before.strftime("%Y.%m.%d"), ten_minutes_before.strftime("%H:%M")))
                for user_id, alert in cursor.fetchall():
                    try:
                        bot.send_message(user_id, f"Напоминание через 10 минут: {alert}")
                    except Exception as e:
                        logging.error(f"Ошибка при отправке уведомления за 10 минут пользователю {user_id}: {e}", exc_info=True)

                cursor.execute("SELECT id, user_id, alert, repeat_day FROM reminders WHERE date = ? AND time = ?",
                               (now.strftime("%Y.%m.%d"), now.strftime("%H:%M")))
                for reminder_id, user_id, alert, repeat_day in cursor.fetchall():
                    try:
                        bot.send_message(user_id, f"НАПОМИНАЮ: {alert}")
                        if repeat_day == "True":
                            new_date = now + timedelta(days=7)
                            cursor.execute("UPDATE reminders SET date = ? WHERE id = ?", (new_date.strftime("%Y.%m.%d"), reminder_id))
                        else:
                            cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
                        conn.commit()
                    except Exception as e:
                        logging.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"Ошибка при проверке базы данных: {e}", exc_info=True)
        time.sleep(45)


def cleanup_reminders():
    while True:
        try:
            timezone = pytz.timezone("Europe/Moscow")
            now = datetime.datetime.now(timezone)
            with get_db_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, user_id, alert, date, repeat_day FROM reminders WHERE date < ?", (now.strftime("%Y.%m.%d"),))
                for reminder_id, user_id, alert, date_str, repeat_day in cursor.fetchall():
                    if repeat_day == "True":
                        date_obj = datetime.datetime.strptime(date_str, "%Y.%m.%d")
                        new_date_obj = date_obj + timedelta(days=7)
                        new_date_str = new_date_obj.strftime("%Y.%m.%d")
                        cursor.execute("UPDATE reminders SET date = ? WHERE id = ?", (new_date_str, reminder_id))
                    else:
                        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
                conn.commit()
                logging.info("Проверка просроченных напоминаний завершена")
        except Exception as e:
            logging.error(f"Ошибка при очистке напоминаний: {e}", exc_info=True)
        time.sleep(86400)


def reset_user_state(user_id):
    user_states.pop(user_id, None)


def start_reminder_creation(user_id):
    user_states[user_id] = {"step": "alert"}
    bot.send_message(user_id, "Введите текст напоминания:", reply_markup=types.ReplyKeyboardRemove())


def process_alert_step(user_id, text):
    user_states[user_id]["alert"] = text
    user_states[user_id]["step"] = "date"
    bot.send_message(user_id, "Введите дату в формате: день.месяц.год\nПример: 15.12.2024")


def process_date_step(user_id, text):
    try:
        day, month, year = map(int, text.replace(' ', '').split('.'))
        date = datetime.datetime(year, month, day).strftime("%Y.%m.%d")
        user_states[user_id]["date"] = date
        user_states[user_id]["step"] = "time"
        bot.send_message(user_id, "Введите время в формате: часы:минуты\nПример: 15:30")
    except ValueError:
        bot.send_message(user_id, "Неправильный формат даты!")


def process_time_step(user_id, text):
    try:
        hours, minutes = map(int, text.replace(' ', '').split(':'))
        time_str = f"{hours:02d}:{minutes:02d}"
        user_states[user_id]["time"] = time_str
        user_states[user_id]["step"] = "repeat"
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Повторять каждую неделю", callback_data="repeat_weekly"))
        markup.add(InlineKeyboardButton("Одноразовое напоминание", callback_data="repeat_once"))
        bot.send_message(user_id, "Выберите опцию:", reply_markup=markup)
    except ValueError:
        bot.send_message(user_id, "Неправильный формат времени!")


def add_reminder_to_db(user_id, repeat_day):
    state = user_states[user_id]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (user_id, alert, date, time, repeat_day) VALUES (?, ?, ?, ?, ?)",
            (user_id, state["alert"], state["date"], state["time"], repeat_day)
        )
        conn.commit()
    log_user_action(user_id, f"Добавлено напоминание: {state['alert']}, дата: {state['date']}, время: {state['time']}")
    reset_user_state(user_id)
    bot.send_message(user_id, "Напоминание добавлено!")
    start_menu(user_id)


def start_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Добавить напоминание"), KeyboardButton("Удалить напоминание"))
    bot.send_message(user_id, "Выберите опцию:", reply_markup=markup)


@bot.message_handler(commands=['start'])
def start(message):
    start_menu(message.chat.id)


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_id = message.chat.id
    text = message.text
    if text == "Добавить напоминание":
        start_reminder_creation(user_id)
        return
    elif text == "Удалить напоминание":
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, alert FROM reminders WHERE user_id = ?", (user_id,))
            rows = cursor.fetchall()
        if rows:
            markup = InlineKeyboardMarkup()
            for reminder_id, alert in rows:
                markup.add(InlineKeyboardButton(text=alert, callback_data=f"delete_{reminder_id}"))
            markup.add(InlineKeyboardButton(text="Отмена", callback_data="cancel"))
            bot.send_message(user_id, "Выберите напоминание для удаления:", reply_markup=markup)
        else:
            bot.send_message(user_id, "Нет напоминаний для удаления.")
        return
    if user_id in user_states:
        step = user_states[user_id].get("step")
        if step == "alert":
            process_alert_step(user_id, text)
        elif step == "date":
            process_date_step(user_id, text)
        elif step == "time":
            process_time_step(user_id, text)


@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    user_id = call.message.chat.id
    data = call.data
    if data == "cancel":
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="Удаление отменено.")
    elif data == "repeat_weekly" or data == "repeat_once":
        repeat_day = "True" if data == "repeat_weekly" else "False"
        add_reminder_to_db(user_id, repeat_day)
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="Напоминание добавлено!")
    elif data.startswith("delete_"):
        reminder_id = int(data.split("_")[1])
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
            conn.commit()
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="Напоминание удалено.")
    else:
        bot.send_message(user_id, "Неизвестная команда.")


threading.Thread(target=send_reminder, daemon=True).start()
threading.Thread(target=cleanup_reminders, daemon=True).start()

bot.infinity_polling()