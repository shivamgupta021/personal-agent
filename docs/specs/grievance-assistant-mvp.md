# Personal grievance assistant MVP

Status: synthesized from the agreed design interview on 17 November 2026. The user confirmed the public-loop testing boundary and later approved milestone 2 as a real terminal research agent using Pydantic AI and local Firecrawl, with metadata-only tracing. Published as GitHub issue #1 in shivamgupta021/personal-agent; it is the umbrella spec, not a single implementation ticket. This spec does not authorize skipping the agreed learning checkpoints.

## Problem Statement

The user wants a personal AI assistant that can eventually help with everyday activities and grievance redressal, while learning how agents actually work. Building many specialist agents immediately would make it difficult to understand which parts of the system choose actions, execute tools, preserve context, or recover from failures.

The first practical problem is preparing and following up on a refund request. Order details, the person's account of the problem, company policies, complaint drafts, and company replies need to remain associated with the correct grievance case. The user needs help gathering missing facts and composing useful correspondence without invented amounts, unsupported policy entitlements, or confusion between a draft and a submitted complaint.

The project is for personal use and demonstration, running on the user's Mac before any hosting decision. It should use the user's existing ChatGPT subscription and local Firecrawl installation.

## Solution

Build a local, owner-only Telegram assistant for grievance redressal, beginning with refund requests. The user supplies text, the assistant asks for missing information, researches official company sources, saves the grievance case, and prepares an email-style complaint draft. The user sends the complaint manually, records that it was sent, and can paste company replies to obtain follow-up drafts.

Support multiple saved cases with explicit selection when context is ambiguous. Preserve draft history and flag drafts affected by material corrections. Distinguish a requested refund, a sent complaint, and a resolved case. Retain cases until the user deletes them.

Build this in small learning milestones: inspect a minimal model/tool loop first, introduce Pydantic AI afterward, then add Telegram, persistence, research, and the complete case workflow. Review each milestone with the user before moving on.

## User Stories

