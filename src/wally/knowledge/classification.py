"""Heuristic classification recommendations for discovered databases."""

from __future__ import annotations

import json
from dataclasses import dataclass

from wally.models.knowledge import KnowledgeClass

_GOVERNANCE_KEYWORDS = (
    "governance",
    "policy",
    "policies",
    "constitution",
    "principle",
    "principles",
    "sop",
    "standard operating",
    "playbook",
    "guideline",
    "guidelines",
    "operating procedure",
)

_STRONG_GOVERNANCE_KEYWORDS = ("governance", "constitution", "policy", "principles")


@dataclass(frozen=True)
class ClassificationRecommendation:
    classification: KnowledgeClass
    reasoning: str
    signals: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(
            {
                "classification": self.classification.value,
                "reasoning": self.reasoning,
                "signals": list(self.signals),
            }
        )

    @classmethod
    def from_json(cls, raw: str | None) -> ClassificationRecommendation | None:
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        try:
            classification = KnowledgeClass(data["classification"])
        except (KeyError, ValueError):
            return None
        return cls(
            classification=classification,
            reasoning=str(data.get("reasoning", "")),
            signals=tuple(data.get("signals", [])),
        )


def recommend_classification(*, name: str, description: str = "") -> ClassificationRecommendation:
    """Suggest operational vs governance from database metadata (advisory only)."""
    text = f"{name} {description}".lower()
    signals = tuple(kw for kw in _GOVERNANCE_KEYWORDS if kw in text)
    strong = any(kw in text for kw in _STRONG_GOVERNANCE_KEYWORDS)

    if strong or len(signals) >= 2:
        signal_text = ", ".join(signals) if signals else "governance-related naming"
        return ClassificationRecommendation(
            classification=KnowledgeClass.GOVERNANCE,
            reasoning=(
                f"Database '{name}' looks like governance knowledge ({signal_text}). "
                "Governance databases define how Wally operates and should be read-only."
            ),
            signals=signals,
        )

    if signals:
        signal_text = ", ".join(signals)
        return ClassificationRecommendation(
            classification=KnowledgeClass.OPERATIONAL,
            reasoning=(
                f"Database '{name}' contains governance-adjacent terms ({signal_text}) "
                "but appears to be working operational knowledge. Review before approving."
            ),
            signals=signals,
        )

    return ClassificationRecommendation(
        classification=KnowledgeClass.OPERATIONAL,
        reasoning=(
            f"Database '{name}' has no strong governance signals. "
            "Default recommendation is operational (readable and writable once approved)."
        ),
        signals=(),
    )
