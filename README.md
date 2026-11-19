# Personal Agent

A local Django project with a real research agent using your ChatGPT subscription, Pydantic AI, and your existing Firecrawl service. Django supplies application startup, settings, logging, and management commands. Owner-only Telegram, persistent conversation memory, and grievance intake use the same application services. External sending is not available.

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

Only the configured owner in their private chat is accepted. Conversations and explicit
preferences persist as described below. Questions run serially, using the existing
subscription lock. Keep one bot process running; Ctrl-C stops it.

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
ingestion; the owner also confirmed receiving the answer and source link in Telegram.

### Persistent Telegram conversations (milestone #23)

Configure the dedicated application PostgreSQL database using the `POSTGRES_*` values
in `.env` (loaded automatically). Apply migrations, then run the bot:

```sh
uv run python manage.py migrate
uv run python manage.py telegram
```

Completed turns now survive process restarts. Send a public research question followed
by a question referring to its answer. Restart the bot and ask another follow-up.
The bot still processes one owner message at a time; interruption requires manual
resend. A completed answer is saved before delivery, so a failed Telegram send does
not erase it. Interrupted model runs are not appended as completed turns.

This milestone has no compaction or archival retrieval yet. A conservative 48,000-byte
serialized request budget includes message history, tool definitions, instructions,
and 8,000 units of framing/output headroom; output is capped at 4,000 tokens. Bytes
are a pessimistic proxy rather than an exact provider tokenizer. The guard runs before
every model call, including after tool results. When exceeded, it reports the limit
and leaves saved history intact. Automatic compaction follows in #24.

Ask **“Delete this entire conversation.”** to remove local conversation history.
This is a deletion control, not the normal mechanism for managing context length.
Associated Langfuse traces are submitted for asynchronous deletion after local trace
export has been flushed. The reply distinguishes local removal from remote cleanup.
Say **“retry trace cleanup”** to retry without invoking the model or re-exporting old
content. Content-free trace IDs remain available for repeated cleanup because a 404
before delayed ingestion is not proof of final deletion. This does not erase Telegram,
provider, or independently made backup copies. Targeted forgetting arrives in #26.

Run repeatable real-model conversation experiments after prompt/model changes:

```sh
uv run python manage.py evaluate_conversation
```

The command publishes synthetic follow-up, correction, and ambiguity cases to a
versioned Langfuse dataset, creates a new experiment, prints answers and its URL,
and exits unsuccessfully when coarse checks fail. These are SDK evaluators, not
server-side scheduled evaluators. Inspect outputs manually for semantic quality;
substring checks are not a guarantee of correct recall. Synthetic evaluation
conversations remain in the application database for inspection and are separate
from the owner's Telegram conversation.

Database tests use Django's isolated `test_personal_agent` database; the development
role needs permission to create a test database. No test should point at the live
application database as its test database.

Validation checkpoint for #23: 54 deterministic tests and strict typechecking passed.
A real-model Langfuse experiment verified the three coarse conversation checks;
manual review confirmed language recall, corrected-budget precedence, and clarification
of ambiguous orders. A separate synthetic real-model deletion check returned “I don't
recall your code word” after deletion. Trace deletion is asynchronous and must be
checked separately; an accepted request alone is not proof of remote erasure.
The exact phrase “Delete this entire conversation” also works without a model call,
including when saved context is already over budget or model access is unavailable.

Live acceptance completed on 18 November 2026: test messages were sent from the
owner's Telegram Web session. The bot recalled Python and Ruby, then recalled the
same pair after its process was stopped and restarted without restating the names.
Post-restart trace: `7640a760d7b4d3c90ba7784c4159b137`.
The separate synthetic deletion trace `be7eadee671e41d3708304b83be8214f`
subsequently returned zero observations from Langfuse's observations API, confirming
that the previously pending asynchronous cleanup had completed at verification time.

### Automatic context compaction (milestone #24)

After applying migrations, Telegram automatically summarizes older context when the
assembled request reaches 32,000 bytes. It retains recent complete exchanges using the
matching Pydantic AI harness, then checks the 48,000-byte hard limit again. There is no
summary call on ordinary short turns. The earlier #23 “no compaction” limitation above
is superseded by this milestone.

Original messages remain in the local archive. Active context, originals, and summary
source references commit together only after a successful turn. A failed summary keeps
the previous context; the request continues only if that context fits the hard limit.
Whole-conversation deletion includes summaries. Retrieval from the archive is not yet
available in replies.

Inspect `compact_messages` → `summarizing_compaction run` → model generation in
Langfuse. The generation contains the summary prompt/output and usage; the surrounding
framework span provides compaction metadata. Include those generations when comparing
latency and token totals.

