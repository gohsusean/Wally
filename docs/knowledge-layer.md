# Wally — Knowledge Layer Architecture Proposal

**Version:** 0.1 (proposal)  
**Status:** Historical proposal — superseded by Architecture Review v2 and later ADRs
**Date:** 2026-06-27  
**Context:** Pre-v0.4 architectural review — refactor `MemoryProvider` → `KnowledgeProvider`

Retained as history. See [Architecture Review v2](architecture-review-v2.md),
ADR-019/020/021/023 in [decisions](decisions.md), and [current architecture](current-architecture.md).
Embeddings, federation and retriever/store extraction below are proposals, not
current implementations; no review of this old plan is required before routine work.

---

## Executive summary

The v0.3 implementation works, but the name **Memory** understates what Wally is doing and will not scale as we add asset types, retrieval strategies, and additional backends.

**Recommendation:** Adopt **Knowledge** as the domain language. Introduce **Knowledge Assets** as the first-class unit. Split read and write concerns lightly (retriever vs store), but **do not** introduce new infrastructure yet.

This is primarily a **rename + model enrichment + retrieval vocabulary** change — not a rewrite. Estimated effort: one focused PR (~300 lines touched, mostly renames).

---

## 1. Review of current implementation

### What exists today (v0.3)

```
Orchestrator
    → ToolRegistry (provider="memory")
        → MemoryProvider (protocol)
            → NotionMemoryAdapter (REST)
                → Notion pages / databases
```

| Component | File | Responsibility |
|-----------|------|----------------|
| Protocol | `providers/memory.py` | CRUD + search + tool definitions |
| Model | `models/memory.py` | `MemoryEntry`, `MemorySearchResult` |
| Adapter | `adapters/notion/adapter.py` | Notion REST; maps pages → entries |
| Tools | `memory_search`, `memory_get`, `memory_store`, `memory_update`, `memory_delete` | LLM-facing API |
| Config | `providers.memory`, `config/notion.yaml` | Enable flag + database mapping |
| Safety | `classifier.py` rules for `memory.*` | Read/write/delete classification |

### What is already good (keep unchanged)

1. **Provider pattern** — orchestrator depends on a protocol, not Notion. This was the right call.
2. **Tool registration via `ToolRegistry`** — safety gates sit outside the adapter.
3. **Adapter isolation** — Notion entity IDs never appear in orchestrator code.
4. **Config-driven database mapping** — `notion.yaml` is adapter config, not business logic.
5. **Approval gate on delete** — destructive knowledge operations are gated.
6. **Audit logging** — every tool execution is recorded.
7. **Explicit provider failure** — Wally does not hallucinate when Notion is down.

### What is conceptually wrong

| Issue | Example | Why it matters |
|-------|---------|----------------|
| **"Memory" is overloaded** | Conversation history (SQLite) vs Notion pages both called "memory" in docs | Future engineers will conflate session state with knowledge assets |
| **Page-centric model** | `MemoryEntry` is a Notion page with title + content | SOPs, checklists, and principles are not "memories" |
| **No asset semantics** | `role` = database routing (`general`, `financial`) | Not the same as knowledge type (`SOP`, `Fact`) |
| **Search = retrieval** | `memory_search` returns pages matching keywords | Retrieval should return *relevant knowledge assets* ranked by intent |
| **Tool names encode old vocabulary** | `memory_store`, `memory_delete` | LLM prompts and audit logs perpetuate the wrong abstraction |
| **Provider name in orchestrator** | `provider="memory"` hardcoded in audit/safety | Should be `knowledge` as the capability domain |
| **Adapter class name** | `NotionMemoryAdapter` | Implies memory is Notion-specific at the domain level |

### What does *not* need to change

- OpenAI tool-calling loop in orchestrator
- Safety classifier pattern (rules by provider + action)
- `ApprovalProvider` / CLI approval
- Session persistence (`SessionStore`) — this is **conversation state**, not knowledge
- Home Assistant, workflow, and future providers
- JSON Lines audit log
- `config/notion.yaml` structure (adapter-specific; can stay)