1. As an assistant owner, I want the assistant to run on my Mac, so that I can use and develop the MVP without choosing a hosting provider.
2. As an assistant owner, I want to use direct ChatGPT subscription sign-in, so that the project uses the subscription access I already have.
3. As an assistant owner, I want the assistant to reuse my local Firecrawl installation, so that I do not need another deployment for research.
4. As a learner, I want to inspect a model request and its available tool definitions, so that I understand what the model receives.
5. As a learner, I want to see a requested tool call, its Python execution, and the result returned to the model, so that I understand the agent loop.
6. As a learner, I want to move the learning example into Pydantic AI, so that I understand what the framework takes over.
7. As a learner, I want to inspect one working milestone before starting the next, so that I understand the system as it grows.
8. As an assistant owner, I want to interact through Telegram, so that I can use the assistant from a familiar chat interface.
9. As an assistant owner, I want other Telegram users to be unable to access the assistant, so that my cases remain private.
10. As an assistant owner, I want to supply order details, issue descriptions, and company replies as text, so that I can begin without document or inbox integrations.
11. As an assistant owner, I want to create a grievance case for a refund request, so that its facts and correspondence stay together.
12. As an assistant owner, I want the assistant to ask for missing information, so that it does not invent details to complete a complaint.
13. As an assistant owner, I want supplied facts to remain distinct from researched policy statements, so that I can understand the basis of a draft.
14. As an assistant owner, I want multiple cases to remain saved, so that I can pursue more than one grievance.
15. As an assistant owner, I want to list and switch cases, so that I control which grievance the conversation concerns.
16. As an assistant owner, I want ambiguous references clarified, so that a company reply is not attached to the wrong case.
17. As an assistant owner, I want cases to survive application restarts, so that I can return to a grievance later.
18. As an assistant owner, I want the assistant to research official refund policies, so that my request can refer to relevant company terms.
19. As an assistant owner, I want the assistant to find official complaint procedures and support contacts, so that I know how to pursue the grievance.
20. As an assistant owner, I want research sources to include their URLs and retrieval dates, so that I can inspect what the assistant relied on.
21. As an assistant owner, I want search queries to omit personal information and private correspondence, so that researching a policy does not disclose my case details to search providers.
22. As an assistant owner, I want policy applicability distinguished from the existence of a policy, so that the assistant does not overstate my entitlement.
23. As an assistant owner, I want conflicting policies or uncertain purchase-time terms identified, so that I can supply clarification rather than receive a misleading claim.
24. As an assistant owner, I want a factual draft even when reliable research is unavailable, so that a research failure does not prevent requesting a refund.
25. As an assistant owner, I want unverified points identified alongside the draft, so that I know what needs checking.
26. As an assistant owner, I want an email-style complaint with a subject, factual account, requested refund, and references to supplied evidence, so that I can review and send it.
27. As an assistant owner, I want drafts to remain unsent until I send them myself, so that I retain control of external correspondence.
28. As an assistant owner, I want to correct material facts, so that future drafts reflect accurate information.
29. As an assistant owner, I want earlier drafts preserved and marked outdated after a material correction, so that I do not accidentally reuse an incorrect version.
30. As an assistant owner, I want conflicting statements clarified when I have not explicitly corrected a fact, so that the assistant does not silently choose a version.
31. As an assistant owner, I want to record that I sent a complaint, so that the case reflects my actual progress.
32. As an assistant owner, I want to paste a company reply and obtain a follow-up draft in the same case, so that subsequent correspondence retains the relevant context.
33. As an assistant owner, I want to mark a case resolved or closed without resolution, so that its outcome reflects my judgment.
34. As an assistant owner, I want to delete a case and its local messages, drafts, and research, so that I control what the assistant retains.
35. As an assistant owner, I want the deletion boundary explained, so that I do not mistake local deletion for deletion from Telegram or model providers.
36. As a learner, I want to save, recall, correct, and forget an explicit preference in a small learning exercise, so that I understand persistent memory before using it within the larger workflow.
37. As an assistant owner, I want execution metadata available in Langfuse without raw case content, so that I can investigate failures without exporting private correspondence into traces.
38. As a learner, I want to inspect full prompts only with synthetic cases, so that debugging does not require exposing real grievance details.
39. As an assistant owner, I want duplicate message delivery handled without duplicating saved case actions, so that retries do not corrupt my records.
40. As an assistant owner, I want the assistant to report failures honestly, so that I can distinguish saved work, unfinished work, and unavailable research.
41. As a developer, I want realistic, repeatable evaluation cases, so that changes can be assessed before I expand the assistant's scope.

## Implementation Decisions

