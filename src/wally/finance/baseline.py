"""Owner's administrative coverage policy; never transaction settlement proof."""

from datetime import date

from wally.models.ops import ObservationCategory


class FinancialBaseline:
    def __init__(self, store):
        self.store = store

    def allows(self, observation):
        if observation.category not in {
            ObservationCategory.INVOICE,
            ObservationCategory.KNOWLEDGE_OBLIGATION,
        }:
            return True
        # Unbound invoice/reminder text cannot reopen covered obligations, even when
        # delivered in 2027. Retain it as evidence until a canonical period is known.
        if observation.source != "finance" or not observation.trusted:
            return False
        try:
            period_start = date.fromisoformat(observation.extra.get("period_start", ""))
        except ValueError:
            return False
        if period_start >= date(2027, 1, 1):
            return True
        with self.store._connect() as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='payment_baseline_exceptions'"
            ).fetchone()
            if not exists:
                return False
            return (
                c.execute(
                    "SELECT 1 FROM payment_baseline_exceptions "
                    "WHERE instance_id=? AND catalog_binding=?",
                    (observation.source_id, observation.extra.get("finance_binding", "")),
                ).fetchone()
                is not None
            )
