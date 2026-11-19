# Conversation compaction and durable memory

Refreshed 18 November 2026 from primary sources and installed code. This supersedes the preliminary note's recommendations: **research input, not an accepted design**. Documentation access date is 18 November 2026 unless a publication date is stated. No implementation, dependency installation, tickets, or commits.

## Verified evidence

**Current application.** `pyproject.toml` and installed metadata agree on `pydantic-ai-slim[openai]==2.69.0`; `pydantic_ai_harness` is absent. `research/service.py` creates a fresh research agent per request. `subscriptions/service.py` uses `OpenAIChatGPTProvider`, `OpenAIResponsesModel`, and `openai_store=False`. Existing capability in a framework is therefore distinct from a working conversation feature here.

**Core history and persistence.** Installed `pydantic_ai/conversation.py` and the [official history guide](https://pydantic.dev/docs/ai/core-concepts/message-history/) expose `Conversation`: messages, cumulative usage, conversation ID, and deferred tool requests. Passing only messages loses cumulative usage accounting; conversation serialization supports restart continuity. The guide also documents `ConversationStore`, revision conflict detection, and appending newly generated messages. These supply persistence primitives, not a user preference model. Installed `capabilities/process_history.py` runs before model requests and rewrites both request messages and `ctx.messages`: a processor can affect returned history, so preserving original evidence requires an intentional archival boundary.

**Optional harness compatibility.** [PyPI publishes harness 0.69.0](https://pypi.org/pypi/pydantic-ai-harness/0.69.0/json). The [v2.69.0 tagged manifest](https://github.com/pydantic/pydantic-ai/blob/v2.69.0/src/pydantic_ai_harness/pyproject.toml) maps its version to 0.69.0 and pins slim to 2.69.0; it requires Python ≥3.11 and base dependencies `genai-prices>=0.0.71` and `httpx2>=2.7`. This establishes intended release compatibility with this Python 3.12 environment, not a resolved installation or tested integration. Avoid assuming today's latest harness matches the pinned core.

**Harness compaction.** [Current documentation](https://pydantic.dev/docs/ai/harness/compaction/) provides sliding windows, tool-result clearing, summarization, and tiered escalation. `TieredCompaction` remeasures after each tier, stopping at its target; cheaper cleanup can precede summarization. Retention extends over whole messages and matching tool dependencies. Consequently, `keep_tokens` can overshoot, and the docs explicitly deny a context-window-fit guarantee. Token measurement can use prior provider usage plus estimates for new material. These are useful existing mechanisms; their defaults and API availability should be checked against 0.69.0 before adoption. They do not establish a durable preference policy.

**Provider-native compaction.** Installed `models/openai/_compaction.py` supports server-managed Responses compaction and a stateless `/responses/compact` call. The application's actual ChatGPT subscription route has not been exercised for either. Support for ordinary Responses calls does not prove support for compaction endpoints, token counting, or context-management parameters. [Anthropic's current compaction guide](https://platform.claude.com/docs/en/build-with-claude/compaction) separately describes on-demand and token-threshold beta APIs, including retention of recent turns and background operation. Those are Claude API capabilities, not evidence about this application's provider.

**Memory is a separate storage concern.** [LangGraph's memory concepts](https://docs.langchain.com/oss/python/concepts/memory) distinguish thread-scoped checkpoints from cross-thread, namespaced stores. They discuss profiles versus collections and updating synchronously versus in the background. [Its history guide](https://docs.langchain.com/oss/python/langgraph/add-memory) demonstrates extending an existing summary and warns that retained tool calls need matching results. These patterns do not require migrating frameworks. [Anthropic's memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool) requests create/read/update/delete operations, while application code owns storage; `/memories` can map to database keys. Persisting context across restarts and learning a profile across conversations remain different product behaviors.

## Options, not decisions

| Approach | Benefit | Cost or unresolved dependency |
| --- | --- | --- |
| Core history processor + application summary | Existing framework; application controls summary schema | Own thresholds, pairing, recovery, persistence and evaluation |
| Matching harness compaction capability | Reuses cleanup, summarization and escalation | Additional package; verify pinned behavior and integration |
| Provider-native compaction | Delegates compression to provider | Actual subscription compatibility unverified; provider-specific state |
| Sliding window alone | Simple, predictable removal | Drops older facts; insufficient for assured long-task recall |

Durable preferences can accompany any option. Explicit save commands offer clear intent; proposing inferred preferences for confirmation adds review; automatically saving inferred preferences reduces interaction but increases false-memory and correction risk. Research does not select among them.

## Engineering implications to validate

These are project-specific deductions, not framework guarantees:

- Budget the **assembled request**: instructions, tool schemas, retrieved memories, summary, recent messages, latest input, and output headroom. Check inside the tool loop; one large tool response can overflow between user turns. Cumulative spend limits are different from a per-request context limit.
- Preserve complete tool exchanges, pending requests, and the active objective. Handle oversized latest input and summarizer failure explicitly. A summary call also needs its own input budget, latency allowance, and failure behavior.
- Treat summaries as fallible derived context. Anthropic's [context-engineering article](https://www.anthropic.com/research/effective-context-engineering-for-ai-agents), published 29 September 2025, warns that aggressive compaction loses useful detail. Repeated summary-of-summary passes can compound omissions; retain retrievable originals or structured authoritative facts if exact recall matters.
- Candidate durable records should carry scope, source message identifiers, update time, and whether the user stated or the model inferred them. Keep the person's account distinct from a research source; unresolved policy applicability must remain unresolved, following `GLOSSARY.md`.
- A correction must supersede stale records and derived summaries. Forgetting needs a defined reach: retrieval records, cached summaries, original transcript, backups, and observability are separate places. Test that removed preferences cannot be silently reconstructed from stale context.

## Open product decisions and technical checks

### Confirmed design inputs — 18 November 2026

- Design conversation continuity and durable preferences together, then implement in small reviewable milestones.
- Conversation continuity includes automatic compaction and persistence across restarts; routine use must not depend on a manual reset.
- Preserve task facts in conversation context automatically. Save cross-conversation preferences only on an explicit request or approval of a suggestion.
- Treat the private Telegram chat as one ongoing conversation across restarts. Do not automatically split topics; ask when a reference is ambiguous. Future grievance-case facts remain separately scoped.
- Explicit corrections replace active facts or saved preferences. Temporary exceptions remain task-specific; clarify ambiguous scope.
- Forgetting removes the saved preference and removes or redacts relevant application-managed history and summaries so they cannot restore or reuse it.
- Retain original conversation history locally until requested deletion; model requests use bounded context rather than the entire archive. Compaction is not deletion.
- Forgetting also deletes affected application Langfuse traces, reporting cleanup failures explicitly. No application-managed backups are added in this milestone; independently made copies are outside the deletion guarantee.
- On compaction failure, preserve the last saved context. Continue only if the request still fits the context budget; otherwise report inability to continue. Do not silently drop old facts. Trigger compaction when needed, not on every message.
- Retrieve bounded excerpts from older local messages, with message references, when the summary lacks needed details. Ask if the detail cannot be recovered reliably.
- Support ordinary conversational requests to inspect, save, change, and forget preferences. Confirm successful changes and show current values when asked; special commands are not required.
- Preserve manual resend after interruption. Completed conversation turns and saved memory changes survive restart; an unfinished answer is not completed history. Failed reply delivery does not roll back an already saved memory change. Automatic resumption is deferred.
- These inputs define the agreed product behavior; technical selection and validation remain below.

### Technical checks before implementation choices are finalized

1. Verify matching harness behavior, usable context budgets, and token accounting. Provider-native compaction is optional and must not be assumed to work on the subscription route.
2. Validate bounded archival retrieval, correction precedence, and deletion propagation through derived context and trace references.
3. Measure compaction fidelity, latency, and total tokens, including summarization. Select conservative configurable budgets using evidence rather than arbitrary message counts.

Acceptance research should compare repeated compactions against full-history answers using synthetic cases: corrected early facts, exact source references, huge tool results, pending tools, restart, case isolation, and deletion without resurrection. Measure accuracy, provenance, total tokens including summarization, and latency. The existing MVP spec is an input to reconcile during grilling, not authorization to settle these questions here.

## Milestone #24 implementation evidence — 18 November 2026

Installed and exercised `pydantic-ai-harness==0.69.0` with the existing
`pydantic-ai-slim==2.69.0`. The public `SummarizingCompaction.before_model_request`
capability handles tool-safe retention and anchored incremental summaries. Its nested
summary agent uses the existing model connection, contributes to shared request/usage
limits, and emits native agent/generation spans. No provider-native `/compact` API is
required. The subscription route successfully generated summaries in a real run.

Application policy wraps that capability: trigger at 32,000 serialized request bytes,
retain at least 6,000 content bytes of recent messages (whole tool dependencies may
expand this), and enforce the existing 48,000-byte hard guard after compaction.
The summary call has its own hard input guard, a 45-second timeout, 1,500 output-token
cap, no output retries, and at most two compaction attempts per turn. Tool evidence is
not truncated before summarization. These conservative byte estimates are not exact
provider token counts. An oversized protected tail fails visibly rather than losing facts.

Original messages carry stable local archive identifiers. Successful turns atomically
save the original-message archive, reduced active context, and summary snapshots with
source identifiers; failed turns leave committed state unchanged. Whole-conversation
deletion cascades to summary snapshots, and nested summary traces share the recorded
turn trace for deletion. Archival recall remains milestone #25.

Validation: all 57 tests, Django checks, and typechecking (87 files) passed.
Controlled Telegram-turn tests cover repeated compaction across service restarts,
summary failures below/above the hard limit, large tool results with complete tool
pairs, oversized input, and whole-conversation deletion.

A four-turn real-model comparison retained the corrected budget (700), unresolved
vendor selection, project name, and exact fictional source URL on both paths:

| Path | Summary calls | Total generation tokens | Four-turn elapsed |
| --- | ---: | ---: | ---: |
| Automatic compaction | 2 | 9,955 | 24.21 seconds |
| Full history | 0 | 12,053 | 13.83 seconds |

Tokens sum every generation, including both summarizers. This single synthetic case
shows a latency tradeoff, not a general speed or quality benchmark. Both coarse
checks passed and final answers were manually inspected. Summary source references
and original-message retention were inspected in local database records.

Compaction trace: http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/6e4fbae74af35eb91fe0cfd8196aa8e9
Baseline trace: http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/8e31026b356a0d93706b91b898b552e5

Review follow-up: a stronger cumulative-lineage assertion exposed that Pydantic may
merge consecutive requests and discard message-level metadata. Summary provenance
now lives on `CompactionPart.metadata`, which survives normalization; original
capture uses durable run history. The public-turn regression verifies all original
pairs, cumulative resolvable source coverage, and removal of archive/summary records.
Standards review found no issues; the spec review's validation finding was resolved.

## Milestone #25 — bounded archival recall

Added an on-demand `recall_conversation` tool scoped by the application conversation
identity, never by a model-provided owner identifier. It searches original user and
assistant text with case-insensitive keywords and returns newest-first evidence:
message ID, timestamp, role, excerpt and truncation state. Limits are three searches
per turn, five results per search, 500 characters per excerpt and 200 query characters.
The existing assembled-request guard still applies after retrieval. No retrieval
cache or extra service is introduced. Whole-conversation deletion removes originals
and stored tool exchanges, and existing turn/trace associations cover recall spans.

This is lexical recall, not semantic search. All query terms must occur in a message;
excerpts surround the first keyword and can omit widely separated details. Narrower
or reordered keywords may help. The application reads its local archive on demand;
large-archive indexing is deferred. Prompt-based correction/ambiguity handling is not
a guarantee of semantic correctness.

A live long-history case initially hit the budget guard after retrieval. Inspection
found repeated historical instructions were being counted alongside the active
instruction parts, plus local provenance metadata. The pinned Pydantic model code
uses `model_request_parameters.instruction_parts` when present, falling back to
message instructions only when absent. The estimator now follows that distinction
and excludes local metadata; it retains the same 48,000-byte guard and headroom.
Smaller excerpts and a prompt clarification avoid repeated retrieval solely because
an otherwise sufficient excerpt is marked truncated.

Review found and fixed Unicode offset drift: case-folded string positions cannot be
used to slice original text. Original-text match spans now locate excerpts. Typed
result dataclasses preserve the repository's schema conventions.

Validation: 64 tests pass; typechecking passes across 89 files; Django checks pass.
Public Telegram-turn tests cover omitted facts after compaction/restart, original
references, deletion/no resurrection, correction ordering, ambiguity, owner isolation,
result and call bounds, and Unicode offsets. Controlled replies test orchestration;
real-model experiments separately assess semantic behavior. Both review axes have no
remaining findings. Run `uv run python manage.py evaluate_conversation --recall` to
repeat the three hosted Langfuse cases; inspect cited source IDs, not just coarse scores.

Final real-model experiment passed all three coarse checks and manual inspection:
http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/experiments/results?baseline=c5ad713cab50650c
The four-turn long-history case took 25.87s,
with two prior summaries. Its recalled code and corrected budget cite IDs verified
against original user messages. The trace contains two recall calls returning one
and two excerpts respectively, each at most 500 characters, and no error observations.
Missing evidence produced a question instead of an invented invoice; the ambiguous
parcel case asked which parcel. These are limited synthetic acceptance cases, not a
claim of general semantic recall accuracy.

Trace: http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/3e2c28e56e6692f98b7710c3409808c3

## Milestone #26 live acceptance — 19 November 2026

Real-model targeted correction and forgetting both passed. Each five-turn case
included long context, archive recall, a mutation, and a later recall after service
reconstruction. The corrected code superseded its old value; the forgotten code was
reported unavailable; an unrelated Python fact survived both operations.
Local archive/history inspection found neither old synthetic marker. All eight
prior affected trace IDs returned zero observations via the Langfuse observation API.
The test covers these observed synthetic cases, not universal semantic deletion.

Experiment: http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/experiments/results?baseline=2546889dd06181a7
Post-forgetting trace: http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/3160bce76fef2e3235edf9f951df65b3
Post-correction trace: http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/1e5fcd302679aa21c0e8d8f9a717207c

The bounded edit uses intent authorization, exact-quote redaction and semantic
verification before a revision-checked transaction. It preserves unrelated original
prose but discards derived summaries/tool wrappers to prevent stale copies from
returning. Trace deletion conservatively covers earlier turns of the affected
conversation; unrelated conversations remain untouched. Failed verification or
oversized archives leave saved state unchanged. Tests must additionally cover
stale in-flight saves, partial cleanup and retries.
