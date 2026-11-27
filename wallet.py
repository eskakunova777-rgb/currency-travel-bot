"""Логика бота без Telegram: диалоги, расчёты, тексты ответов.

Бот (bot.py) только передаёт сюда сообщения и нажатия кнопок
и показывает ответ. Поэтому логику можно проверить тестами и в консоли.
"""

import math
from dataclasses import dataclass, field

import current_api
from current_api import CurrencyAPIError
from database import TravelWalletDB

# Кнопки главного меню: (текст, callback_data)
MAIN_MENU = [
    [("🆕 Создать путешествие", "menu:newtrip"), ("🧳 Мои путешествия", "menu:trips")],
    [("💰 Баланс", "menu:balance"), ("📊 История расходов", "menu:history")],
    [("💱 Изменить курс", "menu:setrate")],
]
YES_NO_RATE = [[("✅ Да", "rate:yes"), ("✏️ Нет, введу свой", "rate:no")]]
YES_NO_EXPENSE = [[("✅ Да", "exp:yes"), ("❌ Нет", "exp:no")]]


@dataclass
class Reply:
    text: str
    buttons: list = field(default_factory=lambda: MAIN_MENU)


def parse_number(text: str) -> float | None:
    """'1 500,50' -> 1500.5. Отрицательные, ноль и не числа -> None."""
    cleaned = text.strip().replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value) or value <= 0:
        return None
    return value


def money(value: float) -> str:
    """200000 -> '200 000.00'."""
    return f"{value:,.2f}".replace(",", " ")


def rate_text(rate: float) -> str:
    return f"{rate:.4f}" if rate >= 0.0001 else f"{rate:.8f}"


def trip_title(trip: dict) -> str:
    return f"{trip['home_country']} → {trip['dest_country']}"


