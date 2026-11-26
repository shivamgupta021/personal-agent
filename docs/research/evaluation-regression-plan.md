# Evaluation regression coverage

This supplements the existing grievance acceptance, Gmail, reminder, research and conversation suites. It does not replace live Telegram checks.

## Reading applied

[Anthropic: Demystifying evals for AI agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents) distinguishes a successful-looking transcript from an actual successful outcome. We apply that distinction by reading stored state independently of the model's answer, retaining the transcript, repeating model trials, and keeping operational measurements separate from correctness. A passing synthetic experiment cannot establish that live Google consent, Telegram delivery or incoming-mail detection works. Those need integration evidence.

[Langfuse experiments](https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk) support local version-controlled cases through the native experiment runner. These runs appear under Experiments; code-produced scores do not imply a hosted online evaluator has been configured.

## Approved dimensions

1. Correct stored outcome: facts, preferences, drafts and scheduled jobs.
2. Authorization and isolation: approvals, exact recipients, case/owner boundaries.
3. Multi-turn completion: corrections, contextual follow-ups, complete mixed requests.
4. Efficiency: model calls, tokens and elapsed time, separately from correctness.
5. Trace completeness: root inputs/outputs and linked background work.
6. Response quality: factual, concise, appropriate tone. Automated structural checks are not a semantic quality certificate.

## New repeatable model scenarios

`uv run python manage.py evaluate_journeys --trials 3`

This executes the real model through ApplicationService, creates a fresh service object for each turn, uses independent synthetic owners, checks PostgreSQL state and removes only those synthetic owners afterwards. It requests neither Gmail nor web access. A research boundary records accidental delegation instead of making network searches.

| Scenario | Required outcome |
|---|---|
| Conversation recall with a selected case | Recall Python and Ruby; keep case facts and preferences unchanged |
| Preference correction | Save, change and read the latest preference |
| Temporary exception | A one-answer style request must not replace the lasting preference |
| Mixed inspection | Return both the case date and saved preference |
| Missing fact | State that no delivery date is recorded |
| Case correction | Persist the corrected date, then return it with the preference |
| Quoted instruction | Explain quoted text without executing its preference mutation |
| Unknown case | Do not disclose the selected case's date as another case's fact |
| Receipt follow-up | Read the attached receipt and cite it, even when the structured amount field is empty |
| Incoming reply check | Delegate a live mailbox question to the Gmail-capable specialist, not static fact inspection |

Each scenario has independent outcome scores, call-budget measurements, total duration and a transcript with per-turn trace IDs. The command exits unsuccessfully if any trial is missing or an outcome check fails. Call-budget failures remain separate and must be reviewed before release. Report the number of successful trials per scenario; three successes are evidence, not a guarantee of future reliability.

## Existing safety regression coverage

The complete pytest suite checks exact Gmail send envelopes, changed-draft rejection, bounded follow-ups, restart behavior, offer pauses, approval replay, closure, reminder cancellation and once-only delivery, incoming-message tracking, attachment extraction, deletion and case isolation. Controlled responses establish execution behavior, not real-model interpretation quality.

`uv run pytest -q`

`uv run mypy .`

Acceptance evidence fingerprints now include Gmail and attachments. Changes there invalidate old evidence instead of allowing an unrelated earlier test run to certify them.

## Human review and live release check

Review Telegram trace links for natural messages, not internal commands. Independently verify the draft in Gmail, the exact recipient of fictional sends, notification after a new incoming test reply, follow-up stopping conditions, reminder delivery and cancellation. Keep old traces for comparison while resetting application state. Do not label an unexecuted integration check as passed.

For each final reply/draft, review: does it answer every requested part; does it contain unsupported claims; is it concise; does firmness reflect the history without threats or invented deadlines? Keep this review pending until a human or a calibrated judge evaluates the actual output. A short answer can still be wrong.

## Automated gate

`.github/workflows/regression.yml` runs the controlled suite and typechecking for pull requests and main pushes, using an ephemeral PostgreSQL service and installed OCR tools. It needs no Gmail, Telegram, Langfuse or model credentials. Live subscription experiments remain an explicit local release check; hosted runners cannot reach this Mac's Langfuse or borrow its authenticated subscription. Branch protection is a separate repository setting, not implied by adding the workflow.

Targeted investigation after a failed trial is available with repeated `--scenario` options, for example `--scenario preference-correction --scenario case-correction`. Retain the original failed run and label the rerun; never overwrite a failure as though it passed originally.
