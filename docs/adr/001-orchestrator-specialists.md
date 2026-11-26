# Route requests without interpreting specialist tasks

The main orchestrator classifies a request and delegates it. Its model output contains
the specialist name and, for a mixed case/general-conversation question, an optional
verbatim general-conversation question. It does not extract case facts or preferences, choose draft
versions, set schedules, or generate conversational answers.

`core/service.py` owns delegation and returns the specialist's completed reply.
`core/runtime.py` owns subscription lifetime and Gmail consent/resumption.
`core/lifecycle.py` owns delivery authorization, tracing and fixed control actions.

Specialists own their context, instructions and execution:

- `grievances/service.py`: case interpretation and case workflows, including Gmail
  correspondence/drafting and policy research through their existing modules.
- `preferences/runner.py`: preference interpretation, source validation and storage.
- `conversations/memory.py`: conversation, recall and general memory operations.
- `research/service.py`: public research loop.
- `attachments/reader.py`: attachment extraction and associated evidence.

Case interpretation gets selected-case context once. The main router gets the current
request, whether a case is selected, and a bounded text projection of recent general
conversation to resolve follow-ups. It does not load case records or raw tool results.
Privacy and ownership checks remain executable code, not routing-model permissions.

Known help/inspection messages, exact deletion controls, uploads and authenticated
buttons need no model routing. Button approvals remain bound to the displayed plan.
There is no automatic final model call to paraphrase a complete specialist answer.
For a mixed question, the application checks that the secondary question was copied
from the user's message and passes the case privacy boundary before delegating it.
It combines the two specialist replies without an extra generation. This prevents a
case-only reply from silently omitting an independent conversation question.

An open-ended preference save now requires a small routing call and a preference
specialist call. This deliberately separates responsibilities; it does not promise
lower latency than extracting preferences in the main call. Measure model latency and
tokens in traces. Each specialist can later use a different model without changing
routing or persistence contracts.

Verification uses public application-turn tests for delegation, specialist tests for
case operations, and real Telegram traces. Controlled model responses verify execution
contracts; they do not establish routing quality for every possible user message.
