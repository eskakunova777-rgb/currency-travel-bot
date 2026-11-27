"""Telegram-бот «Миникошелёк для путешественника».

Запуск:
    python bot.py            # бот в Telegram (нужен TELEGRAM_TOKEN в .env)
    python bot.py --check    # проверка ключей и связи с API без запуска бота
    python bot.py --console  # диалог в терминале, без Telegram
"""

import logging
import os
import sys

from dotenv import load_dotenv

import current_api
from current_api import CurrencyAPIError
from database import TravelWalletDB
from wallet import Reply, TravelWallet

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "travel_wallet.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("currency-travel-bot")


def build_markup(reply: Reply):
    from telebot import types

    if not reply.buttons:
        return None
    markup = types.InlineKeyboardMarkup()
    for row in reply.buttons:
        markup.row(*[types.InlineKeyboardButton(text, callback_data=data) for text, data in row])
    return markup


def run_telegram() -> None:
    import telebot

    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    if not token:
        sys.exit("Не задан TELEGRAM_TOKEN. Впишите токен от @BotFather в файл .env.")

    bot = telebot.TeleBot(token)
    wallet = TravelWallet(TravelWalletDB(DB_PATH))

    bot.set_my_commands([
        telebot.types.BotCommand("start", "Главное меню"),
        telebot.types.BotCommand("newtrip", "Создать путешествие"),
        telebot.types.BotCommand("switch", "Мои путешествия, переключить"),
        telebot.types.BotCommand("balance", "Баланс"),
        telebot.types.BotCommand("history", "История расходов"),
        telebot.types.BotCommand("setrate", "Изменить курс"),
    ])

    def send(chat_id: int, reply: Reply) -> None:
        bot.send_message(chat_id, reply.text, reply_markup=build_markup(reply))

    @bot.message_handler(commands=["start", "newtrip", "switch", "trips", "balance", "history",
                                   "setrate", "help"])
    def on_command(message):
        try:
            reply = wallet.handle_command(message.from_user.id, message.text,
                                          message.from_user.first_name or "")
        except Exception:
            log.exception("Ошибка в команде")
            reply = Reply("😔 Что-то пошло не так. Попробуй ещё раз.")
        send(message.chat.id, reply)

    @bot.message_handler(content_types=["text"])
    def on_text(message):
        try:
            if message.text.startswith("/"):
                reply = wallet.handle_command(message.from_user.id, message.text)
            else:
                reply = wallet.handle_text(message.from_user.id, message.text)
        except Exception:
            log.exception("Ошибка в сообщении")
            reply = Reply("😔 Что-то пошло не так. Попробуй ещё раз.")
        send(message.chat.id, reply)

    @bot.message_handler(content_types=["photo", "sticker", "voice", "document", "video", "audio"])
    def on_other(message):
        send(message.chat.id, Reply("Я понимаю только текст: названия стран и суммы числом 🙂"))

    @bot.callback_query_handler(func=lambda call: True)
    def on_button(call):
        try:
            reply = wallet.handle_callback(call.from_user.id, call.data)
        except Exception:
            log.exception("Ошибка в кнопке")
            reply = Reply("😔 Что-то пошло не так. Попробуй ещё раз.")
        bot.answer_callback_query(call.id)
        send(call.message.chat.id, reply)

    me = bot.get_me()
    log.info("Бот @%s запущен. Остановить: Ctrl+C", me.username)
    bot.infinity_polling(skip_pending=True)


def run_check() -> None:
    """Проверка без запуска бота: ключи на месте, API отвечает, база создаётся."""
    ok = True
    for name in ("CURRENCY_ACCESS_KEY", "TELEGRAM_TOKEN"):
        present = bool(os.getenv(name, "").strip())
        print(f"{name}: {'задан' if present else 'НЕ задан'}")
        ok &= present
    try:
        rate = current_api.get_pair_rate("RUB", "EUR")
        print(f"API exchangerate.host: 1 RUB = {rate:.4f} EUR")
        converted = current_api.convert_currency(100, "USD", "EUR")
        print(f"API /convert: 100 USD = {converted['result']:.2f} EUR")
    except CurrencyAPIError as error:
        print(f"API exchangerate.host: ошибка: {error}")
        ok = False
    db = TravelWalletDB(DB_PATH)
    print(f"База {DB_PATH}: Италия -> {db.find_country('Италия')['currency']}")
    print("Всё готово к запуску." if ok else "Есть что поправить, см. выше.")


def run_console() -> None:
    """Диалог с логикой бота в терминале: команды /start, /newtrip и т. д.,
    нажатие кнопки = её callback_data, например rate:yes."""
    wallet = TravelWallet(TravelWalletDB(DB_PATH))
    user_id = 1
    print(wallet.start(user_id, "Путешественник").text)
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if text in ("exit", "quit"):
            return
        if text.startswith("/"):
            reply = wallet.handle_command(user_id, text)
        elif ":" in text and text.split(":")[0] in ("menu", "rate", "exp", "switch"):
            reply = wallet.handle_callback(user_id, text)
        else:
            reply = wallet.handle_text(user_id, text)
        print(reply.text)
        if reply.buttons:
            print("Кнопки: " + " | ".join(f"{t} [{d}]" for row in reply.buttons for t, d in row))


if __name__ == "__main__":
    if "--check" in sys.argv:
        run_check()
    elif "--console" in sys.argv:
        run_console()
    else:
        run_telegram()
