"""Verification result types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class VerificationStatus(StrEnum):
    VERIFIED = "verified"
    MISSING = "missing"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VerificationCheck:
    field: str
    status: VerificationStatus
    message: str
    critical: bool = False


@dataclass(frozen=True)
class VerificationReport:
    checks: tuple[VerificationCheck, ...]
    blocked: bool
    block_reason: str | None = None

    def critical_mismatches(self) -> tuple[VerificationCheck, ...]:
        return tuple(
            check
            for check in self.checks
            if check.critical and check.status == VerificationStatus.MISMATCH
        )
