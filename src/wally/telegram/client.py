"""Telegram Bot API client. The token stays in this object and is not logged."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramTransientError(TimeoutError):
    """Network, timeout, or Telegram 5xx. The poller retries without exiting."""


class TelegramTransport(Protocol):
    def send_message(
        self, chat_id: str, text: str, buttons: list[dict[str, str]] | None = None
    ) -> str: ...

    def answer_callback(self, callback_id: str, text: str) -> None: ...

    def get_updates(self, offset: int) -> list[dict]: ...


class BotClient:
    def __init__(self, token: str) -> None:
        self._token = token

    def send_message(
        self, chat_id: str, text: str, buttons: list[dict[str, str]] | None = None
    ) -> str:
        payload: dict = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": [buttons]}
        body = self._post("sendMessage", payload)
        return str(body.get("result", {}).get("message_id", ""))

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:180]})

    def get_updates(self, offset: int) -> list[dict]:
        body = self._post(
            "getUpdates",
            {
                "offset": offset,
                "timeout": 25,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=40,
        )
        result = body.get("result")
        return result if isinstance(result, list) else []

    def _post(self, method: str, payload: dict, timeout: float = 30) -> dict:
        if not self._token:
            raise RuntimeError("Telegram bot token is missing.")
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:  # noqa: S310
                status = getattr(response, "status", 200)
                parsed = json.loads(response.read().decode())
        except HTTPError as exc:
            status = exc.code
            parsed = {}
        except (URLError, TimeoutError) as exc:
            raise TelegramTransientError("Telegram network error") from exc
        if not isinstance(parsed, dict):
            raise TelegramTransientError("Telegram returned an unreadable response.")
        error_code = parsed.get("error_code")
        unavailable = status == 429 or status >= 500
        if isinstance(error_code, int) and (error_code == 429 or error_code >= 500):
            unavailable = True
        if unavailable:
            raise TelegramTransientError("Telegram is unavailable.")
        if status >= 400 or not parsed.get("ok"):
            raise RuntimeError("Telegram rejected the call.")
        return parsed
