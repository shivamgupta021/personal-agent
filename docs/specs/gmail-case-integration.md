# Spec: Gmail correspondence, drafts, reply checks, and reminders

Published specification: [GitHub issue #43](https://github.com/shivamgupta021/personal-agent/issues/43).

## Problem Statement

The owner has a local Telegram personal assistant with research, persistent conversation, explicit preferences, grievance cases, versioned complaint drafts and manually recorded progress. Handling a real grievance still requires manually finding emails, copying correspondence into the assistant, and moving generated text into Gmail. Full email drafts in Telegram are cumbersome to review. The owner also needs to know whether a company replied and when to follow up, without confusing an unsent draft with a sent complaint or automatically contacting companies.

Implementation checkpoint, 23 November 2026: the read-only connection (#41) and natural Gmail correspondence flow (#44) are implemented and live-tested. The owner explicitly delegated selection of clearly relevant threads; ask only when the match is ambiguous. Draft creation (#45) is implemented with passing controlled tests, synthetic real-model experiments, and owner-approved live compose authorization followed by Telegram-to-Gmail unsent-draft acceptance. Revisions preserving manual Gmail edits (#46), verified sending/reply checks (#47), monitoring (#48), and reminders (#49) remain open. This specification describes the full target, not a claim that every capability is delivered.

## Solution

Connect the owner's personal Gmail to the existing assistant. On request, find correspondence for a grievance case and choose the clearly relevant thread and ask the owner only when matching is ambiguous. Use that selected evidence to prepare complaint or follow-up drafts directly in Gmail. Telegram gives a concise plan when needed, asks only for unresolved choices, and returns a short confirmation with a usable draft link or a mailbox/subject fallback.

The owner sends messages manually in Gmail. On-demand checks identify new replies and distinguish verified sending from owner-reported sending. Later, explicit case monitoring and one-time no-response reminders operate locally while the Mac and worker run, preserving state across restarts. No incoming email can authorize actions on the owner's behalf.

## User Stories

1. As the owner, I want to connect my personal Gmail through Google consent, so that the assistant can use the mailbox I choose.
2. As the owner, I want the connected mailbox and capabilities shown without credentials, so that I can check account selection safely.
3. As the owner, I want connection failures and expired grants explained clearly, so that I know when to reconnect.
4. As the owner, I want an unsuccessful replacement connection to preserve the working connection, so that a cancelled login does not break access.
5. As the owner, I want deliberate disconnection to stop access and report revocation failures truthfully, so that I understand what remains authorized at Google.
6. As the owner, I want to search Gmail for a selected grievance case, so that I can find prior order and complaint correspondence.
7. As the owner, I want a bounded candidate list with sender, subject, date and short preview, so that I can select the correct thread.
8. As the owner, I want ambiguous or missing matches explained, so that unrelated emails are not silently attached to a case.
9. As the owner, I want my selected correspondence thread reused for later case requests, so that I do not repeatedly select it.
10. As the owner, I want imported correspondence to retain its source references, so that I can distinguish email evidence from my own account and public research.
11. As the owner, I want unread attachments and truncated content identified, so that the assistant does not imply it reviewed evidence it never read.
12. As the owner, I want a one-time explanation of email processing by the model provider, so that local execution is not mistaken for local-only data processing.
13. As the owner, I want multistep requests briefly explained before execution, so that I understand the plan without approving each already-requested action again.
14. As the owner, I want simple requests answered without a planning preamble, so that Telegram stays concise.
15. As the owner, I want complaint drafts created directly in Gmail, so that I can review and edit them where I will send them.
16. As the owner, I want follow-up drafts based on selected correspondence and current case facts, so that responses address the actual company reply.
17. As the owner, I want recipient ambiguity clarified before a draft is created, so that private case details are not addressed to the wrong person.
18. As the owner, I want reply drafts associated with the selected thread, so that the conversation remains coherent in Gmail.
19. As the owner, I want a concise draft confirmation with recipient and version, so that I can verify the result without reading the full draft in Telegram.
20. As the owner, I want a verified draft link or a clear mailbox/subject fallback, so that I can find the draft on my device.
21. As the owner, I want requested revisions created as new drafts, so that earlier drafts and my manual edits remain intact.
22. As the owner, I want completed exports recognized on replay, so that repeating delivery does not create another identical draft.
23. As the owner, I want uncertain creation outcomes reported rather than blindly retried, so that a timeout does not silently multiply drafts.
24. As the owner, I want sending to remain manual, so that preparing a draft never contacts a company automatically.
25. As the owner, I want on-demand checks of selected case threads, so that I can learn whether a company replied.
26. As the owner, I want verified sent messages distinguished from my own sent reports, so that the assistant does not infer sending from draft disappearance.
27. As the owner, I want new replies summarized concisely without automatic drafting, so that I decide how to respond.
28. As the owner, I want monitoring enabled only when I request it for a case, so that selecting a thread does not start background access.
29. As the owner, I want monitoring to catch up after restart, so that temporary downtime does not permanently hide relevant replies.
30. As the owner, I want reminders at a date and time I supply or approve, so that the assistant does not invent deadlines.
31. As the owner, I want reminders confirmed in Asia/Kolkata and managed conversationally, so that I can inspect, move or cancel them easily.
32. As the owner, I want a substantive reply to cancel a no-response reminder, so that I am not told to chase a company that already responded.
33. As the owner, I want automated acknowledgments distinguished from substantive replies and uncertainty clarified, so that reminders are not cancelled incorrectly.
34. As the owner, I want overdue reminders checked against new replies before delivery, so that restarting the Mac does not produce stale notifications.
35. As the owner, I want failed checks to preserve state and report unknown reply status, so that an outage is not mistaken for no reply.
36. As the owner, I want stopping monitoring, closing or resolving a case to cancel pending monitoring and reminders, so that discontinued work stays stopped.
37. As the owner, I want reconnecting or reopening to require explicit resumption, so that background work does not silently restart.
38. As the owner, I want case deletion to remove imported application copies and invalidate pending work, so that background checks cannot recreate deleted case evidence.
39. As the owner, I want Gmail originals and drafts preserved during local case deletion, so that application cleanup does not unexpectedly alter my mailbox.
40. As the owner, I want ordinary conversation and public research to continue working, so that Gmail integration does not force every message into a case workflow.
41. As the owner, I want rerunnable synthetic Langfuse experiments and inspectable traces, so that I can review quality changes without putting real email in evaluation datasets.

## Implementation Decisions

- Preserve the root-level Django apps, class-based services, separate prompts and constants, existing subscription access and local Firecrawl/Langfuse setup. Extend existing conversation, grievance, draft, ownership and deletion services rather than creating a separate assistant with divergent state.
- The application coordinator routes between ordinary conversation, public research and case operations. It presents concise plans for multistep work and requests clarification only for unresolved case, thread, recipient or timing choices. Gmail access is exposed through bounded private case services; the public-web researcher receives no mailbox tools or private email context. Public policy research continues through the reviewed public brief.
- Draft writing is a dedicated case-generation responsibility with separate instructions. It consumes selected correspondence, current case facts and applicable reviewed research. It must preserve uncertainty, distinguish quotations from owner facts, avoid invented evidence/attachments and return content for Gmail creation. This does not require another orchestration-model call for every step.
- Use one owner-bound Gmail connection. Retain Desktop OAuth with PKCE, state checks, bounded loopback callback, expected-account verification, exclusive credential operations, refresh and macOS Keychain storage without plaintext fallback. Configuration may contain a client-file path; credentials never enter models, database records, logs or traces.
- Request read-only plus compose scopes together during initial connection and validate the granted capabilities. This replaces staged consent at the owner’s request on 25 November 2026. Existing read-only grants still require an upgrade before drafting. Compose grants sending authority at Google's boundary; the application must expose no send operation or arbitrary Gmail request tool. Personal Gmail has no configured Cloud project/OAuth client yet; live setup remains a human prerequisite.
- Search uses case-related company/order terms and owner constraints against the connected Gmail account. Private identifiers never go to Firecrawl. Bound pagination, fetched candidates, decoded content and model-visible text. For ordinary requests for help with an existing case, the assistant resolves the case, searches when no correspondence is saved, and selects a clearly relevant preview without requiring the owner to copy an internal reference. Ambiguous or unrelated matches require a natural clarification before reading full text. Full selected text becomes case-scoped correspondence. Preserve mailbox, message/thread identifiers, timestamps and provenance; imports are deduplicated.
- Reuse the selected thread for subsequent requests, but selection is not monitoring consent. Do not automatically turn email assertions into confirmed case facts or saved preferences. Treat subjects, bodies, HTML, quoted history and signatures as untrusted evidence, not instructions. Initially support text only; do not fetch attachments, remote images or execute HTML.
- Explain and obtain acknowledgment of selected-email processing by the configured model provider before enabling it. Relevant excerpts may appear in the owner's private Telegram chat. Keep real email out of evaluation datasets and exported telemetry, including derived quoted content, prompts and tool payloads. The existing private debug capture switch must not bypass this email-specific exclusion. Implement and verify that boundary before importing real email; it is not currently guaranteed by the connection-only code.
- Use synthetic email for fully readable development traces. Real-email runs retain safe operation/status/timing/usage metadata and explicit content-omission indicators so reviewers can distinguish suppression from empty results. Credentials are always excluded. Native Pydantic AI instrumentation remains the default rather than a hand-built agent loop.
- A request to draft authorizes creation of an unsent Gmail draft after required recipient clarification; no full-body Telegram approval or separate export command is required. Confirm a new recipient unless already supplied/selected. For an unambiguous selected thread use its reply address; no inferred reply-all or unsolicited CC/BCC. Reply creation supplies the selected thread and required reply headers/subject.
- Retain immutable application draft versions and account-bound Gmail mappings with export state and content fingerprints. A revision creates a new Gmail draft and preserves all earlier Gmail drafts and human edits. Completed operation replay returns its saved mapping. Persist intent before creation; uncertain HTTP outcomes remain uncertain until reliably reconciled. Do not assume exactly-once creation or a provider idempotency guarantee.
- Telegram returns a short draft result with recipient, version and a verified specific-draft link where supported. Otherwise give mailbox and subject for Gmail Drafts. Mobile deep links are not universally guaranteed. Never label a created draft as sent or a case as resolved.
- On-demand reply checks inspect selected case threads. Establish sending from an unambiguous matching sent message or explicit owner confirmation; preserve distinct provenance. A missing draft is not evidence of sending. Clarify ambiguous matches. Incoming replies do not resolve cases automatically; the owner must request a follow-up draft.
- Monitoring requires per-case opt-in. A local worker polls selected threads approximately every five minutes while the Mac/application run. Persist cursors, seen-reply identifiers, reminder state and notification progress to support bounded catch-up and duplicate suppression. No always-on delivery guarantee exists while the Mac is asleep or worker stopped.
- Reminder deadlines require owner instruction or accepted suggestions. Confirm exact date/time in Asia/Kolkata and clarify ambiguous timing. Reminders are one-time notifications with conversational inspect/move/cancel operations. Before overdue delivery after restart, check relevant replies. Substantive replies cancel no-response reminders, not cases; obvious acknowledgments preserve them; uncertainty requests clarification.
- Failed checks retain state and report unknown status, with bounded temporary retries and no repeated identical failure alerts. Stop monitoring, resolve, close or delete cancels monitoring and pending reminders. Deliberate disconnection requires explicit resumption after reconnecting; reopening does not resume automatically. Check ownership/deletion state at external handoff so stale in-flight work cannot notify or recreate deleted evidence.
- Local case deletion removes imported content, references, export mappings and monitoring state, including application-managed conversation copies as required by existing deletion guarantees. It does not delete Gmail originals, drafts or sent messages. Disconnect stops new access but preserves already imported case evidence. Reimport requires fresh owner action.
- Deliver in sequence: finish connection live acceptance; case search/selected evidence and coordinator UX; direct Gmail drafts and revisions; on-demand reply checks/sending evidence; local monitoring/reminders. Each is a separately reviewable learning milestone. This spec publication does not create implementation tickets or authorize skipping their review gates.

## Testing Decisions

- Reuse the verification boundary explicitly accepted in the Gmail grilling: public Telegram owner turns for interactive behavior and the public scheduled-worker entry point for time-driven behavior. Use real temporary database storage, controlled Gmail/model/Telegram responses and a controllable clock. Connection lifecycle uses its existing public management-command/service boundary with isolated credential storage. No additional interview is needed for these already-approved seams.
- Test externally visible answers, durable state, outbound requests, exported telemetry and notifications; do not test private call ordering or prompt wording as substitutes for behavior. Prior art is the connection lifecycle suite, application routing regressions, conversation restart/forgetting tests, grievance draft/version/deletion tests and native tracing payload checks.
- Connection checks cover consent/state/PKCE, cancellation, partial scopes, account mismatch/replacement, refresh/restart, competing access, revoked credentials, secure-store failures and truthful disconnect. Later compose scope expansion must preserve working state on failed authorization.
- Search/import checks cover bounded results/pagination, no matches, ambiguous selection, account and case isolation, repeated imports, truncation, unread attachments, malicious email content and explicit selection. Assert private query identifiers never reach Firecrawl, and email content never reaches trace/exported evaluation payloads even with private debug capture enabled.
- Draft checks cover exact version/facts/recipient/body, selected-thread headers, corrected amounts, unread evidence, preserved human edits, replay, uncertain create outcomes, account changes, missing grants, usable link/fallback, deletion races and absence of all send requests.
- Reply/worker checks cover sending provenance, ambiguous matches, substantive replies versus acknowledgments, uncertain classification, duplicate observations, restart catch-up, overdue relevance checks, reminder inspect/move/cancel, timezone interpretation, failures, disconnection, closure/reopening and deletion during in-flight work.
- Keep ordinary conversation, persistent follow-ups, preferences and public research as integration regressions. A successful Gmail test cannot substitute for verifying those existing behaviors.
- Use native Langfuse datasets/experiments with synthetic mail and real models to evaluate routing, query relevance, faithful case/draft content, recipient/thread selection and reply classification. Rerun after relevant prompt/model/tool changes and compare experiment results. Deterministic pass scores do not establish semantic quality; inspect outputs and record human assessment. No real inbox data becomes an evaluation fixture.
- Live acceptance remains separate: owner-assisted OAuth/profile/refresh/disconnect/reconnect with real Keychain; owner-selected mail search/import; one approved disposable unsent Gmail draft and verified link/fallback; selected-thread reply checking; an explicitly requested reminder. Inspect fresh traces, including visible safe payloads or documented email-redaction indicators. Do not claim live success from mocks or from a Telegram reply alone.

## Out of Scope

- Automatic email sending, arbitrary Gmail operations, reply-all by inference, mailbox deletion, broad inbox management or synchronization.
- Attachment retrieval/analysis, remote image fetching, email HTML execution or unsupported evidence claims.
- Overwriting or automatically deleting existing Gmail drafts, including drafts edited by the owner.
- Pub/Sub webhooks, hosted workers, guaranteed notifications while the Mac is unavailable, recurring reminders and unsolicited follow-up drafting.
- Multiple mailboxes/users, generalized travel/calendar/food agents, monetization or broad public OAuth distribution.
- Automatic case resolution, automatic durable preference extraction from emails, or treating correspondence as verified public policy.

## Further Notes

This synthesizes the accepted Gmail grilling decisions Q1–Q20 and the owner's subsequent existing-application review completion. The later decision to create a new draft for every revision supersedes the initial same-draft-update idea; direct Gmail review supersedes a full Telegram preview/export approval step.

The earlier [primary-source research](../research/gmail-integration.md) supplies API background; its preliminary UX recommendations are superseded where they differ from this spec. Provider-specific behavior must be checked against official documentation during implementation, particularly OAuth permissions, reply threading, uncertain draft creation and draft links.

The connection issue remains open pending live acceptance. Keep that status honest and do not duplicate its implementation in a new ticket. Publish this spec as `ready-for-agent`; separate ticket slicing and dependency approval precede implementation. The owner has completed the requested review checkpoint; it is no longer a blocker to planning.

### Conversational acceptance correction — 23 November 2026

The owner rejected command-shaped acceptance tests and delegated clear thread selection to the assistant. Test ordinary messages such as “Help me get my Hosteller refund” and “What about my licence approval?” through the running Telegram UI. An existing named case can be selected within that request. Responses explain the email evidence and next step, not candidate IDs or storage operations. Saved correspondence must not be described as a fresh mailbox check. Email claims remain attributed evidence, and sending, monitoring, outcomes and preference changes are not authorized by reading mail. No full email drafts belong in Telegram.

This repair covers existing cases. New-case discovery, refreshing correspondence, and creating Gmail drafts are not established by these checks. The previous explicit reference command remains a compatibility path, not the acceptance workflow.

### Local development tracing override — 24 November 2026

The owner explicitly requested readable real-email traces during local development, superseding the earlier unconditional telemetry exclusion for this opt-in mode. `LANGFUSE_CAPTURE_EMAIL_CONTENT=true` together with both general/private capture flags enables payload capture only for a loopback Langfuse endpoint. Default and remote-endpoint email exclusion remain. Gmail operation spans include bounded request/result data; credentials remain outside trace payloads. This does not authorize putting real email into evaluation datasets. Restart running processes after changing settings.


## 24 November refinement

The owner approved automatic monitoring during active case assistance after relevant correspondence is selected. Confirm activation in the reply; preserve an explicit previous stop. This supersedes earlier opt-in-only monitoring wording. Exact reminder deadlines remain owner-requested or explicitly accepted, and sending evidence remains required. Consolidated schema and architecture review is deferred until the building phase is complete; then perform an isolated fresh-user demo. See `docs/architecture/data-model.md`.
