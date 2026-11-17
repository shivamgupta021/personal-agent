# Personal assistant: evidence and a learning-first build plan

> Subsequent design decisions: [initial approach](../design/initial-approach.md) supersedes recommendations in this research note. The user selected direct ChatGPT subscription sign-in (not an API-credit-first route), confirmed an existing local Firecrawl installation, and included official-company web research in the refund-drafting MVP.

Researched 17 November 2026. This is a design note, not an implemented application. The repository had no existing source files, research convention, or `AGENTS.md` when inspected. Official documentation and upstream repositories are cited below; architecture choices and milestones are recommendations. Repository links point to moving `main` branches, not audited release snapshots. No repository was installed or executed, and no popularity ranking is implied.

## Recommendation

Build one Python/Django application, one Telegram entry point, and one Pydantic AI agent. Deliver a complete small feature before introducing specialist agents. Start with **explicit personal memory**: “Remember that I prefer vegetarian meals,” followed after a restart by “What food preferences have I told you?” Include correction and deletion. This exercises tools, persistence, user identity, conversation handling and traces without requiring a merchant integration. For the first substantive workflow, use that foundation to turn supplied order facts and an issue description into a tracked refund case and reviewable complaint draft.

A domain is not automatically an agent. Initially, memory, calendar and travel should be ordinary Python services exposed as typed tools. Introduce a specialist only when evaluation shows that a separate prompt, isolated context, different permissions or a different model improves a real task. Pydantic documents single-agent workflows, tool-based delegation, programmatic hand-off and graph control as different levels of complexity; delegation returns control to the parent. Its `SubAgents` capability gives delegates their own histories rather than the whole parent conversation. [Pydantic multi-agent patterns](https://pydantic.dev/docs/ai/guides/multi-agent-applications/)

**Why this order:** independently built “flight agent,” “food agent” and “calendar agent” demos would still leave identity, memory, approvals, retries and delivery untested together. A vertical slice proves the entire path. Keep modules independent; do not require them to become independent autonomous workers.

## What an agent actually does

The operational loop is: (1) send instructions, history and available tool schemas to the model; (2) receive an answer or structured tool call; (3) validate arguments and authorization, then execute the Python tool; (4) append its result to the model conversation; (5) ask the model again until it returns a final answer or reaches a limit. The model requests actions; application code performs them. Pydantic supplies this loop and bounds it with run limits. [Pydantic agents](https://pydantic.dev/docs/ai/core-concepts/agent/)

Recommendation: use deterministic Python workflow steps for known sequences such as quote → approval → purchase → receipt. A subagent is another model-driven loop with its own instructions/context; it is useful when reasoning benefits from that isolation, not merely because a workflow has several functions.

## What the selected stack supplies

| Component | Verified capability | Recommended role here |
| --- | --- | --- |
| Pydantic AI core | Agents support typed tools/output, dependencies and run limits; `UsageLimits` can bound model requests and successful tool calls. [Agent docs](https://pydantic.dev/docs/ai/core-concepts/agent/) | Own the model/tool loop. Do not rewrite that loop in Django. Set explicit small limits and timeouts. |
| Pydantic AI Harness | A separate capability package combines facilities such as memory, file access, shell and delegation. `Coder` includes unrestricted shell; local workspace is not isolation. Harness minor versions can change APIs. [Harness](https://pydantic.dev/docs/ai/harness/) | Use selected capabilities when needed, pin dependencies, and avoid adding `Coder` merely to obtain a general assistant. Prefer narrow application tools. |
| Django | Django supports async views and an async request stack under ASGI, with documented sync/async boundaries. [Django async documentation, version 5.2](https://docs.djangoproject.com/en/5.2/topics/async/) | Own users, durable records, admin inspection, tool services and eventual HTTP endpoints. Keep a local Telegram management command for the first slice. |
| Telegram | Bot API provides polling (`getUpdates`) and webhooks; polling cannot run while a webhook is configured. Failed webhook deliveries are retried and a configured secret appears in a request header. [Bot API](https://core.telegram.org/bots/api) | Begin with long polling. Store update IDs, restrict the bot to the owner's numeric user ID and private chat, and keep Telegram objects outside domain services. |
| Langfuse | Official integration configures the Langfuse SDK and enables Pydantic instrumentation using `Agent.instrument_all()`, exporting OpenTelemetry spans. [Integration](https://langfuse.com/integrations/frameworks/pydantic-ai) | Trace the first real request. Correlate chat, run and tool execution IDs; redact credentials and sensitive personal data before export. |

Recommendation: keep synchronous ORM transactions inside synchronous service functions and cross the async boundary deliberately. An async agent call does not make a database transaction or background job durable. Choose and pin a supported Django release when implementing; the linked version documents the boundary, not a latest-version recommendation.

## ChatGPT subscription authentication: a real option, with constraints

Current official guidance is more nuanced than “ChatGPT subscriptions cannot be used by an application.” OpenAI documents Sign in with ChatGPT for local/open-source applications, including registration, PKCE, explicit token-sharing permission (`chatgpt.tokens.use.direct`), model discovery and OAuth-backed Responses requests. This is an application sign-in flow, not copying browser cookies or scraping a ChatGPT session. [OpenAI Sign in with ChatGPT cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)

Pydantic now documents `OpenAIChatGPTProvider`, `OpenAIChatGPTOAuthFlow` for sign-in and refresh, and use with `OpenAIResponsesModel`. It recommends approval for paid or remotely hosted applications. Its documented preview constraints include `store=false`, no suspend/resume, and web search as the supported native tool. Custom local function tools are supported separately from provider-hosted native tools; supply conversation history as required by the preview. Lack of provider suspend/resume does not prevent application-owned history or pending approvals surviving restart. [OpenAI preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) [Pydantic ChatGPT plan provider](https://pydantic.dev/docs/ai/models/openai-chatgpt/)

OpenAI also documents a separate API-credit benefit for eligible paid Pro/Business customers who link an owned API organization; that benefit uses ordinary API billing/keys and has expiration and eligibility conditions. Plus is not eligible for that particular benefit. This must not be confused with direct plan-usage authentication. [ChatGPT API credits](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)

Recommendation: for a bot running on your own machine, try the documented native Pydantic ChatGPT provider first. Before building further, verify sign-in, refresh, available models, one local function call and a two-turn conversation. Persist history locally where the provider requires it. Confirm the actual subscription plan and intended hosting model. A local bot polling Telegram is different from deploying a public multi-user service. OpenAI explicitly supports your own self-hosted VM as well as your laptop; this differs from offering a third-party hosted service. For a paid or third-party hosted offering, resolve provider approval/terms or use an API-key route before deployment. Sign-in does not import ChatGPT conversations or account context: this assistant must acquire its own authorized data and memory. [OpenAI local/open-source token sharing](https://developers.openai.com/siwc/token-sharing-open-source), [self-hosted VMs](https://developers.openai.com/siwc/token-sharing-open-source/self-hosted-vms) Do not substitute Codex app-server authentication indiscriminately: its published guidance restricts that auth mechanism for commercial/hosted services. [OpenAI app-server authentication](https://learn.chatgpt.com/docs/app-server)

## What to learn from existing projects

### nanobot: closest channel-to-assistant reference

Its architecture separates `nanobot/bus/events.py` and `bus/queue.py`, channel-facing `agent/loop.py`, model-facing `agent/runner.py`, `agent/context.py`, `session/manager.py` and `agent/memory.py`. `AgentLoop` owns session/workspace selection and outbound delivery; `AgentRunner` owns provider requests, tool execution and termination. [Upstream architecture](https://github.com/HKUDS/nanobot/blob/main/docs/architecture.md)

The actual runner imports a tool registry/executor, context-governance machinery, provider conversation state and cancellation helpers: these concerns are explicit source boundaries, not merely README promises. [Runner source](https://github.com/HKUDS/nanobot/blob/main/nanobot/agent/runner.py)

Recommendation: borrow the separation of channel, turn orchestration and context construction. Let Pydantic supply the provider/tool loop instead of transplanting nanobot's. Do not infer that a nominally small assistant remains a tiny loop once recovery, compaction and multiple providers arrive.

### OpenHands: execution and conversation boundaries

OpenHands separates `openhands.sdk`, optional `openhands.tools`, extended `openhands.workspace` implementations and `openhands.agent_server`. Applications consume SDK/server interfaces; local and sandboxed deployment are distinct. [SDK architecture](https://github.com/OpenHands/docs/blob/main/sdk/arch/overview.mdx)

The agent source consumes `ConversationState`, action/observation events, cancellation tokens, validation errors and security-analysis components. [Agent implementation](https://github.com/OpenHands/software-agent-sdk/blob/main/openhands-sdk/openhands/sdk/agent/agent.py)

Recommendation: learn from explicit action/result events and execution boundaries, especially if browser or shell automation arrives. Do not import a software-development agent's broad workspace permissions into personal bookings. A flight quote and a shell command should have different authority and execution policies.

### OpenClaw: a gateway and multiple surfaces

Its architecture uses a long-lived gateway for messaging surfaces, typed WebSocket requests/events and schema validation. Device nodes advertise capabilities and have device pairing. [Gateway architecture](https://github.com/openclaw/openclaw/blob/main/docs/concepts/architecture.md) The repository describes models and agent harnesses as replaceable plugins. [README](https://github.com/openclaw/openclaw/blob/main/README.md)

Recommendation: copy the conceptual separation between transport, identity and execution. Avoid building its whole gateway/plugin/device ecosystem for a one-person Telegram bot. Adding another chat channel should require a transport adapter, not duplicating the assistant's business logic.

### Letta: memory is more than a long transcript

The current Letta memory documentation distinguishes always-present `system/` memory from other files retrieved on demand. It describes versioned agent memory and background consolidation (“dreaming”). These are current SDK concepts; older memory-block tutorials may describe a different generation. [Letta memory](https://docs.letta.com/agent-sdk/memory), [upstream repository](https://github.com/letta-ai/letta)

Recommendation: borrow the distinction between stable user facts, conversation history and retrieved records. Start with structured, explicitly saved preferences and provenance; do not begin with automatic background memory rewriting or a vector database. A conversation summary is a retrieval aid, not authoritative evidence that the user authorized an action.

## Minimal application shape

The following is a proposed structure, not generated code:

```text
manage.py
pyproject.toml
config/                     # Django settings and URLs
assistant/
  models.py                 # Conversation, Message, AgentRun, MemoryItem
  admin.py
  agent.py                  # one agent definition, typed deps and tool bindings
  runner.py                 # run lifecycle, history loading/saving, errors
  observability.py          # trace configuration and redaction
  services/
    memory.py               # ordinary validated read/write operations
  channels/
    telegram.py             # normalize input and format output
  management/commands/
    run_telegram.py         # polling entry point
  tests/
evals/
  cases.jsonl
  run.py
docs/research/personal-assistant.md
```

Recommendation: add calendar/travel adapters only when building those slices. Separate `tools/` from `agent.py` once bindings become substantial. Introduce `PendingAction` and `ToolExecution` records with the first external write. SQLite is sufficient for an initial single-process exercise; choose a database/worker topology based on the first real concurrency requirement.

Recommended request path:

```text
Telegram update → owner check + deduplication → persisted input
  → application runner → Pydantic agent → narrow service tool
  → persisted result → Telegram reply
                       ↘ correlated Langfuse trace
```

Keep business state, agent conversation and telemetry distinct. The database answers “what happened and what is pending”; message history supplies model context; traces explain execution. A telemetry outage should not erase a confirmed booking or memory item.

## Stepwise milestones and completion criteria

All milestones below are recommendations. Advance after using the previous slice yourself.

1. **Provider and trace smoke test.** One local command authenticates, receives a reply, calls a harmless typed function and produces a trace. Restart it and verify auth refresh/history behavior. This validates the proposed stack before scaffolding several domains.
2. **Telegram personal memory.** Save, recall, correct and forget explicit preferences. Restart the process and repeat the query. Reject another Telegram user and handle the same update twice without writing twice. Finish with a small dataset of paraphrases and missing-information cases.
3. **First domain: refund-case drafting.** Accept user-supplied order facts and evidence; ask for missing required facts; save the case and return a complaint draft. Do not send it yet. Graduate when representative cases preserve correct order details, invent no evidence, and survive restart. Then optionally add **a useful read-only daily view**: Add a calendar connector and ask “What is my day like tomorrow?” Normalize time zones and show source event identifiers/links where available. State when the connector fails; do not replace missing results with invented meetings.
4. **One approved external action.** Create a calendar event from a preview with exact time zone, attendees and description. Demonstrate approval, rejection, expiration, duplicate callback and process restart. This teaches action reliability before handling money.
5. **Travel research.** Collect origin, destination, dates and constraints; query one real provider and return sourced, timestamped choices. Separate discovery/quote from reservation/payment. Do not describe a search link as an available bookable fare.
6. **One transaction domain.** Pick flights OR movie tickets OR food. Establish the specific provider API and access first, then implement quote → approval → commit → receipt/status. If only interactive browsing is available, treat login, changing UI and payment as a separate integration project. Do not assume those providers expose usable booking APIs.
7. **Grievance/refund casework.** Persist order, issue, evidence, draft, submission status and follow-up date. Begin with evidence gathering and a draft; later add explicitly approved sending/submission. Report “submitted” separately from “refund granted.”
8. **Only then consider specialists.** Split a measured difficult workflow, such as travel comparison, into a bounded worker with typed input/output and its own evaluation set. Keep purchases and messages behind the same application-owned action service.

## Approvals, retries and long-running work

Pydantic supports `requires_approval=True`, conditional `ApprovalRequired` and deferred requests/results. Its docs explicitly distinguish model approval from server authorization: the tool implementation must still validate the caller and allowed action. [Deferred tools](https://pydantic.dev/docs/ai/tools-toolsets/deferred-tools/)

Recommended first external-action contract: store a pending action with owner, exact normalized arguments, expiry and unique ID; show those same arguments in Telegram; resolve the callback against the server's record; mark execution atomically. A changed fare, attendee list, time or amount creates a new preview. A model saying “approved” is not approval evidence.

For operations with side effects, use provider idempotency keys when available and persist their receipts. If a request times out after being sent, query status before retrying; “unknown outcome” is a real state. Protect against two workers or two buttons executing the same action. A database commit and an external provider call do not form one atomic transaction.

Pydantic documents durable engines including Temporal, DBOS and others; they journal wrapped requests/tool calls and replay settled work after restart. It separately distinguishes persistence/checkpoints from durable execution and notes that coverage varies by engine. [Durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/)

Recommendation: persist simple state first. Add a worker when requests need background processing, and one durable engine when crashes, delayed approval or multiday workflows require automatic recovery. A queue alone is not an exactly-once transaction guarantee; retain domain idempotency whichever runtime is used.

## Evaluation before more agents

Pydantic provides `TestModel`, `FunctionModel`, model/dependency overrides and `ALLOW_MODEL_REQUESTS=False` for deterministic tests without accidental real requests. These check code paths; they do not prove a real model understands language. [Testing](https://pydantic.dev/docs/ai/guides/testing/)

Recommended evaluation layers:

| Layer | Example checks |
| --- | --- |
| Service tests | User isolation; correct memory update/deletion; timezone conversion; idempotent writes. |
| Orchestration tests | Duplicate update; malformed tool argument; tool failure; request limit; restart before reply; rejected/expired approval. |
| Real-model task cases | Preference paraphrases, ambiguity, conflicting old/new facts, missing dates, honest “unknown,” correct tool selection. |
| Adversarial cases | Retrieved text requesting secret disclosure; unauthorized user; forged approval; untrusted page asking to place an order. |
| Operational measurements | Task success, unnecessary tool calls, unauthorized actions, response time, request/token usage, provider errors and trace completeness. |

Start with 15–25 representative cases. Run deterministic checks on code changes; run the real-model set on prompt, provider or model changes. Manually review failures and retain them as regression cases. Enforce authorization and money limits in code rather than relying on a model-based grader. Use traces to explain failures, and compare any proposed specialist against the single-agent baseline on the same cases.

## Confirmed constraints and remaining decisions

The user reports a $100 ChatGPT plan and wants the MVP running on an M4 Pro MacBook Pro, postponing hosting selection. Keep deployment local; Telegram polling needs the process running and the computer awake and online. The selected provider runs the model; local model weights are not required.

If this is the eligible $100 Pro tier, OpenAI currently lists $100 monthly API credits, subject to account linking and eligibility. Verify the benefit in billing before assuming it is active. Recommendation: use ordinary API-key authentication if those credits are available; otherwise test direct ChatGPT plan usage. [Credit amounts and eligibility](https://developers.openai.com/api/docs/guides/chatgpt-api-credits)

- Verify the exact plan name and whether the API-credit benefit is activated.
- Confirm the first substantive workflow: recommended refund-case drafting after a minimal explicit-memory exercise; calendar lookup is an alternative.
- Specific calendar and later transaction providers. Their APIs, scopes, terms and account access must be verified when selected.
- Which personal data may be retained and exported to traces. Recommended default: explicit preferences only, deletion support, redacted telemetry, and secrets outside both prompts and history.

The next implementation session should deliver milestone 1 and the smallest memory slice, then extend it to refund-case drafting. Add a Case model and refund service for that domain; do not scaffold all future specialists.