```sh
uv run python manage.py evaluate_conversation --compaction
uv run python manage.py evaluate_conversation --compaction --full-history
```

These use the same hosted synthetic dataset and distinct experiment variants. The
full-history baseline still enforces the hard request budget. A baseline that exceeds
that budget is a failed comparison, not evidence of speed or token savings.

### Recall older conversation details

In Telegram, ask about an earlier detail. When current context is insufficient,
`recall_conversation` searches the saved original user/assistant text for all supplied
keywords. It returns up to five excerpts of 500 characters, newest first, with message
IDs, roles, timestamps and truncation indicators. At most three retrieval calls are
allowed per turn; the normal assembled-context guard still applies.

Recall is local and scoped to the current conversation. It does not add a vector
service or a retrieval cache. The archive is loaded on demand by the application,
not sent wholesale to the model; searching very large archives remains a future
scalability concern. Keyword matching can miss synonyms, and model interpretation
of corrections/ambiguity still requires evaluation. Whole-conversation deletion
removes the source archive and associated traces using the existing cleanup flow.

Run real-model recall/correction, ambiguity and missing-evidence experiments:

```bash
uv run python manage.py evaluate_conversation --recall
```

Open the printed Langfuse experiment URL, then an item's trace. Inspect
`recall_conversation` input/output and the subsequent model answer; verify cited
message IDs and that newer corrections take precedence. Scores are coarse text
checks, not semantic proof.

### Correct or forget selected conversation facts

Use ordinary messages such as “Correction: my parcel code is ABC-2” or “Forget my
parcel code, but keep my comparison language.” The agent verifies current intent,
proposes exact-quote edits, checks that unrelated prose is preserved, and commits
atomically. A successful edit ends the old run so stale context cannot be saved back.

This invalidates derived summaries, redacts matching text and tool data, and rebuilds
bounded recent context from sanitized originals. Unrelated tool evidence and prose
are retained; other originals remain available through recall. It
conservatively requests deletion of prior traces for that conversation because they
may repeat its context. Local success and pending trace cleanup are reported separately.
Say “retry trace cleanup” to retry asynchronous or failed Langfuse deletion. External
Telegram/provider copies and independent backups are outside this guarantee.

Ambiguous intent, unverifiable edits, and archives beyond the bounded edit budget
leave saved content unchanged. Semantic selection remains model-dependent; review
important changes. Run the synthetic real-model checks with:

```bash
uv run python manage.py evaluate_conversation --facts
```

Those experiments use separate trace roots for each application turn, so an open
experiment parent cannot recreate a deleted trace containing an earlier synthetic fact.

### Explicit saved preferences

Manage preferences in ordinary conversation: “Remember that I prefer concise
replies”, “What preferences have you saved?”, “Change my usual reply style to
detailed”, or “Forget my reply-style preference.” A temporary instruction such as
“For this answer only, be detailed” stays in conversation context and does not
change the saved preference. Ambiguous intent should produce a clarification.

Preferences persist separately from conversation history. Deleting the whole
conversation does not delete independently saved preferences. Forgetting a
preference removes its saved record and sanitizes relevant conversation copies
using the same bounded cleanup and trace-deletion path described above. A saved
change remains saved if Telegram cannot deliver the confirmation; inspect current
preferences after an interrupted or uncertain reply.

Rerun the real-model consent, exception, approval and lifecycle experiment after
changing preference prompts or model settings:

```bash
uv run python manage.py evaluate_conversation --preferences
```

Its scores check stored state and local redaction. Review the actual replies and
clarification behavior in the linked Langfuse experiment as well.

## Grievance intake

Apply migrations and start the normal Telegram command. Use explicit case labels:

- “Create a refund case called Kettle return. Company: Example Home. My kettle arrived broken. I want a refund.”
- Supply the purchase date, amount paid, requested refund and evidence description when asked. Missing values stay unknown.
- “List my cases”, “Select the Kettle return case”, and “Show the facts for my selected case” inspect saved state.
- “Correction: the amount paid was INR 2,700. Update the amount paid only.” changes that fact while preserving earlier revisions. A conflicting amount without a correction prompts clarification.
- “Return to general public research mode” leaves the selected case.

Cases, messages, fact revisions and selection survive restart. Duplicate Telegram delivery
IDs cannot repeat saved case actions. Reusing a case label does not silently create another
case or overwrite its facts. This intake milestone does not send complaints or resolve cases.

Private case agent spans omit content even when public research content capture is enabled.
Use the native synthetic intake experiment to inspect extracted proposals and saved facts:

```sh
uv run python manage.py evaluate_grievances
```

It runs the real subscription model against fictional intake, missing-fact, correction,
case-switching and ambiguity scenarios and publishes the `grievance-intake-v1` dataset
experiment. Inspect outputs and individual checks, not only the aggregate score. Public
research is a controlled boundary in this suite; live Firecrawl is tested separately.

