"""Deterministic sanitization of external content before reasoning.

Future: return structured removal metadata and audit-log suspicious strips
(e.g. injection pattern hits) so debugging can surface
"this webpage contained possible prompt-injection text".
"""

from __future__ import annotations

import re

# Obvious prompt-injection and tool-instruction patterns (defense in depth only).
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore (all )?(previous|prior|above) instructions", re.IGNORECASE),
    re.compile(r"disregard (your|the) (system|safety|policy)", re.IGNORECASE),
    re.compile(r"you are now (in )?(developer|admin|root|sudo) mode", re.IGNORECASE),
    re.compile(r"override (your|the) (policy|safety|rules)", re.IGNORECASE),
    re.compile(r"call (the )?tool[s]?\s*[:=]", re.IGNORECASE),
    re.compile(r"execute (this|the following) (command|action|tool)", re.IGNORECASE),
    re.compile(r"<\s*/?\s*script", re.IGNORECASE),
    re.compile(r"\{\{\s*tool_call", re.IGNORECASE),
)

_ZERO_WIDTH = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]")

_BLOCK_TAGS = (
    "script",
    "style",
    "noscript",
    "svg",
    "iframe",
    "object",
    "embed",
    "meta",
    "link",
    "head",
)


def strip_html_boilerplate(html: str) -> str:
    """Remove scripts, styles, and markup; return plain text."""
    text = html
    for tag in _BLOCK_TAGS:
        text = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            " ",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_html_title(html: str) -> str:
    """Extract document title from raw HTML."""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def remove_injection_phrases(text: str) -> str:
    """Strip obvious adversarial instruction patterns from external text."""
    cleaned = text
    for pattern in _INJECTION_PATTERNS:
        cleaned = pattern.sub("[removed]", cleaned)
    return cleaned


def normalize_whitespace(text: str) -> str:
    """Collapse whitespace and remove zero-width characters."""
    without_zero_width = _ZERO_WIDTH.sub("", text)
    return re.sub(r"\s+", " ", without_zero_width).strip()


def sanitize_external_text(text: str, *, max_chars: int | None = None) -> str:
    """Normalize external plain text before it reaches the Reasoning Provider."""
    cleaned = normalize_whitespace(remove_injection_phrases(text))
    if max_chars is not None and len(cleaned) > max_chars:
        return cleaned[:max_chars]
    return cleaned


def sanitize_external_html(html: str, *, max_chars: int | None = None) -> str:
    """Strip HTML boilerplate and sanitize text from a fetched page."""
    return sanitize_external_text(strip_html_boilerplate(html), max_chars=max_chars)
