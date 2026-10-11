# v0.19 — Reasoned Grounding

Status: implementation plan, 11 October 2026. No reasoner is connected to the
operational Gateway by this document. v0.18.1's D06/D07 fixes are complete in code;
the initial iCloud copy and hourly-agent completion are verified, including
Apple per-item uploaded/unshared metadata. Backup engineering adds no new requirements to this plan.

## Goal and first slice

Understand ordinary owner requests without requiring the exact document and
recipient titles in the message. Start with the existing `deliver_document`
intent and trusted record catalog. For example, “Send the Casaman assessment to
DBKL” should identify the registered assessment document and recipient when the
available evidence distinguishes them. “Send the bill to the council” should ask
which bill when several candidates fit.

This is reasoning about which existing records the owner means. A resolved
request can create one reference-only proposal for review. Approval remains
inert; this milestone does not add document sending or financial execution.

## Verified baseline

- [Telegram hint extraction](../src/wally/telegram/service.py) currently requires
  exactly one registered document title and recipient title to appear in text.
- [Grounding](../src/wally/ops/request_propose.py) accepts exact normalized titles
  only. Partial, unknown and duplicate titles cannot construct a proposal.
- [Gateway submission](../src/wally/gateway/service.py) authenticates the adapter,
  fixes its channel, checks runtime-issued authority and applies the shared
  proposal/continuity path. Evidence is bounded and remains untrusted.
- The registered catalog in [chatgpt.yaml](../config/chatgpt.yaml) is a static
  operator configuration. It is not complete live Notion discovery or a certified
  financial catalog. [Existing tests](../tests/test_chatgpt.py) cover exact
  grounding, ambiguous names and forged authority claims.

## Proposed runtime path

1. Authenticate the owner surface through its existing adapter and capability
   checks. Resolve permitted current catalog candidates and the relevant bounded
   request evidence in the runtime.
2. Keep deterministic exact matching as the first path. If it does not resolve
   the request, call a dedicated reasoned-grounding component through the existing
   LLM abstraction. Give it only eligible candidate identities/titles and the
   minimum bounded evidence required to interpret this request.
3. Request a typed advisory result: `resolved`, `needs_clarification`,
   `unsupported` or `unavailable`, with document/recipient references drawn from
   those supplied candidates and a concise explanation. Model confidence is
   advisory; it cannot establish record identity, authority or uniqueness.
4. Validate the result deterministically. Reject unknown, wrong-kind, duplicated
   or ambiguous references, extra dispatch fields and malformed results. Re-read
   the selected candidates and continuity state before persisting; changed or
   incomplete candidate evidence must not silently produce a proposal.
5. Use the existing canonical Matter/proposal functions to persist exactly one
   pending version and its request provenance. Fingerprints continue to bind
   canonical document/recipient identities. Request wording, model explanation
   and correlation references confer no authority.
6. Return a short, specific clarification for unresolved requests. Do not create
   a guessed proposal or require the owner to learn internal IDs. An unavailable
   reasoner preserves deterministic exact matching and gives a truthful outcome.

The first implementation uses the current trusted catalog. Live Notion candidate
retrieval is a separate reviewed integration, not an implicit promise of v0.19.
Do not claim uniqueness across candidates omitted by truncation, failed reads or
policy restrictions. No extra mailbox/document collection or unbounded transcript
retrieval is required by this plan.

## Boundaries

The component has no ToolRegistry, provider-write, secret-resolution or execution
handle. It cannot approve, certify payable facts, dispatch tools, select a payment
destination, send documents or mark an obligation resolved. External text and
assistant summaries remain evidence even when they contain instructions or
claimed approval. PrincipalAuthority contexts alone carry runtime authority.

Telegram/current ChatGPT keep their existing capability limits. Hosted ChatGPT
decisions stay disabled. Future privileged behavior needs separate authorization
and the existing reviewed-version/execution-time checks under ADR-042. This plan
changes neither ordinary owner-directed Notion connector edits nor the separately
supervised first-payment route.

## Delivery sequence and acceptance

- Define the typed result and deterministic validator with mocked reasoner tests.
  Cover clear paraphrases, multiple candidates, wrong-kind/invented IDs, malformed
  output, prompt injection and claimed authorization. Unresolved results create
  no Matter, proposal or continuity link.
- Implement the isolated grounding component and route authenticated submission
  through it. Reuse proposal persistence and existing replay/idempotency checks;
  preserve exact-title behavior when reasoning is unavailable.
- Add concise Telegram clarification/resolution wording. Reasoning never records
  decisions or grants execution, and repeated submissions do not create duplicate
  canonical Matters/proposals.
- Exercise catalog/continuity drift between selection and persistence. An old
  review or changed reference needs fresh grounding rather than inherited authority.
- Run isolated checks, then separately authorize a small owner acceptance set
  using the configured model/provider. Record actual model configuration and
  outcomes; mocks and settings alone do not prove live quality or readiness.

Scheduling/proactive triggers, reminder-delivery policy, new action types,
provider writes, transcript expansion and backup additions remain separate later
work. v0.19 is Reasoned Grounding, not the previously proposed proactive loop.
