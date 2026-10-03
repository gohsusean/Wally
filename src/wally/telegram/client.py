"""Telegram Bot API client. The token stays in this object and is not logged."""

from __future__ import annotations

import json
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen


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
        body = self._post("getUpdates", {"offset": offset, "timeout": 0})
        result = body.get("result")
        return result if isinstance(result, list) else []

    def _post(self, method: str, payload: dict) -> dict:
        if not self._token:
            raise RuntimeError("Telegram bot token is missing.")
        request = Request(
            f"https://api.telegram.org/bot{self._token}/{method}",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:  # noqa: S310
                parsed = json.loads(response.read().decode())
        except URLError as exc:
            raise TimeoutError("Telegram network error") from exc
        if not isinstance(parsed, dict) or not parsed.get("ok"):
            raise TimeoutError("Telegram rejected the call")
        return parsed
