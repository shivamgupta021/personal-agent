# Personal Agent

A real terminal research agent using your ChatGPT subscription, Pydantic AI, and local Firecrawl. Give it a public research question; it chooses searches and page reads, receives their results, and returns a sourced answer.

## Run it

Requires Python 3.12+, uv, and your existing local Firecrawl service.

```sh
uv sync --locked
PYTHONPATH=src uv run python -m personal_agent models
PYTHONPATH=src uv run python -m personal_agent research "Find Python's official explanation of asyncio.timeout and explain deadline expiry with sources." --model gpt-6.1-sol
```

Use an available model slug from `models`. If you have not signed in, run `PYTHONPATH=src uv run python -m personal_agent login` first. Authentication uses the app's existing private `.local/chatgpt` credentials, refreshes them, and never falls back to an API key. The research command consumes subscription usage. Each invocation starts a fresh conversation; there is no cross-run memory yet.

Default Firecrawl address: `http://127.0.0.1:3002`. Override it with `FIRECRAWL_BASE_URL`; set `FIRECRAWL_API_KEY` only if your service requires it. `.env.example` lists settings. The app reads environment variables; it does not automatically load `.env` files.

Use public topics for this milestone. Terminal progress shows tool names and result sizes; the question and retrieved content go to the model, and searches go through Firecrawl's configured search backend. Instructions ask the model to exclude personal details, but this is not an enforced personal-data redactor or a real grievance intake system.

## Understand and review the flow

Start with `ResearchAgent.run()` in `src/personal_agent/research.py`. It opens a metadata trace, awaits Pydantic AI's `Agent.run()`, and returns the answer. Pydantic AI owns the model/tool loop, tool argument validation, and conversation history. The method no longer walks graph nodes.

Then read these modules:

| Module | Responsibility |
| --- | --- |
| `constants.py` | Application limits, retries, HTTP timeouts, and response sizes |
| `prompts.py` | Research instructions, including planning complementary queries |
| `firecrawl.py` | Execute planned searches, merge duplicate sources, read bounded page chunks |
| `logging_setup.py` | Configure application progress logging once |
| `telemetry.py` | App-lifetime Langfuse connection, isolated per-run metadata, export |
| `enums.py` | Fixed run statuses and known tool names |
| `errors.py` | Map failures to safe messages at the CLI boundary |
| `__main__.py` | Start services, connect the model/tools, display the answer |

The model generates one to three focused queries before its first search and passes them as a batch. Python executes each unique query sequentially and merges results by URL. Partial failures are returned with the usable results. This is an instruction and a bounded tool schema, not a separate planner agent or a guarantee that the model will always choose good queries.

Progress logs go to stderr; the final answer goes to stdout. Logs include tool names, result counts, and run status—not raw queries, URLs, tool results, or exception text. Controlled models exist only in tests.

See [the code walkthrough](docs/learning/research-agent.md) for async/await, context managers, agent types, tracing, and the reference repositories.

## Limits and failures

Research defaults: 8 model requests, 10 completed tool calls (Pydantic AI also checks a proposed batch before execution), 180 seconds for the run, 45-second model and Firecrawl HTTP timeouts, and one framework validation retry; defaults live in `constants.py`. SDK HTTP retries are disabled. A page read returns at most 16,000 characters; each search batch runs at most three unique queries and returns at most five compact results per query; downloaded Firecrawl responses are capped at 2 MB. Schema-validation failures still consume the model-request budget.

HTTP and malformed tool responses become fixed error results so the model can recover or explain missing evidence. Blocked/empty pages are not treated as verified evidence. Run limits, timeouts, and provider failures propagate to the CLI, which logs a sanitized message and exits with a nonzero code. Trace export has a separate five-second HTTP timeout and cannot replace an answer with an HTTP export error. Source correctness and prompt-injection resistance remain model-dependent; citations are not mechanically verified. Obvious local/private URL targets are rejected; DNS resolution and redirect protection remain the responsibility of the local Firecrawl deployment.

## Optional Langfuse tracing

Your local Langfuse UI is at `http://127.0.0.1:3000`. Create project API keys in that UI and export `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the shell running this app. Set `LANGFUSE_BASE_URL` if the address differs. Keep secrets in your shell environment or a Git-ignored `.env`, never committed files. Both keys are required to enable tracing.

The app initializes the shared tracing service once at startup and closes its connection on shutdown. A `ContextVar` keeps each asynchronous run's metadata separate, including when runs overlap. The app sends one metadata-only run span using Langfuse's [OTLP/HTTP JSON endpoint](https://langfuse.com/integrations/native/opentelemetry): start/end times, status, request/tool counts, token usage, and known tool names. It uses no automatic instrumentation, global tracing configuration, or Langfuse SDK. Prompts, answers, query/URL strings, page content, arbitrary model names, and raw exceptions are absent from the payload by construction. It does not provide individual model/tool spans or cost estimates yet. With no project keys, research works with tracing disabled.

The telemetry tests inspect serialized outbound HTTP bodies for privacy, export failure handling, concurrent-run isolation, and cancellation status. Live Langfuse project ingestion has not been verified because project keys have not been configured.

## Verification and scope

```sh
uv run mypy
uv run pytest
```

A real research run on 18 November 2026 used `gpt-6.1-sol` with the existing subscription credentials, searched through local Firecrawl, read Python's official documentation and source, and returned a cited explanation of timeout cancellation. No fabricated order lookup was involved. Tests cover the public run, repeat-run isolation, budgets, timeouts, source retrieval failures, long-page continuation, and outbound metadata privacy.

Milestone 2 is tracked in [GitHub issue #2](https://github.com/shivamgupta021/personal-agent/issues/2). Telegram, Django persistence, case intake, refund drafting, and external actions remain later milestones. Pause here to review and use the research agent before proceeding.

Prompts currently live in `prompts.py`. Langfuse-managed prompt versions are a possible next step; no prompt has been published or fetched from Langfuse, and project keys are still required for that integration.
