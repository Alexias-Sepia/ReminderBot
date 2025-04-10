import os
import sqlite3
import threading
import time
import datetime
from telebot import types
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telebot import TeleBot
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

conn = sqlite3.connect('reminders.db', check_same_thread=False)
cursor = conn.cursor()

cursor.execute('''CREATE TABLE IF NOT EXISTS reminders (user_id INTEGER, alert TEXT, date TEXT, time TEXT, repeat_day TEXT)''')
conn.commit()

user_states = {}

def send_reminder():
    while True:
        try:            
            timezone = pytz.timezone("Europe/Moscow")
            now = datetime.datetime.now(timezone)
            
            ten_minutes_before = now + timedelta(minutes=10)
            cursor.execute("SELECT * FROM reminders WHERE date = ? AND time = ?", 
                           (ten_minutes_before.strftime("%Y.%m.%d"), ten_minutes_before.strftime("%H:%M")))
            rows_10_min = cursor.fetchall()

            for row in rows_10_min:
                user_id, alert, _, _, repeat_day = row
                try:
                    bot.send_message(user_id, f"Напоминание через 10 минут: {alert}")
                except Exception as e:
                    logging.error(f"Ошибка при отправке уведомления за 10 минут пользователю {user_id}: {e}", exc_info=True)

            cursor.execute("SELECT * FROM reminders WHERE date = ? AND time = ?",
                           (now.strftime("%Y.%m.%d"), now.strftime("%H:%M")))
            rows = cursor.fetchall()
            for row in rows:
                user_id, alert, _, _, repeat_day = row
                try:
                    bot.send_message(user_id, f"НАПОМИНАЮ: {alert}")
                    if repeat_day == "True":
                        new_date = now + timedelta(days=7)
                        cursor.execute("UPDATE reminders SET date = ? WHERE user_id = ? AND alert = ?",
                                       (new_date.strftime("%Y.%m.%d"), user_id, alert))  
                    else:
                        cursor.execute("DELETE FROM reminders WHERE user_id = ? AND alert = ?", (user_id, alert))
                    conn.commit()
                except Exception as e:
                    logging.error(f"Ошибка при отправке напоминания пользователю {user_id}: {e}", exc_info=True)
        except Exception as e:
            logging.error(f"Ошибка при проверке базы данных: {e}", exc_info=True)
        time.sleep(45)


thread = threading.Thread(target=send_reminder)
thread.daemon = True
thread.start()


@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.chat.id
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("Добавить напоминание"), KeyboardButton("Удалить напоминание"))
    bot.send_message(user_id, "Этот бот поможет вам создавать напоминания.\nВыберите опцию:", reply_markup=markup)
        

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    user_id = message.chat.id
    if message.text == "Добавить напоминание":
        user_states[user_id] = "alert"
        bot.send_message(user_id, "Введите текст напоминания:", reply_markup=types.ReplyKeyboardRemove())
        return  
    elif message.text == "Удалить напоминание":
        cursor.execute("SELECT alert FROM reminders WHERE user_id = ?", (user_id,))
        rows = cursor.fetchall()
        if rows:
            markup = InlineKeyboardMarkup()
            for row in rows:
                markup.add(InlineKeyboardButton(text=row[0], callback_data=row[0]))
            markup.add(InlineKeyboardButton(text="Отмена", callback_data="cancel"))
            bot.send_message(user_id, "Выберите напоминание для удаления:", reply_markup=markup)
        else:
            bot.send_message(user_id, "Нет напоминаний для удаления.")
        return
    if user_id in user_states:
        if user_states[user_id] == "alert":
            user_states[user_id + 1000] = message.text  
            bot.send_message(user_id, "Введите дату в формате: день.месяц.год\nПример: 15.12.2024")
            user_states[user_id] = "date"
        elif user_states[user_id] == "date":
            try:
                day, month, year = map(int, message.text.replace(' ', '').split('.'))
                date = datetime.datetime(year, month, day).strftime("%Y.%m.%d")
                user_states[user_id + 2000] = date 
                bot.send_message(user_id, "Введите время в формате: часы:минуты\nПример: 15:30")
                user_states[user_id] = "time"
            except ValueError:
                bot.send_message(user_id, "Неправильный формат даты!")
        elif user_states[user_id] == "time":
            try:
                hours, minutes = map(int, message.text.replace(' ', '').split(':'))
                time_str = f"{hours:02d}:{minutes:02d}"
                user_states[user_id + 3000] = time_str
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("Повторять каждую неделю", callback_data="repeat_weekly"))
                markup.add(InlineKeyboardButton("Одноразовое напоминание", callback_data="repeat_once"))
                user_states[user_id] = "repeat"
                bot.send_message(user_id, "Выберите опцию:", reply_markup=markup)
            except ValueError:
                bot.send_message(user_id, "Неправильный формат времени!")


