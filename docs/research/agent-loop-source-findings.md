# Agent loop source findings

Researched 26 November 2026. Research input, not an accepted architecture or implementation milestone. All five requested sources were accessible. Repository files were inspected without running their code, installing dependencies, or changing application behavior. This note distinguishes executable examples, captured observations, documentation, and recommendations; it does not benchmark this application.

## Source boundaries

The tutorial repository was inspected at commit `7fa90c39c56182d4248b27cf500d416da69914df`; the internals repository at `3d33e5b435955fb0901308634a49ab7fc251e2d9`. The commerce article's linked reference implementation was additionally inspected at `fd4d59224ab96b43c6dc6888207c67b3bd5a24cf`. File links below pin those versions. Web documentation is the version retrieved on the research date.

## Agentic AI Engineering: useful teaching examples, incomplete production curriculum

The [minimal loop](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/01-foundations/05-agent-loop/01_minimal_agent.py) creates fresh history per goal, calls the model, appends the assistant response, executes requested tools, and appends correlated results. It has ten iterations, a per-command timeout, and explicit human confirmation before shell execution. The shell runner uses `shell=True`; confirmation is not isolation. This illustrates the essential feedback cycle, not a durable conversation or production permission model.

The [orchestrator/workers example](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/02-effective-agents/04-orchestrator-workers/01_orchestrator_workers.py) makes a planning call, parallel worker calls through `ThreadPoolExecutor`, then a synthesis call. Workers receive individual research prompts and can use one server-side web search. It implements independent research decomposition, not a rule that every request must enter a specialist. Planning and synthesis remain serial overhead. The requested two-to-four workers are a prompt instruction; the schema does not enforce array bounds, and executor size comes from the returned plan. Failed workers are logged and omitted from synthesis inputs. These details matter when adapting the idea: fan-out, partial completion, and failure visibility need explicit policy.

The [streaming agent](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/03-advanced-techniques/02-streaming/02_streaming_agent.py) renders text and tool-status indicators during generation, but waits for the final model message before executing tools sequentially. Its simulated weather is deliberately not live data. Streaming display therefore must not be confused with concurrent or eager tool execution. This distinction is directly visible in `run`, `_stream_response`, and `_execute_tool_calls`.

The [context example](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/03-advanced-techniques/03-context-engineering/01_context_engineering_anthropic.py) uses the provider token-counting API, a deliberately small context budget, and a model summary of older messages while retaining four recent messages. Counting and summarization introduce their own requests. It recounts afterward but does not enforce a final fit guarantee or demonstrate tool-exchange-safe summarization: this example is text chat. It is a teaching example for bounded context, not evidence of lossless memory.

The [loop-engineering chapter](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/05-loop-engineering/README.md) is explicitly forthcoming. Skills, hooks, sandboxing, MCP, child loops, tool-safe compaction, and its extensible-agent capstone describe intended lessons. The inspected repository tree has README files for those lessons, not their promised implementation. The frameworks and production chapter directories likewise contain README material. A broad table of contents is not proof of a completed harness.

### Evaluation examples need particularly careful interpretation

The [benchmark runner](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/04-testing-evaluation/07-eval-harness/eval_harness/benchmark.py) generates latency and token counts randomly and calculates accuracy from configured model and task-difficulty modifiers. Its Pareto calculation demonstrates an evaluation method; its numbers are **not measured model performance** and cannot justify a model choice or predicted speedup.

The [graders](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/04-testing-evaluation/07-eval-harness/eval_harness/graders.py) check keyword and source-ID substrings. Both can pass with half the expected matches. The composite combines those two scores while reporting tool usage separately. An answer containing expected words and IDs could still misstate the evidence, so these are cheap signals rather than grounded correctness checks.