- **Code organization:** Use class-based, object-oriented Python for core behavior, as requested after milestone 1. Prefer focused responsibilities, composition and constructor-injected dependencies. Use root-level `manage.py`, `config/` and Django apps, singular `service.py`, module-scope imports, app constants and separate prompts. Keep only thin entry points and small private stateless utilities as standalone functions.
- **Runtime and authentication:** Python and Django on the user's M4 Pro MacBook Pro. Use direct ChatGPT subscription sign-in. API-credit activation is not a prerequisite. Do not add automatic paid fallback or recharge. Integrating and checking authentication is implementation work, not a reason to reopen the chosen access route.
- **Agent architecture:** One Pydantic AI agent with narrow Python tools. The handwritten learning loop was completed and then removed at the user's request. The current real research loop uses Pydantic AI. Do not reintroduce synthetic application commands. Specialist orchestration is deferred; add capabilities through focused services and narrow tools.
- **Application responsibilities:** Keep Telegram transport, application-level turn handling, model/tool execution, case services, research integration, and observability distinct. Django owns durable application records; model conversation history supplies context; traces explain execution. Neither a model answer nor a trace is authoritative evidence of a case outcome.
- **Telegram boundary:** Use local long polling initially. Restrict access to the owner and private chat. Persist enough delivery identity to avoid duplicate application changes. Maintain one active grievance case per conversation, support listing and switching, and ask when a reference is ambiguous.
- **Persistence:** Preserve conversations, grievance cases, supplied facts, research sources, draft versions, company replies, and user-reported sent/outcome information. Persist across restarts. Use a dedicated local PostgreSQL database on the existing server when persistence is introduced, with Django migrations and an isolated test database. Do not reuse another application's tables. Conversation context, explicit preferences and grievance records have distinct responsibilities.
- **Case domain:** Refund requests are the first case type within grievance redressal. Preserve the distinction between a complaint draft, a user-reported sent complaint, a resolved case, and closure without resolution. Generating a draft or receiving a company reply must not automatically mark resolution.
- **Intake and corrections:** Accept typed/pasted text only. Ask for missing facts that affect the draft. An explicit material correction updates the facts used for future work and marks affected prior drafts outdated. Preserve those versions. Clarify conflicting statements without an explicit correction.
- **Research integration:** Reuse the user's existing local Firecrawl service for search and page retrieval. Verify its endpoint and behavior during implementation; no new installation is planned by default. Scope research to company-published policies, complaint procedures, and support contacts. Check that sources are genuinely official rather than relying solely on search ranking or a query's domain restriction.
- **Research evidence:** Preserve source URLs and retrieval dates with the policy statements used. Distinguish supplied facts, source content, and conclusions about policy applicability. Conflicting sources or uncertainty about historical terms must remain visible. Ask for relevant purchase-time terms where needed.
- **Research privacy and failure:** Search automatically with company names and general issue descriptions. Exclude names, order numbers, addresses, and private correspondence from query construction. Failed research or absent reliable sources permits a factual draft with uncertainty identified, not invented policy support.
- **Drafting:** Produce an email-style subject and body with a concise factual account, requested refund, and references to supplied evidence. Prepare follow-up drafts using the same case and pasted replies. Do not assert that referenced evidence was attached or sent when it was not.
- **Authority:** Tools may save and edit the application's local records and perform the agreed research. Sending email, submitting complaints, negotiating externally, purchasing, and other external write actions are not exposed by the MVP. The user manually records sent status and outcomes.
- **Retention:** Keep cases until explicit deletion. Delete locally stored case messages, draft versions, and associated research for that case. Ensure deleted case content is not subsequently reintroduced from retained conversation context. Do not claim to delete external provider or Telegram copies.
- **Observability:** Langfuse receives metadata such as tool names, timing, usage, internal identifiers, and sanitized errors. Exclude raw messages, tool payloads containing case content, drafts, credentials, and personal details from exported traces. Full-content learning/debugging uses synthetic cases only. Trace configuration must enforce these boundaries rather than merely ask the model to comply.
- **Learning delivery:** Complete and inspect one milestone at a time: minimal loop; Pydantic AI and tracing; Telegram and persistence with the small explicit-memory exercise; intake/research/drafting; manual progress updates, follow-ups, and evaluation. Pause for the user's review before moving to the next milestone.
- **Execution limits:** Verify bounded model/tool execution, tool failures, restart persistence, duplicate updates, owner access, and trace redaction as implementation checks. Exact limits and library versions are implementation choices to document when selected.

## Testing Decisions

