# Gmail connection, case evidence, and draft export

Status: on 22 November 2026 the owner accepted the recommended local approach and authorized credential-independent development while postponing Google setup. Milestone 1 is implemented on its feature branch with controlled tests; live Google consent/profile/refresh/disconnect and real Keychain acceptance remain pending. Milestones 2 and 3 remain proposed. No inbox has been accessed or Google consent grant requested by the assistant.

## Goal

Connect one Gmail account to the local owner-only Django application, find relevant correspondence for a selected grievance case, and export a saved complaint draft into Gmail for manual review and sending. Preserve the existing distinction between case facts, company correspondence, public policy evidence, and owner-reported sending.

## Design constraints

- Retain the root-level Django layout, class-based services, separate constants/prompts, subscription model access, and local Firecrawl/Langfuse.
- Add a `gmail` app with a small application service, OAuth/credential lifecycle, and a bounded Gmail API client. Do not expose arbitrary Google endpoints to the model or place mailbox tools on the public-web researcher.
- Start with `gmail.readonly`. Draft export later requires fresh desktop authorization for the full `gmail.readonly` + `gmail.compose` scope set. Compose also permits sending at Google's permission boundary; application code must expose no send operation. These permissions are not case-scoped.
- Use Google's maintained OAuth/authentication libraries, desktop authorization-code flow with PKCE and loopback callback. Validate OAuth state and granted scopes. The user completes Google account selection and consent in their browser.
- Recommend a dedicated Cloud project for this application and Mac Keychain storage for refresh/access credentials. Do not put tokens in `.env`, PostgreSQL, source control, model input, telemetry, or diagnostic output. `.env` may contain the path to the ignored desktop client configuration. No silent plaintext fallback if secure storage is unavailable.
- Google publishing/verification settings and Workspace administrator policies are external setup requirements, not guarantees supplied by this application. Explain the short token lifetime of external apps in Testing when Gmail scopes are requested.

## Milestone 1: connect, inspect status, disconnect

Provide thin management commands calling the Gmail service: `gmail_connect`, `gmail_status`, and `gmail_disconnect`.

Connection checks the granted permissions and Gmail profile before saving a usable connection. Bind the connection to the configured application owner and show the connected mailbox locally. A failed/cancelled replacement connection preserves the previous working one; changing mailbox must be explicit. Prevent concurrent refresh/write operations from corrupting credentials. A restart reuses stored credentials and refreshes when necessary; a revoked or expired grant produces an actionable reconnect message without retry loops.

Status exposes connection health and granted capability names, never credentials. Connection/status does not fetch email messages, invoke a model, or change a case. Disconnect disables application access and removes local credentials; report whether Google revocation succeeded. If revocation fails, state that the owner must remove access in their Google account rather than claim that remote access was revoked. Disconnect does not silently erase imported case evidence or Gmail drafts.

Acceptance: controlled OAuth/HTTP responses and temporary credential storage through the public command/service boundary cover successful consent, cancellation, denied/partial scopes, identity mismatch, refresh/restart, concurrent access, revoked grants, secure-store failure, and truthful disconnect. A separate owner-assisted live connection verifies browser consent, profile, refresh, and disconnect/reconnect. Never use the real Keychain token in deterministic tests.

## Milestone 2: case-scoped correspondence search

An explicit request such as “Search Gmail for correspondence about this selected case” authorizes bounded search of the connected mailbox. Use company, order reference and user-supplied constraints; those private identifiers may go to that owner's Gmail search endpoint, never to Firecrawl. No background mailbox synchronization or broad mailbox scan.

Present a bounded candidate list with stable message/thread references, sender, subject, date and short preview. Ask the owner to select the relevant evidence before attaching it to a case. Read only bounded text from selected messages, with clear truncation and missing-content indicators. Initial scope excludes attachments, remote image fetching and executing HTML. Do not infer case facts or lasting preferences automatically from email.

Preserve source mailbox/message/thread IDs and selected content provenance alongside case-scoped evidence. An email claiming to be from a company is correspondence, not independently verified company policy. Email text, signatures, quoted history and instructions are untrusted evidence; they cannot authorize deletion, sending, preference changes or access to another case. Feed selected evidence only to private case processing; native private trace content remains suppressed.

Before enabling model-assisted reading, explain and confirm that selected email content is sent to the configured model provider and may appear in owner-directed Telegram replies. Running Django locally does not keep those copies local. Review Google's applicable user-data requirements and the provider's data handling for this route; do not assume that suppressing Langfuse content establishes compliance. Real email must not enter developer evaluation datasets.

Local case deletion removes imported copies and references and invalidates in-flight work. It does not delete the original Gmail message. Reimporting evidence requires a fresh explicit owner action; background work cannot recreate it after deletion. Disconnect blocks new searches without erasing existing evidence.

Acceptance: public owner-turn tests exercise selection, ambiguous matches, no results, pagination/budgets, wrong account/case, revoked access, duplicated imports, malicious message content, truncation, deletion races and outbound/telemetry privacy. Native synthetic Langfuse experiments assess query relevance, evidence selection, faithful attribution and clarification. A separate live smoke check uses messages the owner explicitly selects.

## Milestone 3: export a saved version into Gmail Drafts

Export only after an explicit request naming the saved draft version and an owner-confirmed recipient. Preview the exact subject/body and recipient before first export. Do not guess recipients from a company name, add CC/BCC, or claim that export sent anything. A reply draft additionally requires an explicitly selected Gmail thread and the valid reply headers; otherwise clarify rather than silently select a thread.

Keep the immutable application draft unchanged and record its Gmail draft mapping, connected account, case/version, export status and content fingerprint. Replaying a completed export returns the existing mapping. Exporting a new version is explicit; it does not overwrite the user's edits to an existing Gmail draft or silently delete an older one.

Treat a timeout after create as an uncertain outcome. Do not blindly retry a non-idempotent create. Reconcile only when the original operation can be reliably identified; otherwise surface an uncertain export for owner inspection. Do not promise exactly-once creation based solely on a local receipt or a guessed Message-ID behavior.

Case deletion removes local exported-content references but does not delete Gmail drafts or sent mail. Explain that external copy boundary. Do not automatically infer sending or resolution by exporting a draft; retain existing explicit owner-reported progress.

Acceptance: public export-turn tests cover exact version/recipient/body, account changes, retries and ambiguous outcomes, existing human edits, reply threading, deletion races, missing grants, no send endpoint and private payload exclusion. Synthetic Langfuse experiments check intended version/recipient/thread selection; live acceptance creates one owner-approved disposable draft for review without sending it.

## Delivery and unresolved setup

Dependency order: connection → case search/evidence → draft export. Each milestone receives its own branch, deterministic checks, applicable real-model evaluation, independent review and live acceptance before the next. The first milestone needs no model-quality experiment because it has no model invocation. Preserve the agreed public Telegram turn boundary for model-driven milestones.

The owner authorized the first milestone and credential-independent tests while deferring setup. Confirm account type/setup at live acceptance and candidate-selection/draft-export behavior before later milestones. The Cloud project and browser consent cannot be completed merely by connecting a Gmail plugin to Codex. No consent or access to actual email is inferred from the existence of that plugin.

Research evidence is recorded in [the Gmail research note](../research/gmail-integration.md); the scope and implementation recommendations here are application design proposals, not Google guarantees.
