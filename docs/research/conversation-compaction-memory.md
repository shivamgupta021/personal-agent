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