The [Langfuse example](https://github.com/agenticloops-ai/agentic-ai-engineering/blob/7fa90c39c56182d4248b27cf500d416da69914df/04-testing-evaluation/06-eval-frameworks/03_langfuse.py) unconditionally constructs `SimulatedLangfuse` and evaluates canned responses. Even its branch detecting real credentials only prints a message; the execution path remains simulated. It teaches tracing and scoring shapes but does not demonstrate a functioning exporter or real model experiment. Prefer actual platform integration evidence when assessing this project's observability.

## Agentic Apps Internals: captured traffic, not product implementation

The [research method](https://github.com/agenticloops-ai/agentic-apps-internals/blob/3d33e5b435955fb0901308634a49ab7fc251e2d9/RESEARCH.md) describes AgentLens interception of provider requests. It captures prompts, schemas, messages, timings, and token usage for specific agent versions, modes, and tasks. This is primary evidence of the authors' observations, not first-party documentation of the products' full internals. Network-visible traffic cannot establish every local state transition or implementation detail.

The [Claude session](https://github.com/agenticloops-ai/agentic-apps-internals/blob/3d33e5b435955fb0901308634a49ab7fc251e2d9/claude-code-cli/agent-mode/session.md) separates six main model requests from two security-review and two overhead requests. Its [CSV](https://github.com/agenticloops-ai/agentic-apps-internals/blob/3d33e5b435955fb0901308634a49ab7fc251e2d9/claude-code-cli/agent-mode/log/session.csv) records request durations and first-token times, while the summary separately reports cache reads and cache creation. This supports measuring the complete request chain instead of counting only visible agent turns. Fresh input alone understates processed context. However, these are isolated observations, not a controlled latency distribution.

The [Codex session](https://github.com/agenticloops-ai/agentic-apps-internals/blob/3d33e5b435955fb0901308634a49ab7fc251e2d9/codex-cli/agent-mode/session.md) uses a different model and date. Its wall time cannot rank architectures against the Claude session without controlling task, environment, settings, context, and repeated trials. The capture's tool-use and prompt documents are also version-specific observations rather than current product contracts.

Crucially, [result/agent-loop.py](https://github.com/agenticloops-ai/agentic-apps-internals/blob/3d33e5b435955fb0901308634a49ab7fc251e2d9/claude-code-cli/agent-mode/result/agent-loop.py) is the standalone script the observed agent was asked to create. It streams text, requests human approval for shell commands, and returns tool outputs to a loop. It is **not Claude Code's source code**. Copying it would reproduce a small tutorial task output, not the captured application's harness.

## Claude Agent SDK: documented harness behavior

The [official loop guide](https://code.claude.com/docs/en/agent-sdk/agent-loop) documents repeated model/tool cycles, lifecycle and result messages, permissions, session continuation, caching, and automatic compaction. Application consumers should inspect terminal result status: reaching a limit differs from completing the task. A final answer needs its own turn allowance. Spend limits are checked after responses and can overshoot; they are not prepaid hard reservations. Subagent spend contributes to the documented budget.

The guide also distinguishes partial streaming events from complete responses. Tool schemas, instructions, history, and tool output all consume context. Skills can load on demand, and subagents can return compact results instead of entire transcripts. Compaction can lose earlier details; permanent instructions need a persistent source. These are SDK capabilities, not evidence that adopting the SDK automatically preserves an application's domain state, consent rules, or unfinished writes. Its documented behavior should be verified against a pinned SDK/runtime before implementation.

## Commerce article: one shared conversation, measured performance

Anthropic's [commerce guide](https://claude.com/resources/articles/the-anatomy-of-effective-commerce-agents), dated 2 September 2026, recommends a shared agent with skills for tightly connected commerce tasks rather than routine domain handoffs. It preserves narrow, self-contained research delegation as an exception. Its deployment comparisons and recall improvement are reported experience, not a reproducible benchmark for this application.

The article prioritizes completed-task quality, fewer model rounds, independent tool parallelism, faster backends, eager dispatch, stable cache prefixes, and visible progress. Model and effort selection should use quality floors plus latency and cost distributions. Streaming improves perceived progress without necessarily reducing completion time. It calls for positive/negative and cross-capability evals. Its asynchronous memory extraction recommendation is a product policy choice, not authorization to infer and save preferences here. Permission enforcement belongs in application code. These recommendations motivate experiments; they do not establish that replacing a framework or adding agents is beneficial.

### What the linked reference actually implements

The [shopping runtime](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/shopping-agent/runtime-messages-api/shopping_agent_runtime/orchestrator.py) builds stable prompt/tool material once, prefetches preferences/account/cart/memory concurrently, and constructs per-turn context. It loops with an explicit tool-iteration budget and reserves a final text-only round. It exposes text, progress, and UI events. On termination it pairs open tool calls with results or errors, which addresses interrupted-stream history validity. Its host passes session state and stores mutated message history; the runtime does not make those persistence choices disappear.

The shared [turn implementation](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/commerce-common/commerce_common/turn.py) starts tool tasks when complete JSON arrives at a tool-block boundary. It tracks them by call ID, joins with `asyncio.gather`, and avoids dispatching the same ID twice within that round. This overlaps execution with remaining generation; it is not a durable exactly-once guarantee across crashes. Cancellation and malformed-stream recovery are explicit concerns. Its `compact_history` replaces old tool-result text after a token threshold until serialized history is approximately halved. That is clearing evidence, not semantic summarization; provenance remains in separate session state. This strategy would need reconciliation with any requirement to retain or recover detailed earlier evidence.

The [tool executor](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/shopping-agent/core/shopping_agent/executor.py) delegates domain operations to a backend, records returned products/orders in session provenance, bounds inputs, and translates unavailable/not-offered results into actionable tool outcomes. This makes tool boundaries domain operations rather than raw database access. It is an implementation pattern; business backend correctness still belongs to the deployment.

The [merchant gates](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/merchant-agent/core/merchant_agent/gates.py) check session-known listing/campaign/change IDs, variant requirements, current change limits, and host approval when configured. A model instruction to apply a change is insufficient. Staging and applying are different operations. The useful transferable principle is authoritative state and checks at execution, not importing commerce-specific approvals wholesale.

The shared [memory module](https://github.com/anthropics/commerce-agents/blob/fd4d59224ab96b43c6dc6888207c67b3bd5a24cf/commerce-common/commerce_common/memory.py) has retention, validation/write filtering, subject-scoped stores, bounded extraction, and a purge-generation check before storing asynchronously extracted facts. It also retains an explicit `save_memory` tool path; the code is more nuanced than a blanket claim that every memory write must be asynchronous. Extraction failures return no new facts and log a warning. The shopping runtime's separate `update_memory` must be invoked by the host after streaming; it is not an automatic durable background job. Its extraction path must not override this repository's explicit-save preference policy.

## Jev article: optional bounded judgment inside a controlled workflow

Vercel's [Jev agent-control article](https://vercel.com/i/jev-agent-control), dated 18 September 2026, positions Jev as a typed decision model, not a prose generator or tool executor. Application code owns validation, permission checks, execution, and state. Independent questions about the same evidence may share a request; questions needing newly fetched evidence cannot. Tool failures should remain distinct from empty successful results, with bounded retries and repeated-action limits.

Its specialist-routing example records the decision for workflow replay. Routing inside a tool leaves delegation optional for the parent; routing before the parent makes it mandatory. This is a choice, not a universal requirement. Typed output and probabilities do not guarantee correctness or grant permission. The page offers architectural examples, not measured evidence that a Jev layer improves this application's end-to-end latency, quality, or cost. Additional routing would need to earn its extra dependency and call through evaluation.

## Questions these sources support testing

These are research deductions, not design decisions or established deficiencies in this application:

- Which requests finish correctly without delegation, and which benefit from isolated research context? Measure any extra planning, handoff, and synthesis rounds.
- Where does task time go: queueing, model generation, tool execution, retries, context work, persistence, or reply delivery? Distinguish first useful progress from completed output.
- Can independent reads overlap while dependent steps and stateful writes retain correct ordering? Streaming presentation alone does not answer this.
- Do small tool results preserve necessary evidence and source identity? Can earlier facts be recovered after context reduction?
- Do outcome evals detect unsupported claims, incomplete multi-part tasks, missing evidence, and inappropriate actions, rather than only keywords or tool invocation?
- When changing models, prompts, caching, or routing, does quality stay above an agreed floor across repeated trials, including failures and tail latency?

No source establishes a numerical performance improvement for this repository. Local traces and representative evaluations are required before selecting changes.

## Comparison with this application

Audited application revision: `714da7583df2eecaecb37aae904b5c6710e1042b`, branch `codex/orchestrator-specialists`. This comparison is a read-only architecture assessment, not a comprehensive security audit. The preceding E2E run and its exported observations provide examples, not a benchmark distribution. No application behavior was changed for this research.

### What is already worth retaining

- **Real research loop:** [research/agent.py](../../research/agent.py) gives Pydantic AI actual search/read tools and uses its bounded `run()` loop. Optional query planners remain disabled by default. There is no need to write a replacement framework loop to obtain this behavior.
- **Authoritative domain state:** Django storage holds case facts, draft versions, approvals and scheduled work independently of model conversation. [gmail/followups.py](../../gmail/followups.py) rechecks correspondence before sending, preserves the approved recipient/thread, and reserves a send attempt before external handoff. [gmail/followup_store.py](../../gmail/followup_store.py) prevents blindly repeating an uncertain attempt. Keep these guarantees when changing the model-facing interface.
- **Useful context controls:** [conversations/compaction.py](../../conversations/compaction.py) wraps framework summarization with budget checks and failure preservation; [conversations/context.py](../../conversations/context.py) records original-message and summary provenance. [conversations/recall.py](../../conversations/recall.py) provides bounded owner-scoped excerpts. Compaction is not the durable preference store.
- **Zero-model paths:** [core/lifecycle.py](../../core/lifecycle.py) can handle supported help and preference-inspection messages directly. Authenticated buttons identify approved operations without asking a model to reinterpret them. There is no universal final paraphrasing call.
- **Actual observability and experiments:** [research/evaluation/experiment.py](../../research/evaluation/experiment.py) uses Langfuse's dataset experiment runner with real SDK scores. This is stronger integration evidence than the tutorial's simulator, although the graders remain deliberately coarse.

### Where the current architecture falls short

1. **Two interpretation stages are mandatory for many case turns.** [core/routing.py](../../core/routing.py) first chooses a specialist. [grievances/intake.py](../../grievances/intake.py) then generates an `IntakeAction`; [grievances/service.py](../../grievances/service.py) dispatches through Python branches. This is a hybrid workflow containing model calls, not a general grievance loop that observes each tool result and freely selects its next tool. Deterministic workflows are useful, but the terminology should be accurate.

2. **The model boundary does not coincide with the user's task boundary.** The router reads at most four recent general-conversation messages and 2,000 characters, plus selected-case presence. It does not see a unified projection of pending case work. The added `conversation_question` field repairs one mixed case/general query shape; it does not solve arbitrary combinations of preferences, attachments, case work and research. Separate ownership tables remain appropriate; separate interpretation calls for every ownership boundary are not automatically necessary.

3. **The action schema carries unrelated concerns.** [grievances/schema.py](../../grievances/schema.py) puts drafting, Gmail connection, reminders, research, case facts and outcomes into one model with many optional fields. This makes correctness depend on combinations of flags and downstream branches. A future tool interface should expose small domain operations with validated input/result types over the existing services; avoid a new general workflow engine.

4. **Some language validation doubles as product interaction design.** [grievances/progress_store.py](../../grievances/progress_store.py) and intake authorization use phrase matching. Exact quotes establish textual provenance, not semantic authorization by themselves. The recent closure fix is a useful regression repair, but adding a phrase for every incident cannot become the long-term language interface. Preserve code-enforced ownership/approval rules; use typed proposed actions and a clear confirmation surface for consequential ambiguity. Do not replace execution permissions with a model judge.

5. **Presentation remains coupled to strings.** [core/turns.py](../../core/turns.py) carries reply text; [telegram_bot/presentation.py](../../telegram_bot/presentation.py) extracts buttons from special Markdown lines and formats only bold/code. This explains why some links and lists have inconsistent rendering. A small typed reply containing text, actions and trace reference would remove parsing conventions while leaving Telegram-specific rendering in its adapter. It need not require another LLM call.

6. **Budgets and waiting are fragmented.** There are local time/request limits and some shared specialist usage, but the router, case interpretation, secondary conversation and public research do not all consume one turn-wide budget. [subscriptions/service.py](../../subscriptions/service.py) serializes access through a shared lock. This prevents competing session use but can add foreground waiting behind background model work. Instrument waiting before deciding whether to prioritize foreground work or change concurrency; do not remove the lock speculatively.

7. **Scheduled notifications break the review trail.** [gmail/monitoring.py](../../gmail/monitoring.py) creates a new application trace when delivering an already prepared notification. The Telegram URL can therefore show delivery rather than the earlier model/tool decisions that produced it. Store an originating trace reference and link execution, notification and case/session identifiers. Do not pretend two operations separated by a scheduler are one continuously running span.

8. **Current evaluation coverage does not establish robust conversational quality.** [grievances/routing_evaluation.py](../../grievances/routing_evaluation.py) has five scenario families and several substring/path checks. [gmail/evaluation.py](../../gmail/evaluation.py) invokes `CaseService` directly, bypassing the top-level router, and a draft check depends on the phrase `Nothing was sent`. These can test component contracts but miss the actual composition and penalize harmless wording changes. The research graders explicitly disclaim semantic correctness. The 437 passing deterministic tests are valuable, not a measured user-task success rate.

### Concrete latency evidence from the latest run

| Request | End-to-end application trace | Generations | Observed generation durations |
| --- | ---: | ---: | --- |
| Help/introduction | 0.147 s | 0 | none |
| Save a preference | 5.401 s | 2 | 2.304 s router; 2.915 s preference interpretation |
| Mixed case fact + earlier conversation recall | 7.354 s | 3 | 2.535 s router; 2.456 s case interpretation; 2.071 s conversation |
| Create short Gmail reply | 17.428 s | 3 | 1.750 s router; 5.647 s case interpretation; 7.472 s drafting |

Trace references: [help](http://localhost:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/f47e964ec8eda6341679a88c4fccd553), [preference](http://localhost:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/fb00adee2b85974eb537ed9bad0629c6), [mixed recall](http://localhost:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/e352e6cce7a15b443767264bd1ce636f), [draft](http://localhost:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/5f568e5ebc67985355b0d5c965830f7c). These measured generations report zero reasoning tokens. That does not prove an explicit reasoning-off configuration or rule out provider/internal waiting. Subscription usage is not an observed per-call dollar bill. Do not copy API pricing into reported subscription cost.

### Proposed improvement sequence — not yet approved implementation

1. **Create the comparison baseline first.** Turn observed failures into reusable state snapshots: selected case plus unrelated question; corrected fact plus concise draft; attachment without a caption; disconnected Gmail with a pending goal; offer received just before send; changed draft after approval; negated closure; stopped/deleted work; long context. Exercise the public application boundary with controlled external services and a real model. Keep ordinary phrasing and deliberate paraphrases. Check final state, all parts of the reply, provenance, buttons and absence of unintended side effects.
2. **Measure complete tasks.** Record code/prompt/model versions, supported effort settings, queue wait, model calls, input/cached/output tokens, tools, retries, first useful response, completion time and correctness. Run repeated comparable trials, with modest sample-size caveats for tail percentiles. Keep deterministic contract tests separate from semantic scoring and live integration checks. Publish experiments in Langfuse and make relevant checks a release gate.
3. **Run a narrow architecture experiment.** Compare the current router/specialists with one conversation-owning Pydantic AI loop using scoped tools over the same services. Begin with preferences, read-only case facts and mixed questions. Keep writes, approvals and privacy checks unchanged. A shared conversation view must contain only authorized task-relevant state, not every case/email. Retain isolated research and other expensive self-contained work where useful. A tool may return a completed user-facing receipt without an extra synthesis pass when no interpretation is needed.
4. **Adopt only demonstrated improvements.** Measure whether fewer decision calls actually reduce successful-task time without degrading accuracy. A single loop can still need multiple model/tool turns. Likewise a specialized decision model may be worth testing later, but adding Jev on top of existing classification is not a latency fix. Keep the user's selected GPT-6.2 Sol baseline unless a model change is approved.
5. **Improve contracts and observability independently.** Typed replies, origin-linked scheduled traces, a turn-wide budget, and clearer error/retry results can be introduced without rewriting the domain layer. Optimize only independent reads in parallel. Keep current-state rechecks around external writes; apparent duplicate reads can prevent sending against stale evidence.

The recommended direction is one coherent conversation owner with modular tools and selective delegation, subject to evaluation—not deleting the working application and not declaring another architecture intrinsically superior. Durable cases, Gmail integration, attachments, approvals, scheduling and memory are reusable assets.

### Ideas not to copy blindly

- Do not use educational randomized benchmarks or isolated captured sessions to pick a production model.
- Do not migrate to the Claude SDK merely because its loop documentation is clear; Pydantic AI already provides the needed model/tool loop here.
- Do not replace explicitly requested/approved preference writes with inferred asynchronous memory. An explicit successful-save reply must follow a committed write. Background suggestions would need separate product semantics, deletion protection and failure reporting.
- Do not copy reference compaction that discards old tool text when our policy requires evidence retention and bounded recall.
- Do not disable repeated evidence checks at Gmail handoff just to reduce tool counts.
- Do not require an evaluator model after every generation by default. Start with outcomes and failing decisions; an evaluator call itself consumes latency and can be wrong.

## Measured routine-tool experiment — 26 November 2026

Implemented a reversible, opt-in alternative (`ROUTINE_TOOLS_ENABLED`) to the
router-only baseline: one structured model response selects a preference operation,
a read of selected-case facts, or delegation to an existing specialist. Existing
preference authorization/storage and case ownership/deletion checks execute the
proposal. There is no additional model call to rewrite a completed tool receipt.
This is a narrow exception to the default router-only design in ADR 001, not a
replacement of Gmail, drafting, scheduling or research workflows.

Eight fictional scenarios, three trials, two variants, alternating variant order:
48 real-model requests using the same subscription model, GPT-6.2 Sol. The six
optimization targets were save/change/list preferences, inspect purchase date,
inspect date plus preferences, and inspect an absent delivery date. Temporary
reply style and public-research delegation were guard scenarios. Web research was
a controlled boundary; no Gmail operations or real-case changes occurred.

| Targeted requests | Existing router + specialist | Terminal routine operation |
| --- | ---: | ---: |
| Median elapsed time | 4.9945 s | 2.7155 s |
| Model calls | 2 | 1 |
| Median total model tokens (Langfuse generations) | 3,126.5 | 1,601 |
| Outcome checks, including guard scenarios | 21/24 | 24/24 |

Latency fell 45.6%; median tokens fell 48.8%. This is a small local sample, not a
latency SLA or a subscription billing estimate. Some individual calls remained
slower, including a 5.30-second mixed inspection and a 6.21-second preference change.
Known greeting/preference-list shortcuts remain zero-model operations.

The pilot exposed a bad clarification for an explicitly temporary reply style.
After tightening the terminal prompt and adding the failing reply to evaluator
checks, the candidate handled it correctly in all three repeats. The unchanged
baseline instead misrouted that guard request to grievances in all three repeats;
these failures remain visible in the experiment. Candidate answers were manually
reviewed as well as checked for stored-state invariants and required answer facts.

[Full paired experiment](http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/experiments/results?baseline=a5d0dea44d0f367b)

Representative comparison:
- [Existing preference save](http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/2866c6e9b12021c4d1662cf0d911a590)
- [One-call preference save](http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/b3be3ee3740bdcf1bc618e41619ab877)
- [One-call mixed inspection](http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/5e5290fe426a94205e6d95d96072d605)

Rerun with `uv run python manage.py evaluate_routines --trials 3`. It creates
isolated fictional identities, checks resulting application state, and removes
those identities afterward. Langfuse retains the synthetic experiment and traces.
The candidate is enabled in this developer's local `.env`; the repository default
stays false for an explicit rollout. Setting it false and restarting restores the
baseline. Case writes, drafts and sending-plan selection are outside this experiment.

Scheduled notification links also now persist the originating work trace ID across
worker restarts; delivery spans record the origin as metadata. Older notifications
without an origin fall back to their delivery trace. Controlled public-worker tests
verify both paths through serialized native telemetry, including email privacy mode.