- **Current implementation:** The real public ResearchAgent.run boundary is covered with controlled model/HTTP responses, execution limits, failure recovery and fresh history. Native tracing tests inspect serialized OTLP payloads. At the 18 November checkpoint, 21 tests, strict mypy and Django checks pass. Live subscription/Firecrawl research has succeeded; live Langfuse project ingestion still needs keys and verification. Extend these boundaries as durable application state is added.
- **Primary test boundary:** Submit a normalized owner message or user action through the application-level turn handler and observe the user-facing reply and durable case state. For milestone 1, the user confirmed message input, controlled model responses, visible tool steps, final answer, and stopping limits, with a separate live subscription smoke check. Persistence is added in later milestones. Exercise the agent, tool services, and persistence together when available. This keeps the main suite independent of Telegram transport objects, prompt wording, internal helper calls, and future module rearrangements.
- **External dependencies at that boundary:** Substitute controlled model and research responses for repeatable application tests, while using an isolated real test database. Simulate failures and ambiguous results through those dependencies. Do not mock the case services or persistence merely to assert that internal methods were called.
- **Good tests:** Check external behavior: correct case association, preserved/corrected facts, appropriate clarification, draft version visibility, saved state after restart, honest failure reporting, deletion, and absence of unauthorized actions. Assert meaningful outcomes rather than exact prose, particular prompts, or incidental tool order.
- **Small integration checks:** Complement the primary suite with focused checks for Telegram normalization/owner rejection, actual subscription-authenticated tool calling, existing Firecrawl search/page retrieval, and the exported telemetry payload. These verify contracts hidden by controlled test dependencies; they do not require parallel full suites for every internal module.
- **Real-model evaluations:** Start with approximately 20 realistic cases. Use controlled company source fixtures for repeatable grounding checks, plus a separate live research smoke check. Include missing details, conflicting amounts, ambiguous case references, unavailable research, conflicting or historical policies, corrections, case deletion, and unrelated-case facts.
- **Acceptance gate:** Require zero observed cross-case disclosures and zero fabricated amounts or policy entitlements in the agreed evaluation set. Manually review usefulness, factual fidelity, clarity of uncertainty, and the appropriateness of follow-up questions. Passing this set is a development gate, not a claim of universal reliability.
- **Persistence and delivery checks:** Reopen saved cases after restart, replay duplicate updates, retain earlier draft versions after correction, and ensure local deletion prevents later retrieval or reuse of deleted case content.
- **Privacy and authority checks:** Inspect outbound search queries and exported trace payloads. Verify that real case content and secrets are absent where required. Confirm that drafts cannot trigger external sending and that user-reported outcomes remain distinct from inferred outcomes.
- **Regression practice:** Retain observed failures as additional cases. Run deterministic checks on code changes and real-model evaluations when prompts, models, providers, or orchestration change. Do not use deterministic simulated-model tests as evidence of language-understanding quality.

## Out of Scope

- Flights, movie tickets, food ordering, meetings, or a general-purpose personal-assistant orchestrator.
- Specialist-agent orchestration for the first MVP.
- Refund execution, external negotiation, sending email, complaint submission, or consumer-portal automation.
- Legal research, legal-rights determinations, or automatic escalation to regulators.
- Screenshots, PDF/document ingestion, inbox access, and automatic reading of company replies.
- Automatic monitoring, follow-up reminders, or background case progression.
- Broad personal-data collection, imported ChatGPT history, or automatic inference of unrelated personal memories.
- Public multi-user access, monetization, hosted deployment, or a replacement Firecrawl installation by default.
- Automatic paid API fallback, API-credit onboarding as a prerequisite, or recharge configuration.
- Full-content tracing of real cases, deletion of external copies, or a claim that the first evaluation set guarantees correctness.

## Further Notes

The root project glossary supplies the domain vocabulary. The design interview is the source of agreed scope and supersedes earlier research recommendations, notably the earlier API-credit-first suggestion and the initial supplied-facts-only research boundary.

Project-specific ChatGPT subscription access and local Firecrawl integration have been exercised successfully. Reuse them. Langfuse is running locally, but live project ingestion remains unverified until project keys are configured.

This spec describes the full product, not a claim that every feature is implemented. The handwritten learning loop was completed and subsequently removed; only the real research loop remains in application code. The user brought local Firecrawl forward into milestone 2 to build a real terminal research agent rather than another synthetic example. Producing a complete spec does not remove the requirement to review milestones together.

The private GitHub repository shivamgupta021/personal-agent is the configured issue tracker. This spec is published as issue #1; milestone 2 is tracked in issue #2. Tracker and triage conventions live in docs/agents/. Milestone 1 is implemented; subsequent milestones remain subject to the learning checkpoints.

## Milestone 2 scope update — 18 November 2026

The user approved public-web terminal research as the next usable vertical slice: one Pydantic AI agent, existing ChatGPT subscription authentication, local Firecrawl search and page reading, sourced answers, bounded execution, visible tool steps, and optional metadata-only Langfuse export. The user subsequently requested removal of the handwritten lesson and synthetic commands; the real research agent is the sole application loop. Public-run tests and outbound telemetry exclusion checks are approved; code review baseline is `7c5996f`. General public-topic research is allowed in this standalone milestone; company-only research and enforced separation of private case facts remain requirements for the later grievance workflow. No Telegram, persistent case state, drafts, or external actions are included.

