"""Тесты логики бота и модуля API на моках, без сети и Telegram."""

import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import current_api  # noqa: E402
from current_api import CurrencyAPIError  # noqa: E402
from database import TravelWalletDB  # noqa: E402
from wallet import TravelWallet, money, parse_number  # noqa: E402

USER = 42


class FakeAPI:
    def __init__(self, rate=0.0104, fail=False):
        self.rate, self.fail = rate, fail

    def get_pair_rate(self, a, b):
        if self.fail:
            raise CurrencyAPIError("Нет связи с сервисом курсов валют.")
        return self.rate

    def convert_currency(self, amount, a, b):
        if self.fail:
            raise CurrencyAPIError("Нет связи")
        return {"result": amount * self.rate, "rate": self.rate}


@pytest.fixture
def wallet(tmp_path):
    return TravelWallet(TravelWalletDB(str(tmp_path / "test.db")), api=FakeAPI())


def create_trip(w, amount="200000"):
    w.handle_command(USER, "/newtrip")
    w.handle_text(USER, "Россия")
    w.handle_text(USER, "Италия")
    w.handle_callback(USER, "rate:yes")
    return w.handle_text(USER, amount)


# --- вспомогательные функции ---

def test_parse_number():
    assert parse_number("1 500,50") == 1500.5
    assert parse_number("25") == 25
    for bad in ("abc", "0", "-5", "nan", "inf", ""):
        assert parse_number(bad) is None


def test_money_format():
    assert money(200000) == "200 000.00"


# --- /start и /newtrip ---

def test_start_shows_menu(wallet):
    reply = wallet.handle_command(USER, "/start", "Женя")
    assert "Привет, Женя" in reply.text
    labels = [t for row in reply.buttons for t, _ in row]
    assert any("Создать путешествие" in t for t in labels)
    assert any("Изменить курс" in t for t in labels)


def test_newtrip_full_flow(wallet):
    reply = create_trip(wallet)
    assert "Путешествие создано" in reply.text
    assert "200 000.00 RUB" in reply.text
    assert "2 080.00 EUR" in reply.text
    trip = wallet.db.get_active_trip(USER)
    assert trip["dest_currency"] == "EUR" and trip["balance_dest"] == pytest.approx(2080)


def test_unknown_country_asks_again(wallet):
    wallet.handle_command(USER, "/newtrip")
    wallet.handle_text(USER, "Россия")
    reply = wallet.handle_text(USER, "Италию")
    assert "Не могу определить валюту" in reply.text
    reply = wallet.handle_text(USER, "италия")
    assert "EUR" in reply.text and "Подходит ли этот курс" in reply.text


def test_currency_code_instead_of_country(wallet):
    wallet.handle_command(USER, "/newtrip")
    reply = wallet.handle_text(USER, "rub")
    assert "RUB" in reply.text


def test_manual_rate_when_user_says_no(wallet):
    wallet.handle_command(USER, "/newtrip")
    wallet.handle_text(USER, "Россия")
    wallet.handle_text(USER, "Италия")
    reply = wallet.handle_callback(USER, "rate:no")
    assert "свой курс" in reply.text
    assert "положительным числом" in wallet.handle_text(USER, "дорого").text
    wallet.handle_text(USER, "0,01")
    reply = wallet.handle_text(USER, "1000")
    assert "10.00 EUR" in reply.text


def test_api_failure_falls_back_to_manual_rate(tmp_path):
    w = TravelWallet(TravelWalletDB(str(tmp_path / "t.db")), api=FakeAPI(fail=True))
    w.handle_command(USER, "/newtrip")
    w.handle_text(USER, "Россия")
    reply = w.handle_text(USER, "Италия")
    assert "Не получилось узнать курс" in reply.text
    w.handle_text(USER, "0.01")
    assert "Путешествие создано" in w.handle_text(USER, "500").text


def test_bad_amount(wallet):
    reply = create_trip(wallet, amount="много")
    assert "числом больше нуля" in reply.text


# --- расходы ---

def test_expense_confirm_and_balance(wallet):
    create_trip(wallet)
    reply = wallet.handle_text(USER, "26")
    assert "Учесть как расход" in reply.text and "2 500.00 RUB" in reply.text
    reply = wallet.handle_callback(USER, "exp:yes")
    assert "Расход учтён" in reply.text
    assert "2 054.00 EUR" in reply.text and "197 500.00 RUB" in reply.text
    assert len(wallet.db.get_expenses(wallet.db.get_active_trip(USER)["id"])) == 1


def test_expense_rejected(wallet):
    create_trip(wallet)
    wallet.handle_text(USER, "30")
    assert "Расход отклонён" in wallet.handle_callback(USER, "exp:no").text
    assert wallet.db.get_active_trip(USER)["balance_dest"] == pytest.approx(2080)


def test_expense_more_than_balance(wallet):
    create_trip(wallet)
    wallet.handle_text(USER, "35435")
    assert "больше остатка" in wallet.handle_callback(USER, "exp:yes").text


def test_expense_without_trip(wallet):
    assert "Сначала создай путешествие" in wallet.handle_text(USER, "25").text