def cleanup_reminders():
    while True:
        local_conn = None
        try:
            local_conn = sqlite3.connect('reminders.db')
            local_cursor = local_conn.cursor()
            
            timezone = pytz.timezone("Europe/Moscow")
            now = datetime.datetime.now(timezone)
            
            local_cursor.execute("SELECT * FROM reminders WHERE date < ?", 
                               (now.strftime("%Y.%m.%d"),))
            expired_reminders = local_cursor.fetchall()
            
            for reminder in expired_reminders:
                user_id, alert, date_str, repeat_day = reminder
                
                if repeat_day == "True":
                    date_obj = datetime.datetime.strptime(date_str, "%Y.%m.%d")
                    new_date_obj = date_obj + timedelta(days=7)
                    new_date_str = new_date_obj.strftime("%Y.%m.%d")
                    
                    local_cursor.execute("UPDATE reminders SET date = ? WHERE user_id = ? AND alert = ?", (new_date_str, user_id, alert))
                else:
                    local_cursor.execute("DELETE FROM reminders WHERE user_id = ? AND alert = ?", 
                                      (user_id, alert))
            
            local_conn.commit()
            logging.info("Проверка просроченных напоминаний завершена")
            
        except Exception as e:
            logging.error(f"Ошибка при очистке напоминаний: {e}", exc_info=True)
        finally:
            if local_conn:
                local_conn.close()
                
        time.sleep(86400)


cleanup_thread = threading.Thread(target=cleanup_reminders)
cleanup_thread.daemon = True
cleanup_thread.start()


@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    user_id = call.message.chat.id
    if call.data == "cancel":
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="Удаление отменено.")
    elif call.data == "repeat_weekly" or call.data == "repeat_once":
        repeat_day = "True" if call.data == "repeat_weekly" else "False"
        alert = user_states.get(user_id + 1000)
        date = user_states.get(user_id + 2000)
        time_str = user_states.get(user_id + 3000)
        log_user_action(user_id, f"Добавлено напоминание: {alert}, дата: {date}, время: {time_str}")
        cursor.execute("INSERT INTO reminders (user_id, alert, date, time, repeat_day) VALUES (?, ?, ?, ?, ?)", (user_id, alert, date, time_str, repeat_day))
        conn.commit()
        del user_states[user_id]
        del user_states[user_id + 1000]
        del user_states[user_id + 2000]
        del user_states[user_id + 3000]
        bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text="Напоминание добавлено!")
        start(call.message)
    else:
        try:
            cursor.execute("DELETE FROM reminders WHERE user_id = ? AND alert = ?", (user_id, call.data))
            conn.commit()
            bot.edit_message_text(chat_id=user_id, message_id=call.message.message_id, text=f"Напоминание '{call.data}' удалено.")
        except Exception as e:
            print(f"Ошибка при удалении напоминания: {e}")
            bot.send_message(user_id, "Произошла ошибка при удалении напоминания.")


bot.infinity_polling()