---

## 2. Proposed architecture

### Target mental model

```
User question
     ↓
Orchestrator          ← reasons about intent, plans retrieval, composes answer
     ↓
KnowledgeProvider     ← capability facade: health, tools, policy
     ↓
KnowledgeRetriever    ← read path: find relevant assets (today: search + rank)
     ↓
KnowledgeStore        ← write path: create, update, archive (optional split)
     ↓
Knowledge Assets      ← domain objects Wally reasons about
     ↓
Reasoning Provider    ← LLM (already exists as LLMProvider)
```

**Important:** The orchestrator requests **knowledge**, never **pages**, **databases**, or **Notion**.

### Layer responsibilities

#### KnowledgeProvider (capability — orchestrator sees this)

The stable contract. Registers tools. Reports health. Delegates to retriever/store.

```python
# Conceptual — not yet implemented
class KnowledgeProvider(Protocol):
    def is_healthy(self) -> bool: ...
    def tool_definitions(self) -> list[dict]: ...
    def execute_tool(self, name: str, arguments: dict) -> str: ...
```

Same shape as today’s `MemoryProvider`. Minimal churn: **rename + enrich return types**.

#### KnowledgeRetriever (read path — internal initially)

Responsible for: *given an intent, return the most relevant Knowledge Assets.*

```python
class KnowledgeRetriever(Protocol):
    def retrieve(
        self,
        query: str,
        *,
        types: list[KnowledgeAssetType] | None = None,
        limit: int = 10,
    ) -> KnowledgeRetrievalResult: ...
```

**v0.3.1 implementation:** Notion keyword search + optional type filter from a Notion property.  
**v0.7 implementation:** Local embeddings, multi-query expansion, reranking.

The retriever lives inside `adapters/notion/` initially. Extract to `providers/knowledge_retriever.py` only when a second backend appears.

#### KnowledgeStore (write path — internal initially)

```python
class KnowledgeStore(Protocol):
    def create(self, asset: KnowledgeAssetDraft) -> KnowledgeAsset: ...
    def update(self, asset_id: str, changes: KnowledgeAssetDraft) -> KnowledgeAsset: ...
    def archive(self, asset_id: str) -> None: ...
```

Not exposed as a separate orchestrator concern. Writes still go through `KnowledgeProvider` tools with safety gates.

#### Reasoning Provider

Already exists as `LLMProvider`. No change. The LLM decides *what* to retrieve; the retriever decides *how* to find it.

### Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                      ORCHESTRATOR                           │
│  • Assembles session context                                │
│  • Exposes knowledge tools to LLM                           │
│  • Runs tool loop → safety gates → audit                    │
└──────────────────────────┬──────────────────────────────────┘
                           │ KnowledgeProvider (protocol)
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                   KnowledgeProvider                           │
│  • tool_definitions() / execute_tool()                        │
│  • is_healthy()                                             │
└───────────────┬─────────────────────────────┬───────────────┘
                │                             │
                ▼                             ▼
┌───────────────────────────┐   ┌───────────────────────────┐
│   KnowledgeRetriever      │   │     KnowledgeStore        │
│   retrieve(query, types)  │   │   create / update / archive│
└───────────────┬───────────┘   └─────────────┬─────────────┘
                │                             │
                └──────────────┬──────────────┘
                               ▼
                ┌───────────────────────────┐
                │   Adapter (Notion REST)     │  ← swappable
                │   Future: MCP, Obsidian,  │
                │   vector store, GDrive      │
                └───────────────────────────┘
                               │
                               ▼
                ┌───────────────────────────┐
                │     Knowledge Assets        │
                │  (Wally domain objects)   │
                └───────────────────────────┘
