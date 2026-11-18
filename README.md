# Personal Agent

A real terminal research agent using your ChatGPT subscription, Pydantic AI, and local Firecrawl. Give it a public research question; it chooses searches and page reads, receives their results, and returns a sourced answer. The handwritten milestone 1 loop remains available for comparison.

## Run it

Requires Python 3.12+, uv, and your existing local Firecrawl service.

```sh
uv sync --locked
PYTHONPATH=src uv run python -m personal_agent models
PYTHONPATH=src uv run python -m personal_agent research "Find Python's official explanation of asyncio.timeout and explain deadline expiry with sources." --model gpt-6.1-sol
```

Use an available model slug from `models`. If you have not signed in, run `PYTHONPATH=src uv run python -m personal_agent login` first. Authentication uses the app's existing private `.local/chatgpt` credentials, refreshes them, and never falls back to an API key. The research command consumes subscription usage. Each invocation starts a fresh conversation; there is no cross-run memory yet.

Default Firecrawl address: `http://127.0.0.1:3002`. Override it with `FIRECRAWL_BASE_URL`; set `FIRECRAWL_API_KEY` only if your service requires it. `.env.example` lists settings. The app reads environment variables; it does not automatically load `.env` files.

Use public topics for this milestone. Queries and page URLs appear in your terminal; the question and retrieved content go to the model, and searches go through Firecrawl's configured search backend. Instructions ask the model to exclude personal details, but this is not an enforced personal-data redactor or a real grievance intake system.

## Understand and review the flow

Read these in order:

1. `ResearchAgent.run()` in `src/personal_agent/research.py`: starts a fresh run, prints each model-request step, observes requested tools, returns the final answer, and enforces limits. This is the main learning entry point.
2. `Firecrawl.search()` and `Firecrawl.read_page()` in `src/personal_agent/firecrawl.py`: actual HTTP requests to your service. Tools return compact evidence, URLs, retrieval timestamps, and sanitized errors. Long pages expose `next_offset`, which the model can use on another read.
3. `LearningApp.research()` in `src/personal_agent/__main__.py`: connects the subscription model, local tools, and optional tracing.
4. `LangfuseTrace.send()` in `src/personal_agent/telemetry.py`: the entire outbound trace payload is visible here. It receives usage counts, tool names, timing, and a fixed status—not message history or tool payloads.

The flow is:

```text
Your question → model request → tool call chosen by model
             → Python executes search/read → result added to history
             → next model request → more tools or final answer
```

Pydantic AI owns tool schemas, argument validation, dispatch, message history, and the model/tool cycle. Our code owns available tools, instructions, authentication, budgets, presentation, and tracing. `agent.iter()` lets us observe the real execution without rebuilding that framework loop. Printed steps describe observable execution, not the model's private reasoning.

The research command uses no scripted answers. Controlled models in `tests/test_research.py` make regression checks repeatable; they do not establish research quality. The old `demo` command and `demo-001` order are synthetic and can wait if you are reviewing the usable agent. See [the original handwritten lesson](docs/learning/handwritten-loop.md) for its walkthrough.

## Limits and failures

Research defaults: 8 model requests, 10 completed tool calls (Pydantic AI also checks a proposed batch before execution), 180 seconds for the run, 45-second model and Firecrawl HTTP timeouts, and one framework validation retry. SDK HTTP retries are disabled. A page read returns at most 16,000 characters; search returns at most five compact results; downloaded Firecrawl responses are capped at 2 MB. Schema-validation failures still consume the model-request budget.

HTTP and malformed tool responses become fixed error results so the model can recover or explain missing evidence. Blocked/empty pages are not treated as verified evidence. Run limits, timeouts, and provider failures stop with sanitized errors and a nonzero exit code. Trace export has a separate five-second HTTP timeout and cannot replace an answer with an HTTP export error. Source correctness and prompt-injection resistance remain model-dependent; citations are not mechanically verified. Obvious local/private URL targets are rejected; DNS resolution and redirect protection remain the responsibility of the local Firecrawl deployment.

## Optional Langfuse tracing

Your local Langfuse UI is at `http://127.0.0.1:3000`. Create project API keys in that UI and export `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` in the shell running this app. Set `LANGFUSE_BASE_URL` if the address differs. Keep secrets in your shell environment or a Git-ignored `.env`, never committed files. Both keys are required to enable tracing.

The app sends one metadata-only run span using Langfuse's [OTLP/HTTP JSON endpoint](https://langfuse.com/integrations/native/opentelemetry): start/end times, status, request/tool counts, token usage, and known tool names. It uses no automatic instrumentation, global tracing configuration, or Langfuse SDK. Prompts, answers, query/URL strings, page content, arbitrary model names, and raw exceptions are absent from the payload by construction. It does not provide individual model/tool spans or cost estimates yet. With no project keys, research works with tracing disabled.

The telemetry test inspects the serialized outbound HTTP body on both successful and failed exports. Live Langfuse project ingestion has not been verified because project keys have not been configured.

## Verification and scope

```sh
uv run mypy
uv run pytest
```

A real research run on 18 November 2026 used `gpt-6.1-sol` with the existing subscription credentials, searched through local Firecrawl, read Python's official documentation and source, and returned a cited explanation of timeout cancellation. No fabricated order lookup was involved. Tests cover the public run, repeat-run isolation, budgets, timeouts, source retrieval failures, long-page continuation, and outbound metadata privacy.

Milestone 2 is tracked in [GitHub issue #2](https://github.com/shivamgupta021/personal-agent/issues/2). Telegram, Django persistence, case intake, refund drafting, and external actions remain later milestones. Pause here to review and use the research agent before proceeding.
