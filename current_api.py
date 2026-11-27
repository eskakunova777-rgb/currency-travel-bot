"""Модуль работы с API exchangerate.host.

Все запросы идут на api.exchangerate.host с параметром access_key.
Ключ берётся из переменной окружения CURRENCY_ACCESS_KEY (файл .env).
"""

import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

DEFAULT_BASE_URL = "https://api.exchangerate.host"


class CurrencyAPIError(Exception):
    """Понятная ошибка API: текст можно показать пользователю."""


def _base_url() -> str:
    return os.getenv("CURRENCY_API_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout() -> float:
    try:
        return float(os.getenv("CURRENCY_API_TIMEOUT", "10"))
    except ValueError:
        return 10.0


def _access_key() -> str:
    key = os.getenv("CURRENCY_ACCESS_KEY", "").strip()
    if not key:
        raise CurrencyAPIError(
            "Не задан ключ API. Впишите CURRENCY_ACCESS_KEY в файл .env."
        )
    return key


RATE_LIMIT_RETRIES = 3
RATE_LIMIT_PAUSE = 1.5  # секунды; бесплатный тариф не любит запросы подряд


def _is_rate_limited(data: dict) -> bool:
    error = data.get("error") or {}
    return data.get("success") is False and (
        error.get("code") == 106 or "rate_limit" in str(error.get("type", ""))
        or "rate limitation" in str(error.get("info", ""))
    )


def _request(endpoint: str, params: dict) -> dict:
    """GET-запрос к API с повтором при ограничении частоты запросов."""
    for attempt in range(RATE_LIMIT_RETRIES):
        data, status = _request_once(endpoint, params)
        if not _is_rate_limited(data):
            break
        if attempt < RATE_LIMIT_RETRIES - 1:
            time.sleep(RATE_LIMIT_PAUSE * (attempt + 1))
    return _check(data, status)


def _request_once(endpoint: str, params: dict) -> tuple[dict, int]:
    """Один GET-запрос. Ключ в тексте ошибок не показывается."""
    query = {"access_key": _access_key(), **params}
    url = f"{_base_url()}/{endpoint}"
    try:
        response = requests.get(url, params=query, timeout=_timeout())
    except requests.Timeout:
        raise CurrencyAPIError("Сервис курсов валют не ответил вовремя. Попробуйте позже.") from None
    except requests.RequestException:
        # Текст исключения requests содержит URL с ключом, поэтому его не выводим.
        raise CurrencyAPIError("Нет связи с сервисом курсов валют. Проверьте интернет.") from None

    try:
        data = response.json()
    except ValueError:
        raise CurrencyAPIError(
            f"Сервис курсов валют вернул непонятный ответ (HTTP {response.status_code})."
        ) from None

    if not isinstance(data, dict):
        raise CurrencyAPIError("Сервис курсов валют вернул непонятный ответ.")
    return data, response.status_code


def _check(data: dict, status: int) -> dict:
    """Переводит ошибки API в понятные сообщения."""
    if data.get("success") is False:
        error = data.get("error") or {}
        code = error.get("code")
        if code in (101, "invalid_access_key", "missing_access_key"):
            raise CurrencyAPIError("Ключ API не подошёл. Проверьте CURRENCY_ACCESS_KEY в .env.")
        if code == 104:
            raise CurrencyAPIError("Закончился месячный лимит запросов к API.")
        if _is_rate_limited(data):
            raise CurrencyAPIError("Сервис просит подождать: слишком частые запросы. Попробуйте через минуту.")
        if code in (105, "function_access_restricted"):
            raise CurrencyAPIError("Эта функция API недоступна на бесплатном тарифе.")
        if code in (201, 202, 401, 402):
            raise CurrencyAPIError("Такую валюту сервис не знает.")
        info = error.get("info") or error.get("type") or "неизвестная ошибка"
        raise CurrencyAPIError(f"Ошибка API: {info}")

    if status >= 400:
        raise CurrencyAPIError(f"Сервис курсов валют ответил ошибкой HTTP {status}.")
    return data


def get_current_rate(default: str = "USD", currencies: list[str] | None = None) -> dict:
    """Текущие курсы: {"USDEUR": 0.85, ...} относительно валюты default."""
    currencies = currencies or ["EUR", "GBP", "JPY"]
    data = _request("live", {"source": default.upper(), "currencies": ",".join(c.upper() for c in currencies)})
    quotes = data.get("quotes")
    if not quotes:
        raise CurrencyAPIError("Сервис не прислал курсы для этих валют.")
    return quotes


def convert_currency(amount: float, from_currency: str, to_currency: str) -> dict:
    """Конвертация через endpoint /convert.

    Возвращает {"result": сумма в валюте назначения, "rate": курс 1 from = rate to}.
    """
    data = _request(
        "convert",
        {"from": from_currency.upper(), "to": to_currency.upper(), "amount": amount},
    )
    result = data.get("result")
    rate = (data.get("info") or {}).get("quote") or (data.get("info") or {}).get("rate")
    if result is None:
        raise CurrencyAPIError("Сервис не прислал результат конвертации.")
    if rate is None:
        rate = result / amount if amount else 0
    return {"result": float(result), "rate": float(rate)}


def get_pair_rate(from_currency: str, to_currency: str) -> float:
    """Курс пары: сколько to_currency дают за 1 from_currency."""
    from_currency, to_currency = from_currency.upper(), to_currency.upper()
    if from_currency == to_currency:
        return 1.0
    try:
        return convert_currency(1, from_currency, to_currency)["rate"]
    except CurrencyAPIError as error:
        if "бесплатном тарифе" not in str(error):
            raise
    # Запасной путь: кросс-курс через USD по /live (доступен на бесплатном тарифе).
    quotes = get_current_rate("USD", [from_currency, to_currency])
    usd_from = 1.0 if from_currency == "USD" else quotes.get(f"USD{from_currency}")
    usd_to = 1.0 if to_currency == "USD" else quotes.get(f"USD{to_currency}")
    if not usd_from or not usd_to:
        raise CurrencyAPIError("Сервис не знает курс для этой пары валют.")
    return usd_to / usd_from


def get_all_supported_currencies() -> dict:
    """Список валют, которые знает сервис: {"USD": "United States Dollar", ...}."""
    data = _request("list", {})
    return data.get("currencies", {})


if __name__ == "__main__":
    # Быстрая ручная проверка: python current_api.py
    try:
        print("1 RUB =", round(get_pair_rate("RUB", "EUR"), 6), "EUR")
        print("100 USD =", convert_currency(100, "USD", "EUR")["result"], "EUR")
    except CurrencyAPIError as error:
        print("Ошибка:", error)
