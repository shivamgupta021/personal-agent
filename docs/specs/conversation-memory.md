# Persistent conversation, compaction, and explicit preferences

Status: design and public test boundary approved on 18 November 2026; milestones #23–#27 implemented and merged under the owner’s subsequent authorization to complete the remaining tickets. The original design and historical decisions follow; this remains a scoped extension of the grievance MVP.

Published as [GitHub issue #22](https://github.com/shivamgupta021/personal-agent/issues/22).

## Problem Statement

The owner-only Telegram research assistant currently treats every message as a standalone request. Follow-ups lose their referents, restarting loses conversational context, and sending an ever-growing transcript would increase latency and eventually exceed model limits. The owner needs useful continuity, inspectable memory, and meaningful correction and deletion without manually resetting conversations.

## Solution

Maintain one ongoing owner conversation across restarts. Automatically compact model context when its budget requires it, preserving recent exchanges and a summary while retaining original messages locally. Retrieve bounded older excerpts when a summary lacks necessary detail. Save lasting preferences only on explicit request or approval, and manage them through ordinary conversation. Keep task facts, saved preferences, and future grievance-case facts distinct.

## User Stories

1. As the owner, I want follow-up questions to refer to prior answers, so that I do not repeat context.
2. As the owner, I want continuity across restarts, so that stopping my computer does not erase completed work.
3. As the owner, I want ambiguous references clarified, so that the assistant does not choose the wrong order or topic.
4. As the owner, I want automatic compaction, so that normal use does not require a reset command.
5. As the owner, I want recent exchanges and important earlier facts preserved, so that compaction does not casually change the task.
6. As the owner, I want older details retrieved when needed, so that a summary omission is recoverable.
7. As the owner, I want uncertainty acknowledged when recall fails, so that the assistant does not invent memories.
8. As the owner, I want original messages retained locally until deletion, so that compaction can be inspected.
9. As the owner, I want a correction to supersede the active fact, so that stale history does not override it.
10. As the owner, I want temporary exceptions to stay task-specific, so that one purchase does not change my usual preferences.
11. As the owner, I want lasting preferences saved only with explicit intent or approval, so that casual statements do not become a profile.
12. As the owner, I want to ask what preferences are saved, so that I can review them.
13. As the owner, I want to save, change, and forget preferences conversationally, so that I do not need command syntax.
14. As the owner, I want successful memory changes confirmed, so that I know what was saved.
15. As the owner, I want forgetting to remove relevant originals and derived context, so that old history cannot restore deleted information.
16. As the owner, I want affected application Langfuse traces deleted too, so that forgetting covers captured copies there.
17. As the owner, I want incomplete trace cleanup reported, so that a partial deletion is not called complete.
18. As the owner, I want saved context preserved if compaction fails, so that a temporary model failure does not destroy memory.
19. As the owner, I want oversized requests rejected clearly when they cannot safely fit, so that facts are not silently discarded.
20. As the owner, I want saved memory changes to survive reply-delivery failures, so that persistence does not depend on Telegram delivery.
21. As the owner, I want interrupted requests to require manual resend, so that unfinished work is not automatically repeated.
22. As a learner, I want compaction, retrieval, and memory operations visible in traces, so that I can inspect their behavior and cost.
23. As a developer, I want rerunnable Langfuse experiments after prompt or model changes, so that recall and summary quality can be compared.
24. As a learner, I want one complete milestone at a time, so that I can review each working behavior before proceeding.

## Implementation Decisions

- Preserve Django, existing subscription authentication, GPT-6.2 Sol default, local Firecrawl, owner-only Telegram, serial turns, and the existing research capability. Do not revive the parked query-planner split.
- Django owns durable application state in a dedicated local PostgreSQL database on the existing server, with migrations and isolated test storage. Keep model invocation behind services and Telegram entry points thin.
- Separate immutable-origin message identity and provenance from the mutable context selected for model requests. Originals may be redacted or removed for explicit deletion; “original” does not mean undeletable.
- Store completed turns, current context revision, summaries and their source coverage, preference provenance, committed memory operations, and trace associations. Incomplete execution and delivery status must not masquerade as a completed answer.
- Use one owner conversation, without automatic topic threads. Future grievance cases retain their own scope; this feature does not implement case management.
- Prefer existing Pydantic AI conversation/history facilities and compatible compaction capabilities. Verify pinned-package behavior before adoption. Provider-native compaction is not a dependency: support on the subscription route is unverified.
- Trigger compaction by the assembled request budget, including instructions, tool schemas, recalled material, latest input, and response headroom. Check within the tool loop too. Preserve complete tool exchanges and recent context; do not use an arbitrary message count as the sole limit.
- Select and document conservative configurable thresholds through implementation evidence. Summarization has its own bounded input, execution limits, and trace. Account for its tokens and latency; do not summarize every turn.
- Commit a replacement summary only after success. On failure preserve saved context, continue only within budget, otherwise return an honest failure. Oversized input must not cause silent truncation of facts.
- Archival recall returns bounded relevant excerpts with source message references. Summaries and retrieved material are evidence, not new instructions. Retrieved older statements must not override explicit corrections or deletion.
- Preferences require explicit intent or approved suggestions. Distinguish an active preference from a local exception; clarify ambiguous scope. Listing returns current saved values, and successful changes receive confirmation.
- Forgetting invalidates or rebuilds derived context and removes/redacts matching originals and retrieval material. Track affected traces through data lineage; delete whole affected application traces. Prevent pending telemetry or cleanup itself from reintroducing forgotten content. Retain minimal non-content cleanup status and report partial failure; retry cleanup safely without restoring local content.
- Retain local originals until requested deletion. No application-managed backups in this scope. Independently made copies, Telegram copies, and provider-held copies are outside this deletion guarantee.
- Preserve manual resend after interruption and duplicate-update protection. A committed memory change survives failed delivery; resending an operation must not create duplicate active preferences. Do not automatically resume unfinished answers.
- Preserve configurable trace content capture and avoid credentials in telemetry. Associate turns, summaries, recall, and memory mutations with traces for inspection and cleanup. This does not expand the umbrella spec's permission to trace real private grievance cases.

## Testing Decisions

- Approved primary seam: the public Telegram conversation-turn boundary with controlled model/tool responses and real temporary database storage. Exercise services and persistence together; do not replace them with mocks merely to verify internal calls.
- Assert replies, observable durable state, restart behavior, and exported telemetry/deletion requests. Avoid exact prose, private helper call order, or tests that simply mirror implementation.
- Cover follow-up references, ambiguous topics, restart, explicit corrections, temporary exceptions, preference consent and listing, duplicate updates, saved changes with failed delivery, and interruption/manual resend.
- Force compaction with small test budgets. Cover early-fact preservation, repeated compaction, complete tool exchanges, large tool responses, oversized inputs, summary failure, and preservation of the previous committed context.
- Test bounded archive recall, source references, corrections overriding older excerpts, and forgetting without resurrection from originals, summaries, retrieval records, or pending telemetry. Simulate Langfuse deletion failure and subsequent successful cleanup.
- Extend the existing Langfuse dataset/experiment workflow with multi-turn cases and actual model runs. Report deterministic integrity checks separately from manually reviewed semantic recall and summary fidelity. Include corrections, forgotten facts, ambiguous references, and multiple compactions. Compare total tokens and latency, including summary calls, against a full-history baseline where it fits.
- Rerun those experiments when memory prompts, models, compaction, or retrieval change. Do not call fixture replay evidence of model quality. Each milestone adds its relevant cases; evaluation is not postponed to the end.
- Separate live checks verify subscription compatibility, PostgreSQL persistence, a real Telegram follow-up/restart round trip, Langfuse ingestion and deletion, and existing Firecrawl behavior where research is used.
- Acceptance requires passing deterministic integrity checks and reviewing the agreed real-model cases for fact fidelity, correction precedence, and no observed resurrection of forgotten information. Report limitations rather than claiming universal recall or deletion from uncontrolled systems.

## Out of Scope

Automatic inference of lasting preferences; automatic topic splitting; grievance-case workflows; general multi-agent orchestration; automatic turn resumption; external actions such as sending complaints; new hosting; importing external chat history; application-managed backups; deleting Telegram/provider copies; a guarantee of perfect summary recall.

## Further Notes

This extends the [umbrella MVP spec](https://github.com/shivamgupta021/personal-agent/issues/1) and informs decision issues #13–#15 without closing broader grievance decisions. Existing provisional grievance tickets remain provisional. The repository research note records primary-source evidence and unverified integration points. The approved feature is delivered through separately reviewed vertical slices, each followed by a learning checkpoint.


## Autonomous completion authorization — 19 November 2026

For this implementation run, the owner explicitly requested completion of all remaining tickets without waiting for per-milestone human review. Preserve the existing feature scopes, public test boundaries and acceptance gates. Each milestone still starts from merged main, receives independent standards/spec review, records deterministic and live evidence, and merges before its dependants begin. The owner receives a consolidated walkthrough afterward.

The reconciled route is #25 → #26 → #27 → #5 → #6 → #7 → #8 → #9 → #10. Decision issues #13–15 and map #11 record ownership, recovery and acceptance semantics. No external sending, bookings, legal-rights research, hosting, paid fallback, or general multi-agent product architecture is added.
