# Personal Agent — milestone 1

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

1. `request`: the message and tool schema offered to the model.
2. `response`: a structured request to call `demo.get_demo_order`.
3. `tool_call`: the action Python is about to validate.
4. `tool_result`: the actual function result, associated with the model's call ID.
5. A second `request`: the original input, model output and tool result together.
6. `answer`: the model's final response, followed by the stop reason.

The loop is in `src/personal_agent/loop.py`. Start there: Python decides what may execute, while the model requests a tool or produces an answer. All response output, including reasoning continuation items, is retained for the next request.

## Run with your ChatGPT subscription

```sh
PYTHONPATH=src uv run python -m personal_agent login
PYTHONPATH=src uv run python -m personal_agent models
PYTHONPATH=src uv run python -m personal_agent live --model YOUR_MODEL_SLUG
```

Complete **Continue with ChatGPT** in the browser, including permission to use your plan. Choose a model slug printed by `models`; available models are account-specific. Live mode sends the same fixed synthetic exercise. It consumes subscription allowance and never falls back to `OPENAI_API_KEY` or enables recharge. The command prints its collected events when the run ends.

Credentials are specific to this application. We do not copy another tool's credentials. They live in the Git-ignored `.local/chatgpt` directory, with owner-only permissions and atomic replacement after refresh. The directory also stores a stable installation ID and registration ID. A process lock serializes commands using that session. `--state-dir` selects a different directory; keep any such directory outside source control.

Login waits at most five minutes on `127.0.0.1:1455`; Ctrl-C cancels. A conflicting process on that port must be stopped before retrying. No credential or authorization URL is printed. Provider error bodies are deliberately hidden because they can include secrets. You can manage or revoke this app's access in ChatGPT settings.

### Why Pydantic appears in the dependencies already

Milestone 1 uses only its documented OAuth/provider helper to handle subscription credentials and refresh. It **does not use `Agent`, model wrappers, tool registration, or an agent harness**. The model/tool loop and tool validation are written here. Milestone 2 will replace that loop with Pydantic AI so we can compare responsibilities without first implementing an OAuth library.

The live transport uses the OpenAI SDK directly: streamed Responses requests, `store=false`, namespace-scoped function tools, and explicit conversation history. A stream must complete successfully before its result is usable. See the official [ChatGPT provider docs](https://pydantic.dev/docs/ai/models/openai-chatgpt/) and [plan-usage limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations).

## Limits and failures

Defaults are four model requests, three attempted tool calls, and 30 seconds per model request. SDK retries are disabled in live mode. Unknown tools, wrong namespaces and invalid arguments return a structured tool error; they cannot access real data. Exhausted budgets, timeouts, provider errors, and incomplete or empty responses return a non-success stop reason and a nonzero exit code. The loop never labels these as a completed answer.

The visible transcript is intended for synthetic data only. Do not adapt this debug output to real grievance content without the metadata-only tracing controls planned for the next milestone. No Langfuse export is configured yet.

## Verify

```sh
uv run mypy
uv run pytest
```

Tests enter through the public loop or CLI. Controlled model responses test tool execution, feedback, validation and stopping behavior. A simulated HTTP stream exercises the actual SDK transport; this is not evidence of a successful real subscription request. Live sign-in and a real response are a separate smoke check.

## Next learning checkpoint

Run `demo`, then explain which step is the model's decision and which step Python executes. Inspect `run_turn`, `execute_tool`, and the second request. Once we review this together, milestone 2 can introduce the Pydantic AI harness and privacy-preserving Langfuse tracing.
