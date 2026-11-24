# Follow-up efficiency repair — 24 November 2026

Baseline: `dfd95f7` (fresh main). Controlled tests use temporary PostgreSQL,
controlled models and synthetic Google responses. Live acceptance uses the owner's
Telegram bot, Gmail correspondence and unsent drafts; no email was sent.

## Reproduced failures and repairs

- Rechecking unchanged correspondence classified the same text again. Analysis now
  depends on a fingerprint of the supplied messages, truncation and classifier
  instructions. Gmail is still fetched; changed evidence invalidates the cache.
- Consecutive draft revisions asked which version despite an active case. The
  latest created version is the default; explicit versions remain supported.
- Review reminders incorrectly required sent mail. Draft review is now a distinct
  persisted purpose and delivers without a model or Gmail read at the deadline.
- Previously received replies could satisfy a newly requested future no-response
  reminder. Only evidence outside its saved baseline can satisfy it.
- Moving a reminder could lose its purpose/version. Both survive rescheduling,
  including moving v1 after v2 exists. Cancellation invalidates queued delivery.
- Ordinary contextual replies went through two models. The intake may answer from
  a bounded dialogue projection. Original tool messages remain stored; unavailable
  details, long context and memory operations retain the full handler.
- Explicit preference changes now route directly to the existing consent validator,
  eliminating the intermediate tool-selection model. Forgetting protections remain.
- Independent public research was blocked with a selected case. It now proceeds
  without exporting case facts or changing the case selection.
- Gmail search excludes drafts and deduplicates listed thread IDs before metadata reads.

## Live trace evidence

Project: `cmwlnbvg00007ro3rd5obwpcu` on local Langfuse. Open
`http://localhost:3000/project/cmwlnbvg00007ro3rd5obwpcu/traces/TRACE_ID`.
Timings are individual observations, not benchmark distributions.

| Scenario | Trace | Model calls | Duration |
| --- | --- | ---: | ---: |
| Greeting/capabilities (preceding repair) | cd633f87eff8567279cf7172a59e8c89 | 0 | 0.099s |
| Context recall after restart | 513072d59804cf633476727d191ad0a1 | 1 | 2.857s |
| Recheck unchanged correspondence | 2aa6c084c2921a35e6191250539e6ccd | 1 | 4.735s |
| Preference save | bef22ab138044f5733a7479689929cc0 | 2 | 7.219s |
| Preference correction | 38b024a05b830bed5aee8d2ac4760053 | 2 | 7.518s |
| Saved preference read | 451e003ac3cd999bebb6c86174e4a387 | 0 | 0.063s |
| Scheduled notification after restart | e8d987bf937f62adc64b1b0e5795a3cc | 0 | 0.044s |
| Consecutive draft revision to v4 | b40960d6a85e68ee325557f90dbee06e | 2 | 17.492s |

Cancellation: `5944b74ddf5969fdd751de5ac25e3a20`. Public research with a
selected case: `489d478bbb13746874268193b995d456`. The exact v4 deep link
was opened in Gmail and its concise body/booking-record request verified.

The inspected application spans have both input and output captured in this local
opt-in configuration. Scheduled delivery now also supplies a Telegram trace link.
The scheduler polls; delivery is not an exact-second alarm, and requires the local
runtime to be awake. A model call for routing and a separate consent or drafting
operation are intentional; this repair removes repeated work, not all model calls.

## Review and validation

- Standards axis: no actionable findings.
- Spec axis: reminder purpose/version preservation finding fixed and re-reviewed.
- Existing Langfuse `gmail-reminders-v1` real-model dataset: passed, experiment
  `http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/experiments/results?baseline=31955b6590192490`.
- Full regression suite: 372 passed. Strict mypy: 246 source files, no issues. Migration drift check: none.
- Existing real-model Gmail reply dataset: passed, experiment
  `http://127.0.0.1:3000/project/cmwlnbvg00007ro3rd5obwpcu/experiments/results?baseline=5ddb9ca1b7486f82`.
