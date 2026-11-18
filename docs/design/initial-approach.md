# Initial approach — design interview

Status: the user requested implementation and confirmed the milestone-1 testing boundary. Milestone 1 is implemented; subsequent milestones await the agreed learning checkpoint. Updated 17 November 2026.

## Confirmed context

- Personal use and a showcase project; no current monetization goal.
- User is comfortable with Python and Django.
- Use class-based, object-oriented Python for core application behavior, with focused responsibilities, composition and injected dependencies. The user explicitly requested this before further tickets; conventions are recorded in CODING_STANDARDS.md.
- Run the MVP on the user's M4 Pro MacBook Pro; defer hosting selection.
- Use direct ChatGPT subscription sign-in. The user has a $100 subscription and has used subscription authentication successfully before. Do not make API-credit eligibility or activation a prerequisite.

## Round 1 — accepted decisions

1. The broader domain is grievance redressal. Begin with refund requests to keep the first feature small: collect missing facts, save a grievance case, prepare a complaint draft, and return to the case later.
2. Build a tiny model/tool-loop learning example before using Pydantic AI for the application.
3. The first version may save and edit its own local records. The user performs external actions manually. Sending, submission, and negotiation are later milestones.

The prior research note supplies recommendations; it does not override decisions recorded here. The memory exercise is a learning step toward the grievance workflow, not a separate first product.

## Round 2 — accepted decisions

1. Accept typed or pasted text in Telegram, including order details and company replies. Attachments and inbox integration are deferred.
2. Prepare an email-style complaint for company customer support, with a subject, factual account, requested refund and references to supplied evidence.
3. Include web research in the MVP using local Firecrawl. This replaces the proposed supplied-facts-only boundary.
4. Support multiple saved grievance cases, one active case per conversation, and explicit selection when a message could refer to more than one case. Provide listing and switching.

## Round 3 — accepted decisions

1. Research official company refund policies, complaint procedures and support contacts. Defer legal research. Preserve source URLs and retrieval dates; distinguish policy wording from its applicability to the case.
2. Search automatically using company names and general issue descriptions. Do not put personal details, order numbers or private correspondence into search queries.
3. If research fails or no reliable policy is found, allow a factual complaint draft with the unverified points clearly identified. Do not invent policy entitlements.
4. Langfuse receives execution metadata, with case content excluded: tool names, timing, usage, errors and internal case identifiers. Exclude raw messages, drafts, credentials and personal details. Use synthetic cases for full-prompt inspection during development.
5. Retain cases until explicitly deleted. Case deletion covers locally stored case messages, drafts and research, but does not imply deletion of copies held by Telegram or model providers.

## Round 4 — accepted decisions

1. Allow the user to mark a complaint as sent, paste a company reply, and request a follow-up draft within the same case. The user explicitly marks resolution or closure without resolution. Automatic monitoring and reminders are out of scope.
2. Preserve prior drafts after material corrections, flag them as outdated, and produce revised drafts. Ask for clarification when facts conflict without an explicit correction.
3. Begin with roughly 20 realistic evaluation cases covering missing/conflicting facts, ambiguous case references, failed research, unsupported policy claims, corrections and deletion. Require zero observed cross-case disclosures or fabricated amounts/policy entitlements in that set, plus manual usefulness review. This is a development gate, not a guarantee of general reliability.
4. The user confirms Firecrawl is already set up locally. Reuse and verify the existing installation; do not plan a new installation by default. Verify search and page retrieval after the minimal loop exercise and before real-case integration.

## Local Firecrawl findings

Official self-hosting documentation supports search. Current upstream search source selects configured Fire-engine, configured SearXNG, or a DuckDuckGo fallback; pin and test the chosen release rather than assuming current main behavior. Independently check returned source domains before labeling them official. [Self-hosting guide](https://docs.firecrawl.dev/contributing/self-host), [search source](https://github.com/firecrawl/firecrawl/blob/main/apps/api/src/search/v2/index.ts).

The user confirms an existing local installation. Its endpoint and behavior have not yet been independently verified: no deployment manifests were found in this project, and a read-only Docker inspection was denied socket access. The connected Firecrawl plugin is not evidence that a local service is running. No service was installed or changed.

## Round 5 — accepted decisions

1. Flag conflicting official sources or uncertainty about purchase-time terms. Preserve sources and ask for relevant historical terms when needed. A factual request may still be drafted without claiming a verified entitlement.
2. Build one small milestone at a time, inspect its behavior together, and pause before the next milestone. Start with a minimal model/tool loop showing the request, arguments, tool execution and subsequent model response; introduce Pydantic AI afterward.
3. Use direct ChatGPT subscription sign-in, per the user's explicit correction. The proposed API-credit-first route is rejected. Do not add automatic paid fallback or recharge.

## Agreed implementation sequence

1. Minimal inspectable model/tool-loop learning example using subscription authentication and a harmless tool with synthetic data.
2. Move the loop into Pydantic AI and add metadata-only Langfuse tracing; use synthetic cases for full-content debugging.
3. Add the owner-only Telegram interface and Django persistence, including the small explicit-memory learning exercise, case selection and deletion.
4. Add refund-case intake, local Firecrawl research, sourced email-style drafts, corrections and draft versions.
5. Add manual sent/outcome updates and follow-up drafts; run the agreed evaluation cases and review usefulness.

This sequence organizes the accepted scope. Pause for shared inspection at each milestone. No further product design questions are currently open.

## Milestone 1 implementation checkpoint

- The public loop is exercised with controlled responses, alongside CLI and simulated streaming transport checks.
- Subscription sign-in, model discovery and a real two-request tool-call round trip succeeded using an account-listed model. The read-only synthetic order returned INR 1200; no real grievance or external action was involved.
- Pydantic's OAuth/provider helper supplies authentication only. The loop is handwritten and does not use its Agent harness.
- The live check exposed SDK-only parsed fields being replayed as API input. Raw streaming events now preserve API-compatible output; a regression test guards that boundary.
- Credentials are ignored by Git and stored in an owner-only directory with owner-only files. No API-key fallback is configured.
- The next checkpoint is inspecting this loop together, before adding Pydantic Agent or Langfuse.

## Learning-first simplification

After reviewing milestone 1, the user found the architecture too complex and authorized simplifying it before moving to new tickets. One `Agent` class now contains a direct `run()` loop and a method for the synthetic lookup. It prints progress directly, returns an answer string and raises safe errors on failure. Separate limits, event, result and demo-tool classes are removed. Model transport and authentication remain separate, all imports remain at module scope, and execution/credential protections are retained. Review this change against baseline `df5fb7b`, confirmed by the user.

## Remaining integration checks for later milestones

- Discover and verify the existing local Firecrawl endpoint and search/page-retrieval behavior.
- Integrate direct ChatGPT subscription authentication and verify this application's tool-call behavior without recording secrets in project files or traces. This is an integration check, not a renewed debate about subscription access.
- Check restart persistence, duplicate Telegram update handling, owner-only access and trace redaction during implementation.

No ADR yet: the decisions so far are readily reversible and do not warrant one. Terminology is recorded in the root GLOSSARY.md.