class TravelWallet:
    def __init__(self, db: TravelWalletDB, api=current_api):
        self.db = db
        self.api = api
        self.states: dict[int, dict] = {}

    # --- входные точки ---

    def start(self, user_id: int, first_name: str = "") -> Reply:
        self.db.get_or_create_user(user_id)
        self.states.pop(user_id, None)
        name = f", {first_name}" if first_name else ""
        return Reply(
            f"Привет{name}! 👋\n\n"
            "Я твой персональный помощник для управления финансами в путешествиях! 🌍💰\n\n"
            "Что я умею:\n"
            "• Создавать кошельки для разных стран\n"
            "• Конвертировать валюты по актуальному курсу\n"
            "• Отслеживать твои расходы\n"
            "• Показывать остаток в двух валютах\n\n"
            "Выбери действие из меню ниже:"
        )

    def handle_command(self, user_id: int, command: str, first_name: str = "") -> Reply:
        command = command.split()[0].split("@")[0].lstrip("/").lower()
        actions = {
            "start": lambda: self.start(user_id, first_name),
            "newtrip": lambda: self.new_trip(user_id),
            "switch": lambda: self.list_trips(user_id),
            "trips": lambda: self.list_trips(user_id),
            "balance": lambda: self.balance(user_id),
            "history": lambda: self.history(user_id),
            "setrate": lambda: self.ask_new_rate(user_id),
            "help": lambda: self.start(user_id, first_name),
        }
        action = actions.get(command)
        if action is None:
            return Reply("Не знаю такой команды. Выбери действие в меню:")
        return action()

    def handle_callback(self, user_id: int, data: str) -> Reply:
        if data.startswith("menu:"):
            return self.handle_command(user_id, data.split(":", 1)[1])
        if data.startswith("switch:"):
            return self.switch_trip(user_id, data.split(":", 1)[1])
        if data in ("rate:yes", "rate:no"):
            return self._rate_answer(user_id, data == "rate:yes")
        if data in ("exp:yes", "exp:no"):
            return self._expense_answer(user_id, data == "exp:yes")
        return Reply("Эта кнопка устарела. Главное меню:")

    def handle_text(self, user_id: int, text: str) -> Reply:
        state = self.states.get(user_id, {})
        step = state.get("step")
        if step == "home":
            return self._newtrip_home(user_id, text)
        if step == "dest":
            return self._newtrip_dest(user_id, text)
        if step == "rate_confirm":
            return Reply("Нажми «Да» или «Нет» под сообщением с курсом 👆", YES_NO_RATE)
        if step == "manual_rate":
            return self._newtrip_manual_rate(user_id, text)
        if step == "amount":
            return self._newtrip_amount(user_id, text)
        if step == "setrate":
            return self._set_rate(user_id, text)
        return self._expense_request(user_id, text)

    # --- создание путешествия ---

    def new_trip(self, user_id: int) -> Reply:
        self.db.get_or_create_user(user_id)
        self.states[user_id] = {"step": "home"}
        return Reply(
            "🆕 Создание нового путешествия\n\n"
            "Из какой страны ты выезжаешь? (например: Россия, США, Германия)",
            [],
        )

    def _newtrip_home(self, user_id: int, text: str) -> Reply:
        country = self.db.find_country(text)
        if not country:
            return Reply(
                f"❌ Не могу определить валюту для страны «{text.strip()}».\n\n"
                "Попробуй ещё раз или напиши страну по-другому (например: Италия) "
                "или код валюты (EUR):",
                [],
            )
        self.states[user_id] = {"step": "dest", "home": country}
        return Reply(
            f"✅ Отлично! Валюта {country['country']}: {country['currency']}\n\n"
            "Теперь введи страну назначения:",
            [],
        )

    def _newtrip_dest(self, user_id: int, text: str) -> Reply:
        state = self.states[user_id]
        country = self.db.find_country(text)
        if not country:
            return Reply(
                f"❌ Не могу определить валюту для страны «{text.strip()}».\n\n"
                "Попробуй ещё раз или напиши страну по-другому:",
                [],
            )
        home = state["home"]
        try:
            rate = self.api.get_pair_rate(home["currency"], country["currency"])
        except CurrencyAPIError as error:
            state.update(step="manual_rate", dest=country, api_rate=False)
            return Reply(
                f"⚠️ Не получилось узнать курс: {error}\n\n"
                f"Введи курс вручную: сколько {country['currency']} дают за 1 {home['currency']}?",
                [],
            )
        state.update(step="rate_confirm", dest=country, rate=rate, api_rate=True)
        return Reply(
            f"✅ Валюта {country['country']}: {country['currency']}\n\n"
            f"💱 Текущий курс: 1 {home['currency']} = {rate_text(rate)} {country['currency']}\n\n"
            "Подходит ли этот курс?",
            YES_NO_RATE,
        )

    def _rate_answer(self, user_id: int, accepted: bool) -> Reply:
        state = self.states.get(user_id, {})
        if state.get("step") != "rate_confirm":
            return Reply("Эта кнопка устарела. Главное меню:")
        home, dest = state["home"], state["dest"]
        if accepted:
            state["step"] = "amount"
            return Reply(
                f"✅ Курс принят: 1 {home['currency']} = {rate_text(state['rate'])} {dest['currency']}\n\n"
                f"Теперь введи начальную сумму в {home['currency']} (сколько денег ты берёшь с собой):",
                [],
            )
        state.update(step="manual_rate", api_rate=False)
        return Reply(
            f"✏️ Введи свой курс (например, из местного обменника): "
            f"сколько {dest['currency']} дают за 1 {home['currency']}?",
            [],
        )

    def _newtrip_manual_rate(self, user_id: int, text: str) -> Reply:
        rate = parse_number(text)
        if rate is None:
            return Reply("❌ Курс должен быть положительным числом, например 0.0105. Попробуй ещё раз:", [])
        state = self.states[user_id]
        state.update(step="amount", rate=rate)
        return Reply(
            f"✅ Курс принят: 1 {state['home']['currency']} = {rate_text(rate)} {state['dest']['currency']}\n\n"
            f"Теперь введи начальную сумму в {state['home']['currency']} (сколько денег ты берёшь с собой):",
            [],
        )

    def _newtrip_amount(self, user_id: int, text: str) -> Reply:
        amount = parse_number(text)
        if amount is None:
            return Reply("❌ Нужна сумма числом больше нуля, например 200000. Попробуй ещё раз:", [])
        state = self.states.pop(user_id)
        home, dest, rate = state["home"], state["dest"], state["rate"]
        received = amount * rate
        if state.get("api_rate"):
            # Курс из API: стартовый баланс считаем через endpoint /convert.
            try:
                converted = self.api.convert_currency(amount, home["currency"], dest["currency"])
                received = converted["result"]
            except CurrencyAPIError:
                pass  # остаётся расчёт по принятому курсу
        self.db.create_trip(
            user_id, home["country"], home["currency"], dest["country"], dest["currency"],
            rate, amount, received,
        )
        return Reply(
            "🎉 Путешествие создано!\n\n"
            f"📍 {home['country']} → {dest['country']}\n"
            f"💰 Начальная сумма: {money(amount)} {home['currency']}\n"
            f"💱 Курс: 1 {home['currency']} = {rate_text(rate)} {dest['currency']}\n"
            f"🌍 Получишь: {money(received)} {dest['currency']}\n\n"
            "Теперь просто отправляй мне суммы расходов, и я буду их учитывать! 📊"
        )

    # --- расходы ---

    def _expense_request(self, user_id: int, text: str) -> Reply:
        trip = self.db.get_active_trip(user_id)
        amount = parse_number(text)
        if amount is None:
            return Reply(
                "🤔 Не понял. Отправь сумму расхода числом (например, 25) или выбери действие в меню:"
            )
        if not trip:
            return Reply("Сначала создай путешествие, чтобы учитывать расходы 👇")
        amount_home = amount / trip["rate"]
        self.states[user_id] = {"step": "expense", "amount": amount, "amount_home": amount_home,
                                "trip_id": trip["id"]}
        return Reply(
            "💸 Конвертация расхода:\n\n"
            f"{money(amount)} {trip['dest_currency']} = {money(amount_home)} {trip['home_currency']}\n\n"
            "Учесть как расход?",
            YES_NO_EXPENSE,
        )

    def _expense_answer(self, user_id: int, accepted: bool) -> Reply:
        state = self.states.get(user_id, {})
        if state.get("step") != "expense":
            return Reply("Эта кнопка устарела. Главное меню:")
        self.states.pop(user_id)
        if not accepted:
            return Reply("❌ Расход отклонён")
        trip = self.db.get_trip(user_id, state["trip_id"])
        if trip is None:
            return Reply("Путешествие не найдено. Главное меню:")
        if state["amount"] > trip["balance_dest"] + 1e-9:
            return Reply(
                f"⚠️ Сумма больше остатка ({money(trip['balance_dest'])} {trip['dest_currency']}). "
                "Расход не учтён."
            )
        trip = self.db.add_expense(user_id, trip["id"], state["amount"], state["amount_home"], trip["rate"])
        return Reply(
            "✅ Расход учтён!\n\n"
            f"💸 Потрачено: {money(state['amount'])} {trip['dest_currency']} = "
            f"{money(state['amount_home'])} {trip['home_currency']}\n\n"
            "💰 Остаток:\n"
            f"🌍 {money(trip['balance_dest'])} {trip['dest_currency']}\n"
            f"🏠 {money(trip['balance_home'])} {trip['home_currency']}"
        )

    # --- разделы меню ---

    def list_trips(self, user_id: int) -> Reply:
        self.states.pop(user_id, None)
        trips = self.db.get_trips(user_id)
        if not trips:
            return Reply("У тебя пока нет путешествий. Создай первое 👇")
        active = self.db.get_active_trip(user_id)
        lines = ["🧳 Твои путешествия:\n"]
        buttons = []
        for trip in trips:
            mark = "👉 " if active and trip["id"] == active["id"] else ""
            lines.append(
                f"{mark}{trip_title(trip)}: {money(trip['balance_dest'])} {trip['dest_currency']}"
            )
            buttons.append([(f"{'✅ ' if mark else ''}{trip_title(trip)}", f"switch:{trip['id']}")])
        lines.append("\nНажми на путешествие, чтобы сделать его активным:")
        return Reply("\n".join(lines), buttons + MAIN_MENU)

    def switch_trip(self, user_id: int, trip_id: str) -> Reply:
        if not trip_id.isdigit() or not self.db.set_active_trip(user_id, int(trip_id)):
            return Reply("Такое путешествие не найдено. Главное меню:")
        trip = self.db.get_active_trip(user_id)
        return Reply(f"✅ Активное путешествие: {trip_title(trip)}\n\nОтправляй суммы расходов 👇")

    def balance(self, user_id: int) -> Reply:
        self.states.pop(user_id, None)
        trip = self.db.get_active_trip(user_id)
        if not trip:
            return Reply("Нет активного путешествия. Создай его 👇")
        return Reply(
            f"💰 Баланс путешествия: {trip_title(trip)}\n\n"
            f"🏠 Домашняя валюта: {money(trip['balance_home'])} {trip['home_currency']}\n"
            f"🌍 Валюта назначения: {money(trip['balance_dest'])} {trip['dest_currency']}\n\n"
            f"💱 Курс: 1 {trip['home_currency']} = {rate_text(trip['rate'])} {trip['dest_currency']}\n"
            f"💳 Начальная сумма: {money(trip['initial_amount'])} {trip['home_currency']}"
        )

    def history(self, user_id: int) -> Reply:
        self.states.pop(user_id, None)
        trip = self.db.get_active_trip(user_id)
        if not trip:
            return Reply("Нет активного путешествия. Создай его 👇")
        expenses = self.db.get_expenses(trip["id"])
        if not expenses:
            return Reply(f"📊 В путешествии {trip_title(trip)} пока нет расходов.")
        lines = [f"📊 Последние расходы ({trip_title(trip)}):\n"]
        for exp in expenses:
            date = exp["created_at"].replace("T", " ")[:16]
            lines.append(
                f"{date}: {money(exp['amount_dest'])} {trip['dest_currency']} = "
                f"{money(exp['amount_home'])} {trip['home_currency']}"
            )
        return Reply("\n".join(lines))

    def ask_new_rate(self, user_id: int) -> Reply:
        trip = self.db.get_active_trip(user_id)
        if not trip:
            self.states.pop(user_id, None)
            return Reply("Нет активного путешествия. Создай его 👇")
        self.states[user_id] = {"step": "setrate", "trip_id": trip["id"]}
        return Reply(
            f"✏️ Введи новый курс обмена: сколько {trip['dest_currency']} "
            f"за 1 {trip['home_currency']} (сейчас {rate_text(trip['rate'])}):",
            [],
        )

    def _set_rate(self, user_id: int, text: str) -> Reply:
        rate = parse_number(text)
        if rate is None:
            return Reply("❌ Курс должен быть положительным числом, например 0.0105. Попробуй ещё раз:", [])
        state = self.states.pop(user_id)
        self.db.update_rate(user_id, state["trip_id"], rate)
        trip = self.db.get_trip(user_id, state["trip_id"])
        return Reply(
            "✅ Курс обновлён!\n\n"
            f"📍 {trip_title(trip)}\n"
            f"💱 Новый курс: 1 {trip['home_currency']} = {rate_text(rate)} {trip['dest_currency']}"
        )