During manual review, the user requested Django app organization and native Pydantic AI–Langfuse instrumentation. Django now owns settings, logging, startup, and management commands; persistent case models and web endpoints remain out of scope. Global instrumentation replaces the manual trace exporter, with content capture disabled and serialized-payload privacy tests.


## Provisional delivery breakdown — 18 November 2026

- Milestone 1: completed as a learning exercise before GitHub tickets existed; synthetic application code was removed in `2363cc0`.
- [#2](https://github.com/shivamgupta021/personal-agent/issues/2): completed real terminal research, subsequently simplified into root-level Django apps with shared native tracing. Current code checkpoint: `5d2e0cb`.
- The closed research ticket means implementation is complete; it does not claim live Langfuse ingestion has been verified.

| Issue | Deliverable | Dependencies |
| --- | --- | --- |
| [#3](https://github.com/shivamgupta021/personal-agent/issues/3) | Verify live native Langfuse ingestion | Independent configuration check |
| [#4](https://github.com/shivamgupta021/personal-agent/issues/4) | Milestone 3: owner-only Telegram with persistent explicit memory | #2 |
| [#5](https://github.com/shivamgupta021/personal-agent/issues/5) | Milestone 4: persistent grievance cases and factual intake | #4 |
| [#6](https://github.com/shivamgupta021/personal-agent/issues/6) | Milestone 5: factual complaint drafts with immutable versions | #5 |
| [#7](https://github.com/shivamgupta021/personal-agent/issues/7) | Milestone 6: private case research with official evidence and grounded drafts | #6 |
| [#8](https://github.com/shivamgupta021/personal-agent/issues/8) | Milestone 7: manual sent status, company replies and follow-up drafts | #7 |
| [#9](https://github.com/shivamgupta021/personal-agent/issues/9) | Milestone 8: complete local case deletion and context cleanup | #8 |
| [#10](https://github.com/shivamgupta021/personal-agent/issues/10) | Milestone 9: integrated grievance evaluations and MVP acceptance | #9, #3 |

The user rejected publishing this breakdown before reviewing its granularity and blockers. Build tickets are now needs-triage and are not authorized as the implementation frontier. The [reviewed grievance MVP decision map](https://github.com/shivamgupta021/personal-agent/issues/11) owns unresolved planning decisions. Use to-spec and to-tickets checkpoints before republishing an approved sequence. Implementation is paused while the user learns Langfuse. The separate live Langfuse ingestion check has now succeeded; evidence is recorded in README.

Focused model evaluations accompany drafting/research milestones; #10 integrates the suite rather than introducing evaluation for the first time. The finished MVP remains refund-first grievance assistance with manual external actions. Langfuse prompt management, general multi-agent orchestration, bookings and hosting are deferred.

## Approved Telegram research slice — 18 November 2026

This replaces the combined Telegram-and-memory scope of #4 for the next learning milestone,
following #12 and the user's explicit implementation approval. Broader context/deletion
questions in #13–#15 remain for subsequent milestones.

- A local Django command uses long polling and accepts only the configured owner's private
  text messages. Other users and group chats cannot reach the model or cause replies.
- Each message is a standalone public research request using the existing ResearchService,
  subscription model, Firecrawl tools, and native Langfuse instrumentation. Process one at a time.
- Save the next Telegram update cursor atomically before starting work. After interruption,
  do not automatically rerun research or resend uncertain replies: the owner must resend the
  question. Persist only cursor/timestamp, not prompts, answers, preferences, or chat history.
- One local process per bot state directory; no automatic webhook deletion or deployment.
- Split long replies without dropping text; preserve source links as plain text. Provide local
  help and sanitized failure messages. Respect rate-limit waits without retrying uncertain sends.
- No conversation memory, explicit preference tools, grievance intake, database, or general
  orchestrator in this slice. Keep the later explicit-memory exercise in the roadmap.
- Approved test seam: public polling/turn boundary with controlled Telegram HTTP and model
  responses. Cover access control, replies/citations, splitting, duplicates/restart, cancellation,
  and failures. A separate live owner-message round trip verifies real Telegram configuration
  and Langfuse ingestion. Code review baseline: a5892b8.
