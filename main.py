import os
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

# Простые словари в памяти для хранения текста книги и индекса текущего предложения для каждого пользователя
user_sentences = {}
user_index = {}

# Функция для разбиения текста на предложения
def split_into_sentences(text):
    import re
    # Умное разбиение по точкам, восклицательным и вопросительным знакам
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    if not sentences or sentences == ['']:
        return [text]
    return [s.strip() for s in sentences if s.strip()]

# Генерация инлайн-клавиатуры
def get_keyboard():
    markup = InlineKeyboardMarkup()
    markup.row(
        InlineKeyboardButton("⬅️ Назад", callback_data="prev"),
        InlineKeyboardButton("Далее ➡️", callback_data="next")
    )
    return markup

@bot.message_handler(commands=['start'])
def func_start(message):
    bot.send_message(
        message.chat.id, 
        "Привет! Пришли мне текст главы или книги, и мы начнем чтение по предложениям."
    )

# Обработка входящего текста книги
@bot.message_handler(func=lambda message: True)
def handle_text(message):
    chat_id = message.chat.id
    text = message.text
    
    if text.startswith('/'):
        return

    sentences = split_into_sentences(text)
    user_sentences[chat_id] = sentences
    user_index[chat_id] = 0
    
    first_sentence = sentences[0]
    response = f"Книга загружена ({len(sentences)} предложений)!\n\nПредложение №1\n\n{first_sentence}"
    bot.send_message(chat_id, response, reply_markup=get_keyboard())

# Обработка нажатий на кнопки
@bot.callback_query_handler(func=lambda call: True)
def callback_inline(call):
    chat_id = call.message.chat.id
    
    if chat_id not in user_sentences:
        bot.answer_callback_query(call.id, "Сначала отправь текст книги!")
        return
        
    sentences = user_sentences[chat_id]
    idx = user_index.get(chat_id, 0)
    
    if call.data == "next":
        if idx < len(sentences) - 1:
            idx += 1
        else:
            bot.answer_callback_query(call.id, "Это последнее предложение.")
            return
    elif call.data == "prev":
        if idx > 0:
            idx -= 1
        else:
        # Версионируем индекс обратно в глобальный словарь
            bot.answer_callback_query(call.id, "Это первое предложение.")
            return
            
    user_index[chat_id] = idx
    response = f"Предложение №{idx + 1}\n\n{sentences[idx]}"
    
    try:
        bot.edit_message_text(
            chat_id=chat_id,
            message_id=call.message.message_id,
            text=response,
            reply_markup=get_keyboard()
        )
    except Exception:
        pass
        
    bot.answer_callback_query(call.id)

if __name__ == "__main__":
    print("Сбрасываем старый вебхук...")
    bot.remove_webhook()
    print("Бот запущен и ждет сообщения...")
    bot.infinity_polling()
