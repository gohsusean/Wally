"""Telegram adapter. Long polling, one owner, decisions on server-side nonces."""

from wally.telegram.service import TelegramConfig, TelegramService

__all__ = ["TelegramConfig", "TelegramService"]
