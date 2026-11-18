# Personal Agent — milestone 1

Historical lesson guide. For the current real research agent, see [README](../../README.md). Statements about upcoming work below describe the milestone 1 checkpoint.

A small, inspectable model/tool loop. This is a learning exercise, not the grievance assistant MVP yet.

The only tool reads the hard-coded **synthetic** order `demo-001`: headphones costing INR 1200. There is no email, browser automation, real order lookup, database, or refund action.

## Run the offline lesson

From this project directory, with Python 3.12+ and uv installed:

```sh
uv sync --locked
PYTHONPATH=src uv run python -m personal_agent demo
```

The command uses a clearly labeled scripted model stand-in. It makes no network requests and needs no credentials. It exercises the same loop and actual Python tool as live mode.

Read the output in order:

1. `request`: the selected model, developer instructions, message and tool schema. Transport flags such as streaming and `store=false` are fixed in the transport adapter.
2. `response`: a structured request to call `demo.get_demo_order`.
3. `tool_call`: the action Python is about to validate.
4. `tool_result`: the actual function result, associated with the model's call ID.
5. A second `request`: the original input, model output and tool result together.
6. `answer`: the model's final response, followed by the stop reason.

The loop is `Agent.run` in `src/personal_agent/loop.py`. Python decides what may execute, while the model requests a tool or produces an answer. All response output, including reasoning continuation items, is retained for the next request.

## Run with your ChatGPT subscription

```sh
PYTHONPATH=src uv run python -m personal_agent login
PYTHONPATH=src uv run python -m personal_agent models
PYTHONPATH=src uv run python -m personal_agent live --model YOUR_MODEL_SLUG
```

Complete **Continue with ChatGPT** in the browser, including permission to use your plan. Choose a model slug printed by `models`; available models are account-specific. Live mode sends the same fixed synthetic exercise. It consumes subscription allowance and never falls back to `OPENAI_API_KEY` or enables recharge. The command prints each step as it happens.

Credentials are specific to this application. We do not copy another tool's credentials. They live in the Git-ignored `.local/chatgpt` directory, with owner-only permissions and atomic replacement after refresh. The directory also stores a stable installation ID and registration ID. A process lock serializes commands using that session. `--state-dir` selects a different directory; keep any such directory outside source control.

Login waits at most five minutes on `127.0.0.1:1455`; Ctrl-C cancels. A conflicting process on that port must be stopped before retrying. No credential or authorization URL is printed. Provider error bodies are deliberately hidden because they can include secrets. You can manage or revoke this app's access in ChatGPT settings.

### Why Pydantic appears in the dependencies already

Milestone 1 uses only its documented OAuth/provider helper to handle subscription credentials and refresh. It **does not use Pydantic AI’s `Agent`, model wrappers, tool registration, or agent harness**. Our small `Agent` class is handwritten. The model/tool loop and tool validation are written here. Milestone 2 will replace that loop with Pydantic AI so we can compare responsibilities without first implementing an OAuth library.

The live transport uses the OpenAI SDK directly: streamed Responses requests, `store=false`, namespace-scoped function tools, and explicit conversation history. A stream must complete successfully before its result is usable. See the official [ChatGPT provider docs](https://pydantic.dev/docs/ai/models/openai-chatgpt/) and [plan-usage limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).

## Limits and failures

Defaults are four model requests, three attempted tool calls, and 30 seconds per model request. SDK retries are disabled in live mode. Unknown tools, wrong namespaces and invalid arguments return a structured tool error; they cannot access real data. Exhausted budgets, timeouts, provider errors, and incomplete or empty responses raise a short, sanitized error; the CLI displays the reason and exits with a nonzero code. The loop never labels these as a completed answer.

The visible transcript is intended for synthetic data only. Do not adapt this debug output to real grievance content without the metadata-only tracing controls planned for the next milestone. No Langfuse export is configured yet.

## Verify

```sh
uv run mypy
uv run pytest
```

Tests enter through the public loop or CLI. Controlled model responses test tool execution, feedback, validation and stopping behavior. A simulated HTTP stream exercises the actual SDK transport; this is not evidence of a successful real subscription request. Live sign-in and a real response are a separate smoke check.

Verified on 17 November 2026: subscription sign-in, account model discovery, and a real tool-call round trip with `gpt-6.1-sol` succeeded. Python returned the synthetic order and the model answered **1,200 INR**. No real case data or external actions were used.

## Review guide: two things to read first

1. Open `src/personal_agent/loop.py` and read **`Agent.run()`** top to bottom. Its numbered comments follow five steps: prepare messages, ask the model, return an answer if present, execute a requested tool, and append the result for the next iteration. It returns an answer string. Limits and failures raise ordinary exceptions with safe messages. There are no event or result classes to understand.
2. In the same file, read **`Agent.get_demo_order()`**. This is the one tool: it validates the request and returns an invented order. The schema below it tells the model how to request this method. Python still validates every request.

Run `demo` beside those two methods. Find the first request, the tool call, the Python result, and the second request. Explain why the second request contains information the first did not.

Everything else can wait:

- `models.py`: a small typing contract and `OfflineModel`, which scripts the model side for the offline exercise. The scripted answers and order values are synthetic; the loop and validation are real.
- `subscription.py`: `ResponsesModel` sends the same conversation to a real model. It retains raw stream output, avoiding SDK-only parsing fields on subsequent requests.
- `auth.py`: working subscription sign-in and protected credentials; unchanged by this simplification. No need to study OAuth to understand the loop.
- `__main__.py`: `LearningApp` chooses offline or live mode and starts `Agent.run()`. The CLI commands have not changed.
- `tests/`: behavior checks using controlled model responses, simulated HTTP streaming, and the CLI. They are not evidence of real-model reasoning quality.

Class-based code remains the convention, but this lesson keeps its core behavior in one `Agent` class. Constructor arguments supply the model and limits directly. Authentication and transport stay separate. See `CODING_STANDARDS.md` for the learning-first exception to broader architectural guidance.

We stop here to review the loop together before introducing Pydantic AI's harness or Langfuse.

## What is established so far

**Working:** a local CLI, a handwritten bounded agent loop, synthetic read-only tool execution, an offline demonstration, direct subscription sign-in, account model discovery, raw streamed Responses transport, protected credential persistence, and behavioral tests. A real synthetic tool-call round trip has succeeded.

**Agreed but not implemented:** the refund-first grievance workflow, Telegram, Django case persistence, local Firecrawl research, metadata-only Langfuse tracing, draft versions, manual sent/outcome updates, follow-up drafts, deletion of cases, and the approximately 20-case product evaluation set. The current automated tests are engineering checks, not that future evaluation set.