```

---

## 3. Knowledge Assets

### Definition

A **Knowledge Asset** is a unit of personal knowledge Wally can retrieve, reason about, and (when permitted) modify.

It is **not** a Notion page. A page is one possible *backing representation*.

### KnowledgeAsset (proposed model)

```python
class KnowledgeAssetType(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    PRINCIPLE = "principle"
    SOP = "sop"
    CHECKLIST = "checklist"
    PLAYBOOK = "playbook"
    TEMPLATE = "template"
    REFERENCE = "reference"


@dataclass
class KnowledgeAsset:
    id: str                    # Wally-stable ID (maps to page ID in Notion for now)
    title: str
    content: str               # Normalised body text for reasoning
    asset_type: KnowledgeAssetType
    domain: str | None = None  # Optional: "feedback", "finance", "travel"
    source: str                # "notion" — provenance, not exposed to LLM by default
    url: str | None = None
    last_edited: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)
```

### Type field — what it does (and does not do)

**Does:**
- Guide retrieval filters (`types=[SOP, PRINCIPLE]` for feedback prep)
- Guide LLM reasoning ("this is a checklist — follow sequentially")
- Enable future type-specific formatting

**Does not (yet):**
- Enforce schema validation per type
- Trigger different storage backends
- Require a complex class hierarchy (`SOPAsset`, `FactAsset`, …)

One dataclass + enum is sufficient for years.

### Mapping from Notion

Add a **select** or **multi-select** property in your Notion databases, e.g. `Asset Type`.

```yaml
# config/notion.yaml (adapter mapping — unchanged location)
databases:
  knowledge:
    id: "..."
    readable: true
    writable: true
    title_property: Name
    content_property: Notes
    type_property: Asset Type    # NEW: maps to KnowledgeAssetType
    domain_property: Domain      # OPTIONAL: e.g. "feedback", "finance"
```

If `Asset Type` is missing on a page, default to `reference` (conservative).

---

## 4. Retrieval philosophy

### Principle

> Wally retrieves **Knowledge Assets**, not pages.

The LLM (reasoning provider) formulates *what* knowledge is needed. The retriever finds *which* assets match.

### Example: manager feedback

**User:** "Help me prepare my manager feedback."

**Desired behaviour (no hard-coded relationships):**

1. LLM infers knowledge needs: SOPs, principles, past examples.
2. LLM calls `knowledge_retrieve` (possibly multiple times):
   - `query="manager feedback", types=["sop", "playbook"]`
   - `query="feedback writing principles", types=["principle"]`
   - `query="previous manager feedback", types=["reference", "fact"]`
3. Retriever returns ranked `KnowledgeAsset` list.
4. LLM composes answer, tagging facts vs inferences.

**No graph database required.** Multi-query retrieval driven by LLM intent is sufficient for v0.3–v0.7.

### Tool surface (proposed — fewer, clearer)

| Current (v0.3) | Proposed | Notes |
|----------------|----------|-------|
| `memory_search` | `knowledge_retrieve` | Primary read tool; supports `types` filter |
| `memory_get` | `knowledge_get` | Fetch by ID after retrieve |
| `memory_store` | `knowledge_create` | Create new asset |
| `memory_update` | `knowledge_update` | Update existing asset |
| `memory_delete` | `knowledge_archive` | Softer verb; still requires approval |

Avoid adding `knowledge_retrieve_related` until we have evidence it's needed. Multiple `knowledge_retrieve` calls are fine.

### Retrieval evolution

| Version | Strategy |
|---------|----------|
| v0.3 (today) | Notion full-text search, filter to configured databases |
| v0.3.1 (refactor) | + asset type filter from Notion property |
| v0.7 | + local embeddings, semantic rank, session consolidation |
| Post-v1.0 | Hybrid: vector + keyword + metadata; optional cross-source federation |

---

## 5. Future-proofing

### What stays stable as backends change

| Stable (Wally domain) | Swappable (adapter) |
|-----------------------|---------------------|
| `KnowledgeAsset` | Notion page structure |
| `KnowledgeAssetType` | Notion property names |
| `KnowledgeProvider` protocol | REST vs MCP vs file system |
| `knowledge_*` tool schemas | Search implementation |
| Safety rules on `knowledge.*` | Rate limits, auth |

### Adding a new source (e.g. Obsidian)

1. Implement `KnowledgeRetriever` + `KnowledgeStore` for Obsidian.
2. Wrap in `ObsidianKnowledgeProvider` implementing `KnowledgeProvider`.
3. Register in config: `providers.knowledge.adapter: obsidian`.
4. **No orchestrator changes.**

### Federation (multiple sources at once)

Defer until needed. When required:

```python
class FederatedKnowledgeProvider:
    """Merges results from multiple retrievers, deduplicates by title/similarity."""