def test_not_a_number_text(wallet):
    create_trip(wallet)
    assert "Не понял" in wallet.handle_text(USER, "привет").text


def test_old_button_is_safe(wallet):
    assert "устарела" in wallet.handle_callback(USER, "exp:yes").text


# --- меню ---

def test_setrate_changes_conversion(wallet):
    create_trip(wallet)
    wallet.handle_callback(USER, "menu:setrate")
    reply = wallet.handle_text(USER, "0.02")
    assert "Курс обновлён" in reply.text and "0.0200 EUR" in reply.text
    assert "1 000.00 RUB" in wallet.handle_text(USER, "20").text


def test_trips_and_switch(wallet):
    create_trip(wallet)
    wallet.handle_command(USER, "/newtrip")
    wallet.handle_text(USER, "Россия")
    wallet.handle_text(USER, "Китай")
    wallet.handle_callback(USER, "rate:yes")
    wallet.handle_text(USER, "1000")
    reply = wallet.handle_command(USER, "/switch")
    assert "Россия → Италия" in reply.text and "Россия → Китай" in reply.text
    first_id = wallet.db.get_trips(USER)[0]["id"]
    assert "Россия → Италия" in wallet.handle_callback(USER, f"switch:{first_id}").text
    assert wallet.db.get_active_trip(USER)["dest_country"] == "Италия"


def test_switch_to_foreign_trip_is_denied(wallet):
    create_trip(wallet)
    trip_id = wallet.db.get_active_trip(USER)["id"]
    assert "не найдено" in wallet.handle_callback(999, f"switch:{trip_id}").text


def test_balance_and_history(wallet):
    create_trip(wallet)
    wallet.handle_text(USER, "52")
    wallet.handle_callback(USER, "exp:yes")
    balance = wallet.handle_command(USER, "/balance").text
    assert "Домашняя валюта: 195 000.00 RUB" in balance
    assert "Начальная сумма: 200 000.00 RUB" in balance
    assert "52.00 EUR = 5 000.00 RUB" in wallet.handle_command(USER, "/history").text


# --- модуль API ---

def _response(json_data, status=200):
    resp = MagicMock(status_code=status)
    resp.json.return_value = json_data
    return resp


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("CURRENCY_ACCESS_KEY", "test-key")


def test_convert_currency_parses_result(api_key):
    data = {"success": True, "info": {"quote": 0.86}, "result": 86.0}
    with patch("current_api.requests.get", return_value=_response(data)) as get:
        assert current_api.convert_currency(100, "usd", "eur") == {"result": 86.0, "rate": 0.86}
    params = get.call_args.kwargs["params"]
    assert params["access_key"] == "test-key" and params["from"] == "USD"


def test_pair_rate_falls_back_to_live(api_key):
    restricted = {"success": False, "error": {"code": 105, "type": "function_access_restricted"}}
    live = {"success": True, "source": "USD", "quotes": {"USDRUB": 80.0, "USDEUR": 0.8}}
    with patch("current_api.requests.get", side_effect=[_response(restricted), _response(live)]):
        assert current_api.get_pair_rate("RUB", "EUR") == pytest.approx(0.01)


def test_invalid_key_message(api_key):
    data = {"success": False, "error": {"code": 101, "type": "invalid_access_key"}}
    with patch("current_api.requests.get", return_value=_response(data)):
        with pytest.raises(CurrencyAPIError, match="Ключ API не подошёл"):
            current_api.get_current_rate("USD", ["EUR"])


def test_network_error_hides_key(api_key):
    err = requests.ConnectionError("https://api.exchangerate.host/live?access_key=test-key")
    with patch("current_api.requests.get", side_effect=err):
        with pytest.raises(CurrencyAPIError) as info:
            current_api.get_current_rate("USD", ["EUR"])
    assert "test-key" not in str(info.value)


def test_missing_key(monkeypatch):
    monkeypatch.setenv("CURRENCY_ACCESS_KEY", "")
    with pytest.raises(CurrencyAPIError, match="CURRENCY_ACCESS_KEY"):
        current_api.get_current_rate()


def test_rate_limit_is_retried(api_key, monkeypatch):
    monkeypatch.setattr(current_api, "RATE_LIMIT_PAUSE", 0)
    limited = {"success": False, "error": {"code": 106, "type": "rate_limit_reached"}}
    ok = {"success": True, "info": {"quote": 0.9}, "result": 90.0}
    with patch("current_api.requests.get", side_effect=[_response(limited), _response(ok)]):
        assert current_api.convert_currency(100, "USD", "EUR")["result"] == 90.0


def test_rate_limit_gives_friendly_error(api_key, monkeypatch):
    monkeypatch.setattr(current_api, "RATE_LIMIT_PAUSE", 0)
    limited = {"success": False, "error": {"code": 106, "type": "rate_limit_reached"}}
    with patch("current_api.requests.get", return_value=_response(limited)):
        with pytest.raises(CurrencyAPIError, match="слишком частые запросы"):
            current_api.convert_currency(100, "USD", "EUR")
