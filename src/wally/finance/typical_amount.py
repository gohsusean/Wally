"""Advisory payment comparison; never an invoice amount or payment policy."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext


@dataclass(frozen=True)
class TypicalAmount:
    amount: str = ""
    currency: str = ""
    binding: str = ""  # Exact definition, stable mapping and observed value.

    def notice(self, proposed: str, currency: str) -> str:
        if self.amount == "":
            return ""
        unavailable = "Typical Amount comparison unavailable (advisory only)."
        try:
            typical, amount = Decimal(self.amount), Decimal(proposed)
            if (
                not typical.is_finite() or typical <= 0 or not amount.is_finite()
                or self.currency != currency
                or len(self.amount) > 100 or len(proposed) > 100
                or abs(typical.adjusted()) > 100 or abs(amount.adjusted()) > 100
            ):
                return unavailable
            # Enough precision for the complete bounded inputs and their difference.
            # Threshold comparison is unrounded; percentage is rounded only for display.
            with localcontext() as context:
                context.prec = 400
                difference = amount - typical
                warning = abs(difference) >= Decimal("0.10") * typical
                percent = abs(difference) / typical * Decimal("100")
                direction = "above" if difference > 0 else "below" if difference < 0 else "equal"
                variance = (
                    "equal to typical" if direction == "equal"
                    else f"{percent:.2f}% {direction} typical"
                )
                return (
                    ("⚠ Amount variance warning (advisory only)\n" if warning else "")
                    + f"Typical Amount: {currency} {typical:f}\n"
                    + f"Proposed amount: {currency} {amount:f} · {variance}\n"
                    + "Invoice amount and payment policy still apply."
                )
        except (InvalidOperation, ValueError, TypeError):
            return unavailable