```

Orchestrator still sees one `KnowledgeProvider`.

### Terminology guardrail

| Term | Meaning | Do not use for |
|------|---------|----------------|
| **Knowledge** | Personal knowledge assets (Notion, etc.) | Conversation history |
| **Session** | Conversation state in SQLite | Notion pages |
| **Memory** (deprecated) | — | New code or docs |
| **Reasoning** | LLM inference | Storage/retrieval |

---

## 6. Migration plan (minimal)

### Phase 1 — Rename and enrich (recommended now, before v0.4)

**Goal:** Correct vocabulary without behaviour change.

| Change | From | To |
|--------|------|----|
| Protocol | `MemoryProvider` | `KnowledgeProvider` |
| Model file | `models/memory.py` | `models/knowledge.py` |
| Model | `MemoryEntry` | `KnowledgeAsset` |
| Model | `MemorySearchResult` | `KnowledgeRetrievalResult` |
| Adapter class | `NotionMemoryAdapter` | `NotionKnowledgeAdapter` |
| Factory | `create_memory_provider` | `create_knowledge_provider` |
| Config key | `providers.memory` | `providers.knowledge` |
| Settings | `memory_enabled` | `knowledge_enabled` |
| Prompt | `prompts/capabilities/memory_v1.md` | `prompts/capabilities/knowledge_v1.md` |
| Tools | `memory_*` | `knowledge_*` |
| Safety rules | `("memory", ...)` | `("knowledge", ...)` |
| Audit provider | `"memory"` | `"knowledge"` |

**Add:**
- `KnowledgeAssetType` enum
- `asset_type` field on `KnowledgeAsset` (default `reference` until Notion property mapped)

**Keep (no rename):**
- `config/notion.yaml` — adapter-specific
- `SessionStore` — conversation history
- `data/sessions.db` — not knowledge
- Roadmap version labels can say "v0.3.1 knowledge refactor"

**Backward compatibility (optional, one release):**
- Accept `providers.memory.enabled` in config with deprecation warning
- Alias old tool names in safety classifier

**Estimated diff:** ~25 files, mostly mechanical renames. No logic rewrite.

### Phase 2 — Retriever extraction (when second backend is planned)

1. Define `KnowledgeRetriever` protocol in `providers/knowledge_retriever.py`.
2. `NotionKnowledgeAdapter` composes `NotionRetriever` + `NotionStore`.
3. `KnowledgeProvider` delegates to them.

**Do not do this in Phase 1** unless you enjoy refactoring for its own sake.

### Phase 3 — Notion schema + smart retrieval (v0.7 overlap)

1. Add `type_property` to `config/notion.yaml`.
2. Implement type filter in retriever.
3. Add embedding-based rank (local SQLite + vectors).

---

## 7. Recommended naming (canonical)

### Domain (Wally-owned)

```
KnowledgeAsset
KnowledgeAssetType
KnowledgeAssetDraft          # for creates/updates (optional, can wait)
KnowledgeRetrievalResult
KnowledgeProvider            # capability protocol
KnowledgeRetriever           # read protocol (phase 2)
KnowledgeStore               # write protocol (phase 2)
```

### Tools (LLM-facing)

```
knowledge_retrieve
knowledge_get
knowledge_create
knowledge_update
knowledge_archive
```

### Files (proposed layout after Phase 1)

```
src/wally/
├── models/
│   └── knowledge.py              # was memory.py
├── providers/
│   ├── knowledge.py              # was memory.py
│   └── knowledge_retriever.py    # phase 2
├── adapters/
│   └── notion/
│       └── knowledge.py          # was adapter.py (optional rename)
config/
├── notion.yaml                   # unchanged — adapter config
prompts/capabilities/
└── knowledge_v1.md               # was memory_v1.md
```

### Config (proposed)

```yaml
providers:
  knowledge:
    adapter: notion
    enabled: true

