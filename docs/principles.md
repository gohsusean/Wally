# Wally — Engineering Principles

These principles state the engineering direction, not a claim that every current path fully enforces them. [Current architecture](current-architecture.md) and [engineering debt](engineering-debt.md) distinguish implementation from intent. New privileged capabilities follow ADR-042.

These principles govern every design and implementation decision. When two options conflict, the higher-numbered principle does not automatically win — but any violation must be documented in [decisions.md](decisions.md) with explicit rationale.

---

## 1. Simplicity over cleverness

Prefer boring, readable solutions. A future you (or a future engineer) should understand any module in one sitting.

**In practice:**
- No metaprogramming unless it removes more complexity than it adds.
- No framework churn. Standard library and well-understood dependencies first.
- If a design requires a diagram longer than one page to explain, simplify it.

---

## 2. Modular architecture over monolithic design

Wally is a set of cooperating components with clear boundaries, not a single god-object.

**In practice:**
- Each capability (memory, home automation, workflows) is a separate module behind an interface.
- Components communicate through defined contracts, not shared mutable state.
- New capabilities plug in without modifying the orchestrator's core logic.

---

## 3. Every capability should be replaceable

No subsystem is permanent. Notion, OpenAI, and n8n are current adapters, not permanent commitments. Home Assistant is an unimplemented optional future source, not a current Wally provider.

**In practice:**
- Define provider interfaces before writing adapters.
- Adapters live in isolation; the orchestrator imports interfaces, not implementations.
- Swapping a provider should not require changes to reasoning logic.

---

## 4. Business logic should never depend on a specific LLM

The orchestrator reasons about *what* to do. The LLM is one implementation of *how* to reason.

**In practice:**
- All LLM calls go through an `LLMProvider` interface.
- Prompts are versioned files in `prompts/`, not hard-coded strings scattered through code.
- Tool schemas and action plans are LLM-agnostic data structures.

---

## 5. External systems should be abstracted behind interfaces

The orchestrator depends on provider contracts rather than external SDK calls. For example, Google API details belong in the communications adapter. No HomeAutomationProvider implementation currently exists.

**In practice:**
- One interface per external domain.
- Adapters translate between Wally's domain model and the external API.
- Integration tests mock at the interface boundary.

---

## 6. Security and auditability are first-class concerns

Every action Wally takes must be explainable and reconstructable after the fact.

**In practice:**
- Structured audit log for every provider call.
- Irreversible actions require explicit human approval.
- Financial actions always require approval.
- Data deletion always requires approval.
- Distinguish facts (retrieved data) from inferences (LLM conclusions) in responses.

---

## 7. Wally should remain understandable by one engineer

This is a personal project built for longevity, not a team of fifty.

**In practice:**
- Avoid distributed systems complexity until v1.0 demands it.
- Prefer a single deployable process on the Mac Mini.
- Documentation is not optional — it is part of the product.

---

## 8. Design for maintainability before performance

Correctness and clarity come first. Optimise only when measurement shows a real problem.

**In practice:**
- No premature caching layers.
- No async everywhere by default — add concurrency when I/O boundaries justify it.
- Profile before optimising.

---

## 9. Every architectural decision should be documented

Significant choices get an ADR in [decisions.md](decisions.md).

**In practice:**
- Status: proposed → accepted → deprecated → superseded.
- Each ADR states context, decision, consequences, and alternatives considered.
- Small implementation details do not need ADRs. Boundary changes do.

---

## 10. Ask for clarification whenever an architectural decision requires human judgement

The founding engineer does not guess on matters that shape the product for years.

**In practice:**
- Present trade-offs, not just a recommendation.
- Flag unresolved questions explicitly.
- Defer implementation when requirements are ambiguous.

---

## Safety principles (operational)

These are required invariants. The table names intended enforcement; it is not a certification that legacy finance provenance, audit outcomes or structured response metadata are complete (D01/D02/D12):

| Rule | Enforcement |
|------|-------------|
| Never execute irreversible actions without explicit confirmation | `ApprovalProvider` gate |
| Never spend money without approval | Financial action classifier + approval |
| Never delete data without approval | Destructive action classifier + approval |
| Always explain important assumptions | Response formatting requirement |
| Distinguish facts from inferences | Structured response metadata |
| Record important decisions | Audit log + optional memory persistence |

---

## Anti-patterns to avoid

- **The magic prompt** — one giant system prompt that encodes all behaviour.
- **Leaky adapters** — Home Assistant entity IDs appearing in orchestrator code.
- **Silent failures** — actions that fail without audit trail entries.
- **Approval theatre** — confirmation dialogs that don't actually gate execution.
- **Dependency avalanche** — adding libraries for problems solvable in twenty lines.
- **Weekend-project entropy** — skipping docs because "we'll fix it later."
