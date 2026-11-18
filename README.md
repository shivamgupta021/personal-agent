# Personal Agent

A local Django project with a real research agent using your ChatGPT subscription, Pydantic AI, and your existing Firecrawl service. Django supplies application startup, settings, logging, and management commands. Case persistence, HTTP endpoints, and Telegram remain later work.

## Run locally

```sh
uv sync --locked
uv run python manage.py check
uv run python manage.py models
uv run python manage.py research "Find Python's official explanation of asyncio.timeout and explain deadline expiry with sources."
```

Research defaults to `gpt-6.2-sol`. Use `--model` with an available slug from `models` only for an explicit comparison. Run `uv run python manage.py login` if sign-in is required. Existing credentials remain in `.local/chatgpt`; no API-key fallback is used. `--state-dir` remains available.

Each research command starts fresh conversation history and consumes subscription usage. Django automatically loads `.env` from the project root, regardless of the working directory. Copy `.env.example` to `.env` if needed and fill in your local configuration. Existing shell environment variables take precedence. The `.env` file is ignored by Git. Firecrawl defaults to `http://127.0.0.1:3002`; override it with `FIRECRAWL_BASE_URL`. Set `FIRECRAWL_API_KEY` if your installation requires one.

## Layout

```text
manage.py
config/            Django settings, logging, URL and server entry points
core/              App startup, native tracing, safe command error boundary
research/          Agent, prompts, limits, service.py, Firecrawl tools
  management/commands/research.py
subscriptions/     Authentication, credentials, service.py, model construction
  management/commands/login.py
  management/commands/models.py
tests/
  research/        Public agent/tool behavior
  core/            Native tracing payloads and privacy
  test_commands.py Django command checks
docs/              Specifications, design notes, and research
```

The research agent uses Pydantic AI's high-level `run()`. The model plans one to three complementary queries per search batch; Python bounds and executes them, deduplicates sources, and reports partial failures. This is an instruction and schema constraint, not a guarantee of research quality. Tool scheduling uses Pydantic AI and provider defaults; independent tool calls may run concurrently.

## Configuration and boundaries

Runtime addresses and credentials live in `config/settings.py`. Policy limits live in the relevant app's `constants.py`: eight model requests, ten tool calls, a 180-second run deadline, 45-second HTTP timeouts, and one validation retry. Each search batch contains at most three queries with five results each. Page reads return at most 16,000 characters and expose `next_offset`; downloads are bounded to 2 MB.

Django configures progress logs on stderr. Answers and model listings go to stdout. Logs omit raw queries, URLs, page contents, and provider exception messages. Use public topics: model instructions exclude personal details from searches, but enforced private-case redaction is not implemented. URL checks reject obvious private targets; DNS and redirect protection remain Firecrawl's responsibility. Citations and prompt-injection resistance still need human assessment.

## Langfuse

Configure both `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` from your project at `http://127.0.0.1:3000`. Override the address with `LANGFUSE_BASE_URL`. Without both keys, tracing is disabled and research still works.

`CoreConfig.ready()` configures Pydantic AI instrumentation globally using `Agent.instrument_all(InstrumentationSettings(...))`. Automatic instrumentation creates run, model, and tool spans with timing, usage, parent/child relationships, and error types. By default, prompts, completions, tool arguments/results, and exception messages are excluded. Set `LANGFUSE_CAPTURE_CONTENT=true` in `.env` to record text content for local debugging: prompts, answers, tool inputs/results, and exception details may then be stored in Langfuse. Binary content and model request parameters remain excluded. Tests inspect the SDK's actual serialized OTLP payload with content capture both enabled and disabled.

A standard OpenTelemetry OTLP/HTTP protobuf exporter sends the spans to Langfuse. Framework instrumentation owns agent/model/tool spans. Explicit `firecrawl.search` and `firecrawl.scrape` retrieval spans record each service request, including requests made inside delegated tools; content follows `LANGFUSE_CAPTURE_CONTENT`. The command boundary flushes pending spans on completion. Live ingestion was verified on 18 November 2026 using a real subscription-backed research run and read-back through the Langfuse observations endpoint: one agent, three model generations with token usage, and two tool observations with correct parent relationships. With content capture disabled, message structure and tool schemas remain visible, while prompt/answer text and tool arguments/results are excluded. After changing the flag, run a new management command; existing traces cannot recover omitted content.

Prompts remain version-controlled in `research/prompts.py`; Langfuse prompt management is not enabled. Reference: [Pydantic AI–Langfuse integration](https://langfuse.com/integrations/frameworks/pydantic-ai). We use the global configuration supported by the installed Pydantic AI version rather than the older per-agent `instrument=True` constructor example.

## Checks

```sh
uv run python manage.py check
uv run mypy
uv run pytest -q
```

Controlled tests exercise real application code with substituted model/HTTP boundaries. They don't consume subscription usage or establish model research quality. Live subscription plus local Firecrawl research has also been exercised separately.

## Parked query-planner experiment (disabled by default)

`uv run python manage.py plan_queries "Find official Python asyncio.timeout documentation" --mode simple`
returns a validated JSON plan without searching or scraping. It uses GPT-6.2 Sol by default
and creates a `simple_query_planner` or `complex_query_planner` trace with the same Langfuse configuration.

The planner has separate instructions in `research/planning/prompts.py` and a bounded
`SearchPlan` output. Simple mode requires one query; `--mode complex` requires two or three complementary queries. Query privacy instructions are advisory,
not an enforced redaction layer. Normal `research` runs expose only `search` and `read_page`: the main
agent generates queries directly. Planner code and tests are preserved for later use.

Only `research --experimental-query-planners` exposes `search_simple`, `search_complex`, and `read_page`
to the main agent. The main agent chooses a planning tool and passes a research question.
That tool invokes its planner and executes the validated queries directly with Firecrawl,
returning the queries and sources together. The main agent then selects pages and answers.

Planner requests count toward the overall model-request budget. Each planner also has a
local request allowance and timeout. Both use GPT-6.2 Sol by default; configure
`SIMPLE_QUERY_MODEL` and `COMPLEX_QUERY_MODEL` in `.env` independently. The research command's
`--model` selects the main model only. One subscription client/lock is shared for the run.
Different provider integrations and a dedicated routing model are not implemented.

Native tracing nests the planner run under its selected search tool. Inspect that child
run for the planner prompt/output, then the parent tool output for actual search results.

## Retrieval behavior

Missing search-result fields are reported as unavailable, rather than confirmed empty results.
Each Firecrawl HTTP operation is visible as a nested `firecrawl.search` or `firecrawl.scrape`
observation. Content follows the tracing opt-in.

Page reads recognize explicit Markdown definition/heading permalinks and select the requested
anchor section before sending evidence to the model. Unrecognized anchors retain page pagination;
`section_found` and `offset_scope` identify the selection. Unanchored pages can still contain noise.
