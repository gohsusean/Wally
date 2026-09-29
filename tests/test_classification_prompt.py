"""Classification prompt tests."""

from wally.cli_knowledge import parse_classification_answer
from wally.models.knowledge import KnowledgeClass


def test_accept_recommendation() -> None:
    assert (
        parse_classification_answer("", recommended=KnowledgeClass.OPERATIONAL)
        == KnowledgeClass.OPERATIONAL
    )
    assert (
        parse_classification_answer("y", recommended=KnowledgeClass.GOVERNANCE)
        == KnowledgeClass.GOVERNANCE
    )


def test_reject_recommendation_flips() -> None:
    assert (
        parse_classification_answer("n", recommended=KnowledgeClass.OPERATIONAL)
        == KnowledgeClass.GOVERNANCE
    )
    assert (
        parse_classification_answer("no", recommended=KnowledgeClass.GOVERNANCE)
        == KnowledgeClass.OPERATIONAL
    )


def test_explicit_governance_or_operational() -> None:
    assert parse_classification_answer("g", recommended=KnowledgeClass.OPERATIONAL) == (
        KnowledgeClass.GOVERNANCE
    )
    assert parse_classification_answer("operational", recommended=KnowledgeClass.GOVERNANCE) == (
        KnowledgeClass.OPERATIONAL
    )


def test_skip() -> None:
    assert parse_classification_answer("skip", recommended=KnowledgeClass.OPERATIONAL) is None
    assert parse_classification_answer("s", recommended=KnowledgeClass.GOVERNANCE) is None
