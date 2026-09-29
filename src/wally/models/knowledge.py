"""Knowledge domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class KnowledgeAssetType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    PRINCIPLE = "principle"
    SOP = "sop"
    CHECKLIST = "checklist"
    PLAYBOOK = "playbook"
    TEMPLATE = "template"
    REFERENCE = "reference"
    UNSPECIFIED = "unspecified"


class KnowledgeClass(StrEnum):
    PENDING = "pending"
    OPERATIONAL = "operational"
    GOVERNANCE = "governance"


@dataclass
class KnowledgeAsset:
    """A unit of personal knowledge Wally can retrieve and reason about."""

    id: str
    title: str
    content: str
    database: str
    role: str
    knowledge_class: KnowledgeClass = KnowledgeClass.OPERATIONAL
    asset_type: KnowledgeAssetType = KnowledgeAssetType.REFERENCE
    domain: str | None = None
    url: str | None = None
    last_edited: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass
class KnowledgeRetrievalResult:
    assets: list[KnowledgeAsset]
    query: str