### Factual complaint drafts

After selecting a case, ask “Draft a factual refund email.” The result is saved as an
immutable version with its source fact revision. It is not sent. Missing required facts
produce an explicitly incomplete draft with named gaps; the requested refund amount is
never assumed to equal the amount paid.

Use “List my saved draft versions”, “Read draft version 1”, or “Read my current draft”.
After an explicit material correction, old versions remain readable but are marked
outdated. Ask for a new draft to use the corrected facts. A result arriving after a fact
or selection change is rejected rather than silently saved against newer state.

The model selects a typed writing plan; application code renders the saved facts and
requested remedy. It cannot add unsupported factual prose or policy rights. Evidence
is quoted as your account, preserving uncertainty without claiming attachments. Semantic
consistency checks still depend on the model and are evaluated with finite examples.

```sh
uv run python manage.py evaluate_grievances --drafts
```

This publishes a fresh `grievance-factual-drafts-v1` experiment. Inspect entire letters,
version/current/outdated fields and individual checks. Scenarios cover corrections,
incomplete facts, uncertain evidence, case isolation, requested deadlines/component
charges and money-back wording. No web research or external sending is used.

### Company policy research

Policy research uses a separate reviewed public brief. It never sends the case transcript,
order reference, name, address or correspondence to Firecrawl. Example turns after selecting
an appropriate case (replace the example company/site with the real official site):

1. “Review public research fields: company Example Home; product electrical appliance; issue defective item; jurisdiction India; official site https://www.example.com/.”
2. Inspect the displayed values, then say “I confirm these public research fields and this official site.”
3. “Research official policies for this case.”
4. “Draft a refund email using the research.”

Research builds three fixed-purpose queries from those public fields. It reads the confirmed
site root and bounded observed links on that exact HTTPS origin. Search rank or a similar
hostname cannot establish official status. A redirected origin needs a new reviewed anchor.
Identity evidence and provenance are recorded as owner-confirmed, not independently certified.

A researched draft preserves exact source quotations, URL/retrieval time and uncertainty in
review notes. Explicit ISO effective dates can identify a purchase-time candidate; this does
not establish eligibility. Undated, conflicting or missing terms remain uncertain. Research
failure preserves factual drafting. Changing the reviewed company/site/jurisdiction invalidates
older researched drafts; newly generated factual drafts omit superseded authority until the
new brief is confirmed and researched. Prior versions are retained unchanged.

The installed local Firecrawl build needed a destination-provenance fix. Setup, pinned image
checks and rollback are documented in [infra/firecrawl/README.md](infra/firecrawl/README.md).
Unpatched or unknown engines lack verified destination metadata and fail closed for policy
verification; ordinary public `research` still works. Existing network protections remain
Firecrawl's responsibility. Private case tracing stays metadata-only regardless of public
content capture.

```sh
uv run python manage.py evaluate_grievances --policy
uv run python manage.py evaluate_grievances --policy --scenario official_historical
```

These native Langfuse experiments use real model calls with fixed fictional HTTP pages and
no live-web fallback. They cover dated versions, impostors, conflicts, missing historical terms,
outages, injected instructions, actual outbound privacy and changes to reviewed authority.
The optional scenario filter runs a focused subset on the same dataset. Compare prompt,
fixture and evaluator hashes; manually read the quotations and final letter. Real Firecrawl
connectivity/provenance is verified separately from these model-quality experiments.

### Manual sending, replies and follow-ups

The assistant prepares drafts; you send them yourself. Report the exact version so the record
cannot silently attach to a different draft:

```text
I sent draft version 1 yesterday by email. Record that version as sent.
Company reply: "We can offer a replacement, not a refund. Please send a photo."
Please save this reply and draft a follow-up asking for my refund.
```

Saved replies retain the supplied text. Follow-up drafts reference their source replies and use
current case facts. Earlier versions remain unchanged, including an outdated version you
actually sent. Ambiguous send details stay unknown; a company request for evidence does not
mean that evidence was attached or sent. If reply storage succeeds but generation fails, the
reply remains saved and the response reports the incomplete phase.

A company offer leaves the case open. Use an explicit outcome such as `I consider this case
resolved` or `Close this case without resolution`. Neither action sends anything externally.

Rerun the real-model examples through native Langfuse datasets/experiments:

```sh
uv run python manage.py evaluate_grievances --progress
uv run python manage.py evaluate_grievances --progress --scenario replacement_followup
```

Review the saved letter and company attribution alongside the coarse state/version scores.
Controlled database tests separately cover failed generation, duplicate delivery and stale
results during corrections; a passing model example does not replace those checks.
