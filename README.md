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

## Research evaluations

Run the real research loop against fixed, fictional web fixtures:

```sh
uv run python manage.py evaluate_research
uv run python manage.py evaluate_research --langfuse
uv run python manage.py evaluate_research --live-model --output .local/evaluations/model.json
```

The first command replays controlled model responses to check orchestration and the evaluator.
The `--langfuse` command publishes a replay experiment without subscription usage.
The `--live-model` command runs a Langfuse experiment using the subscription model (default GPT-6.2 Sol), while
web responses remain fixed. Neither command measures live Firecrawl reliability. Normal
`research` remains the separate end-to-end smoke check. Query planners stay disabled.

The four scenarios cover anchored evidence, recovery after empty search, unavailable search,
and continuation through a noisy unanchored page. `research/evaluation/cases.json` holds
questions, web fixtures, replay steps and expectations; `--suite PATH` selects another suite.
Search fixtures return responses in request order, regardless of query text: query relevance
must be reviewed manually. Unexpected page URLs fail rather than accessing the network.

Reports go to stdout and `--output` (default `.local/evaluations/latest.json`). Failed checks
return exit status 1 after writing the report. Review `evidence_checks` separately from
`answer_checks`, then inspect the answer, queries, search results and page excerpts. Token
counts and elapsed time are recorded; replay timings/tokens are not model benchmarks.
Suite and prompt hashes identify the evaluated inputs.

Automatic checks use literal phrases, citation URLs and retrieval budgets. A pass does not
establish factual entailment or model quality; a failure may be a valid paraphrase requiring
human review. Check whether each claim is supported, queries are relevant, uncertainty is
honest, and the answer is useful. Unanchored-page warnings deliberately expose irrelevant
opening excerpts even when later evidence allows the answer to pass.

Langfuse uses the normal tracing settings, including content opt-in. Evaluation retrieval
spans represent fixtures, not actual Firecrawl requests; the fictional questions distinguish
these runs from real research. The Langfuse SDK 5.2.0 owns experiment execution, dataset linkage, and score publication.
Both experiment modes require the existing Langfuse keys in `.env`; ordinary replay stays offline.
The command prints `experiment_url` and `dataset_name` in its JSON report. Open that URL,
compare experiments, and inspect each item's output, scores, and linked trace.

The hosted dataset uses the question as input, the expected checks as expected output,
and fixed web fixtures as metadata. Its name includes a suite-content hash; edits create
another dataset rather than silently changing comparisons. Repeated runs reuse stable item IDs.
These four fictional scenarios are a reviewable development baseline, not broad coverage.

Three SDK code scores are attached to each complete research execution:
`evidence_checks_pass`, `answer_checks_pass`, and `research_checks_pass` (including execution
failures). A value of 1 means those deterministic checks passed, not that semantic quality
was established. Failed-check names appear in score comments. These evaluator functions
run locally through the SDK; they are not server-side automatic evaluators for production traffic.
Stage-specific query, retrieval, selection, and synthesis scores are deferred.
The SDK serializes evaluation inputs and outputs to Langfuse even if normal tracing content
capture is off; use this command only with the approved synthetic fixture suite.

## Owner-only Telegram research

Create a bot through Telegram's official [@BotFather](https://t.me/BotFather), then put
`TELEGRAM_BOT_TOKEN` and your numeric `TELEGRAM_OWNER_ID` in the root `.env`.
Never put the token in a command argument, commit it, or paste it into chat.

```sh
uv run python manage.py telegram
```

Open your bot's private chat and send `/start`, then a public research question such as:
“Find the official Python documentation for asyncio.timeout and explain deadline expiry.”
The existing research service returns its answer and source links as plain text. Long replies
are split. Review the normal `research run` and its model/search/read spans in local Langfuse.
`LANGFUSE_CAPTURE_CONTENT=true` includes the question and answer, just as in the terminal flow.

Only the configured owner in their private chat is accepted. Each message is independent:
there is no saved conversation or preference memory. Questions run serially, using the
existing subscription lock. Keep one bot process running; Ctrl-C stops it.

A private cursor file under `.local/telegram/` is saved **before** work starts. Interrupted
research or uncertain reply delivery is not retried automatically: resend the question.
This may lose an unanswered request but avoids automatically repeating it. A split answer
can be partially delivered. No exactly-once delivery guarantee is claimed. Telegram retains
pending updates for at most 24 hours; a cursor unused for a week is reset because Telegram
may restart with a random update ID. Do not delete state to retry a question; send a new message.

Token rotation for the same bot preserves its cursor. Webhooks and other pollers can conflict
with long polling; this command does not delete webhooks automatically. Invalid credentials
or polling conflicts stop the command; transient network failures wait and resume polling.

Transport regression tests use controlled HTTP/model boundaries. The Langfuse research
experiments remain the model-quality workflow; Telegram introduces no new research prompt.
The live round trip requires your bot token and owner ID and is separate from those tests.

Live transport verification on 18 November 2026: an owner message reached the real research
service and Telegram accepted the reply. Langfuse trace
`daa396856f5813404569ea7be406760a` recorded 15.669 seconds, three model generations,
one search (five sources, zero failed queries), and one 2,342-character page read.
Content capture was disabled for that run. This verifies delivery through the API and trace
ingestion; the owner's visual check of the answer/citation is separate.