prompts:
  knowledge: prompts/capabilities/knowledge_v1.md
```

---

## 8. Repository changes summary

| Action | Files | Phase |
|--------|-------|-------|
| Rename | `providers/memory.py` → `knowledge.py` | 1 |
| Rename | `models/memory.py` → `knowledge.py` | 1 |
| Rename | `tests/mock_memory.py` → `mock_knowledge.py` | 1 |
| Update | `orchestrator/tools.py`, `core.py`, `app.py` | 1 |
| Update | `safety/classifier.py` | 1 |
| Update | `config/macbook.yaml`, `macmini.yaml` | 1 |
| Update | `docs/architecture.md`, `roadmap.md`, ADR-009 | 1 |
| Add | `docs/knowledge-layer.md` (this document) | 1 |
| Add | ADR-019: Knowledge layer vocabulary | 1 |
| Extract | `KnowledgeRetriever` protocol | 2 |
| Add | `type_property` in notion.yaml | 2–3 |

**No new dependencies in Phase 1.**

---

## 9. Concerns and trade-offs

### Concerns with this direction

1. **Rename churn** — Touches many files. Mitigation: do it now while the codebase is small (~900 lines of src).

2. **"Knowledge" is still broad** — True, but it's more accurate than "memory" and matches how you'll describe Wally to future you.

3. **Session vs knowledge confusion in docs** — Roadmap "v0.7 long-term memory" should be renamed **"v0.7 contextual recall"** or **"conversation intelligence"** to avoid clashing with Knowledge Assets.

4. **Smart retrieval expectations** — Multi-asset discovery for feedback prep requires the **LLM to plan multiple retrievals**. The retriever won't magically find related SOPs without query/type hints until v0.7 embeddings.

5. **Notion schema discipline** — Asset types only work if you maintain `Asset Type` in Notion. Undisciplined pages default to `reference` — acceptable but reduces retrieval quality.

6. **Premature Retriever/Store split** — Extracting protocols too early adds files without benefit. Keep inside Notion adapter until backend #2.

### Alternatives considered

| Alternative | Why not |
|-------------|---------|
| Keep `MemoryProvider`, add `KnowledgeAsset` only | Perpetuates wrong vocabulary in tools, audit, safety |
| `DocumentProvider` | Too generic; loses semantic intent |
| Per-type provider (`SOPProvider`, …) | Over-engineered; one provider with type filter is enough |
| Graph-based knowledge relationships | Years away; LLM multi-query retrieval is sufficient |
| Big-bang rewrite with vector store now | Violates "smallest possible refactor" |

---

## 10. Recommendation

**Approve Phase 1 before v0.4.** Rename Memory → Knowledge, introduce `KnowledgeAsset` + `KnowledgeAssetType`, rename tools, update docs. Behaviour unchanged. One PR.

**Defer Phase 2** until Obsidian, MCP, or a second backend is on the roadmap.

**Plan Phase 3** alongside v0.7 conversation intelligence — semantic retrieval is the natural home for embeddings.

---

## 11. Decision requested

Please confirm:

1. **Phase 1 rename** — proceed before Home Assistant (v0.4)?
2. **Tool names** — `knowledge_retrieve` / `knowledge_archive` acceptable?
3. **Notion property** — will you add `Asset Type` (select) to your databases?
4. **Roadmap wording** — rename "v0.7 long-term memory" to "conversation intelligence"?

Once approved, implementation is a single mechanical refactor PR with no new capabilities.
