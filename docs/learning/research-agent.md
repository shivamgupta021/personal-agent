# Reading the research agent

The application has one real agent. Pydantic AI manages its model/tool loop; our code supplies policy, tools, and application services.

## Start with the run method

`ResearchAgent.run(message)` now has one tracing scope and one `await self.agent.run(...)` call. Read it first, then the constructor, `prompts.py`, and the two Firecrawl methods. Tracing internals can wait.

The flow is: question + instructions + tool schemas → model requests a tool → Python validates and executes it → tool results join conversation history → model requests more tools or answers. A new call starts with fresh history.

## Why async?

`async def` defines a coroutine. Calling it creates work that must be awaited or scheduled; it does not automatically start another thread. At an `await` on network IO, the current task can pause while the event loop runs other ready tasks. Here both model requests and Firecrawl calls spend time waiting on the network.

We could provide a synchronous application, but the libraries already support async IO, which also fits future Telegram handling. Async does not make blocking Python code nonblocking. A `time.sleep()` or CPU-heavy loop inside an async function can still block its event-loop thread.

## What does timeout mean?

The previous `async with asyncio.timeout(...)` set a deadline for the enclosed work. On expiry, it cancelled the current task and translated that cancellation into `TimeoutError` when leaving the context. It did not stop the event-loop thread or guarantee that blocking code would be interrupted immediately.

Now the research call passes `timeout=constants.RUN_TIMEOUT_SECONDS` to Pydantic AI. Pydantic AI owns the whole-run deadline and raises `RunTimedOut`, a `TimeoutError` subtype. Model and web HTTP timeouts separately bound network operations. The tracing exporter has its own short deadline, so trace export time is separate from research time.

## What were iter, node, and assert?

`Agent.iter()` exposes Pydantic AI's execution graph through an asynchronous context manager. `async for node in run` advanced through stages such as model requests and tool execution. `Agent.is_model_request_node(node)` was a type-checking predicate: it asked whether a node represented that stage. It did not call the model itself.

We used this lower-level interface to print per-request progress. That was unnecessary complexity for the application. `Agent.run()` drives the same engine and returns its completed result. Tool and lifecycle logs provide progress without inspecting graph internals.

`assert run.result is not None` was an assertion statement, not a function. It checked an assumption after manual iteration and helped the type checker narrow an optional result. Assertions can be disabled by Python optimization; they should not validate user input. The assertion is gone because `run()` returns a completed result directly or raises.

## What does Agent[None, str] mean?

It is a Python generic type annotation:

- `None`: we are not passing a Pydantic AI runtime dependency object through `RunContext`.
- `str`: a successful run produces a string answer.

The model and Firecrawl objects are still ordinary constructor dependencies. `Agent[None, str]` is not a separate research-specific class or an additional agent.

## Why sequential=True?

Pydantic AI can execute multiple tool calls concurrently. A tool registered with `sequential=True` is scheduled sequentially rather than in parallel. We retain it to keep local service load predictable. The HTTP requests within one search batch also run sequentially.

This does not force search-before-read, choose the model's plan, or make the code synchronous. The model still chooses what to request. `parallel_tool_calls=False` separately tells the model provider not to produce parallel tool calls.

## Why instrument=False?

`agent.instrument` controls Pydantic AI's automatic OpenTelemetry instrumentation. Automatic instrumentation can include messages, tool arguments/results, and other content depending on its configuration. This project explicitly disables it because we have a metadata-only export policy.

Our separate tracing service remains active when credentials are configured. Turning off automatic instrumentation does not disable the agent, tool execution, or our manual trace export.

## Why start time, status, and an enum?

Langfuse needs start/end timestamps to display a run's timing. These now live in `RunTrace`, inside `telemetry.py`.

`RunStatus` in `enums.py` defines the allowed statuses. A particular run still needs its own status value: one run may succeed while another is cancelled. Making the current status a shared mutable global would mix them up. The old `Literal` restricted the type checker to a set of strings; the enum gives those choices reusable names and runtime values.

## Shared tracing and logging

`tracing` is one module-level service instance. Importing it performs no network IO. `ResearchApp.start()` initializes its HTTP connection once and closes it on shutdown. Agents import this service; they no longer receive or construct tracing clients.

Each `tracing.observe()` scope creates fresh run metadata, and a `ContextVar` makes that metadata local to the current asynchronous context. Tools call `tracing.record_tool(ToolName.SEARCH)` without passing trace objects around. Sharing a client is safe here; sharing mutable run data would not be.

The existing explicit OTLP/HTTP JSON exporter is retained. We have not added Langfuse SDK automatic capture. Only the allowlisted fields reach Langfuse, and outbound-payload tests verify that boundary.

Python logging is configured once. Progress goes to stderr with level and module name. Final answers and model listings use stdout; interactive sign-in instructions remain terminal output. We avoid logging raw queries, URLs, page content, secrets, or exception strings.

## Error boundaries

- Invalid URLs and HTTP failures become structured tool results, allowing the model to try another source or explain the gap.
- Invalid tool arguments are handled by Pydantic AI's validation and retry policy.
- Run timeouts, usage limits, and provider failures propagate. The CLI calls `user_error_message()` to present safe errors and return a nonzero exit code.
- Tracing records status and re-raises the original exception. Expected trace transport failures are logged without discarding a successful research answer.

There are still narrow exception handlers where parsing and network IO can fail. Those are explicit boundaries, not scattered wrappers around ordinary agent logic.

## Query planning and prompts

The prompt asks the model to form one to three focused, complementary queries from the question before searching. The `search` tool accepts that list, validates its size, skips duplicate queries, and deduplicates source URLs. The model may refine its queries later. No separate planning agent or additional compulsory model request is needed.

This improves the available workflow; it does not prove query quality. Controlled tests validate batching, limits, result merging, and partial failures. Live runs and future evals assess actual research quality.

Prompts live in `prompts.py`. Langfuse prompt management could supply a versioned prompt later, with a checked-in fallback and a defined version/label policy. It is not connected yet; there are no project keys configured. Moving prompts into Langfuse is separate from turning tracing on.

## References used in this refactor

- [LearnHarness architecture](https://github.com/amosblomqvist/learnharness/blob/main/docs/architecture.md) separates agent construction, tools, providers, and application services. Its [teacher agent](https://github.com/amosblomqvist/learnharness/blob/main/src/agent/teacher-agent.ts) uses the Pi runtime and separate prompt composition. We use those boundaries as inspiration, not its much larger runtime.
- [Treg](https://github.com/superdesigndev/treg) is a tool/credential registry and proxy, not a Pydantic AI research-agent example. Its [configuration module](https://github.com/superdesigndev/treg/blob/main/src/treg/config.py) illustrates centralized configuration.
- Local reference: `emailzap-archive/emailzap/backend/execfn/common/utils/langfuse_setup.py` configures tracing once and supplies shared helpers. We adopted the lifecycle pattern, not its raw input/output capture or inline imports.
- [Pydantic AI agents](https://pydantic.dev/docs/ai/core-concepts/agent/) and [timeouts](https://pydantic.dev/docs/ai/core-concepts/timeouts/) describe the high-level run API and deadline behavior. Implementation was also checked against the installed 2.69.0 sources.
- [Langfuse SDK overview](https://langfuse.com/docs/observability/sdk/overview) describes shared client access, and [prompt management](https://langfuse.com/docs/prompt-management/get-started) is a separate integration.
