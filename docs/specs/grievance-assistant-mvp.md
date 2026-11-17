# Personal grievance assistant MVP

Status: draft synthesized from the agreed design interview on 17 November 2026. The proposed testing boundary awaits user confirmation. Publication is pending project issue-tracker configuration; intended triage label: `ready-for-agent`. This spec does not authorize skipping the agreed learning checkpoints.

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

- **Runtime and authentication:** Python and Django on the user's M4 Pro MacBook Pro. Use direct ChatGPT subscription sign-in. API-credit activation is not a prerequisite. Do not add automatic paid fallback or recharge. Integrating and checking authentication is implementation work, not a reason to reopen the chosen access route.
- **Agent architecture:** One Pydantic AI agent with narrow Python tools. Begin with a minimal inspectable loop using synthetic data and a harmless tool before adopting the framework. Specialist agents are deferred.
- **Application responsibilities:** Keep Telegram transport, application-level turn handling, model/tool execution, case services, research integration, and observability distinct. Django owns durable application records; model conversation history supplies context; traces explain execution. Neither a model answer nor a trace is authoritative evidence of a case outcome.
- **Telegram boundary:** Use local long polling initially. Restrict access to the owner and private chat. Persist enough delivery identity to avoid duplicate application changes. Maintain one active grievance case per conversation, support listing and switching, and ask when a reference is ambiguous.
- **Persistence:** Preserve conversations, grievance cases, supplied facts, research sources, draft versions, company replies, and user-reported sent/outcome information. Persist across restarts. Physical schema and database selection remain implementation details, not additional product scope.
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

- **Current prior art:** The project contains a glossary, research, and design notes only. There is no application, existing test seam, test suite, or ADR to reuse. Upstream examples informed the design, but they are not tests of this application.
- **Proposed primary test boundary — awaiting user confirmation:** Submit a normalized owner message or user action through the application-level turn handler and observe the user-facing reply and durable case state. Exercise the agent, tool services, and persistence together through this boundary. This keeps the main suite independent of Telegram transport objects, prompt wording, internal helper calls, and future module rearrangements.
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

The user confirms both prior success with ChatGPT subscription access and an existing local Firecrawl installation. Their project-specific integration remains to be exercised, without revisiting those choices. A previous read-only Docker inspection was denied socket access; this does not show that Firecrawl is absent.

This spec is a synthesis, not an implemented feature. The first deliverable remains a small model/tool-loop learning example. Producing a complete spec does not remove the requirement to review milestones together.

The current workspace is not a Git repository and provides no project issue-tracker destination or triage configuration. Run `/setup-matt-pocock-skills` to supply that configuration. After the proposed test boundary is confirmed and the tracker destination is known, publish this spec with the `ready-for-agent` triage label. No issue has been created or labeled yet.
