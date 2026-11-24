# Consolidated Gmail review — 24 November 2026

Requested order: merge pending work, pull main, review, fix, then explain scheduling and prior annotations.

Merged PRs: #42, #50, #51, #52, #53. Reviewed merged main `b2b7349` against pre-merge main `6a87c8f7e4343de1cea3cdc149d5dbe5e373f3b6` using `git diff 6a87c8f...b2b7349`. Comparing merged main to itself would omit all changes. Standards and specification reviews ran independently under the code-review skill.

## Standards

1. Hard rule: scattered policy limits in monitoring, mailbox headers and draft output. Fixed with named constants.
2. Judgement: retrieval caveats inferred from answer prose. Fixed by using typed truncation and attachment flags.
3. Judgement: shutdown rules duplicated across case closure, disconnect and monitoring. Fixed with transaction-compatible `MonitoringLifecycle.stop()`.

Independent re-review: all three addressed; no remaining actionable standards findings in the fix diff.

## Spec

1. A reminder queued before a crash could be delivered after restart despite a new substantive reply. Fixed by fresh evidence checking before reminder handoff and reassessment of queued state. Failed checks retain state and retry backoff.
2. A stop before any mailbox/monitor existed was forgotten. Fixed by persisting `GrievanceCase.monitoring_paused`; ordinary case help respects it and an explicit start clears it.
3. A later substantive reply did not satisfy a reminder waiting for clarification. Fixed by reassessing clarification state and cancelling pending obsolete notices while leaving the case open.

Independent re-review: all three addressed; no additional actionable regressions found in the fix diff. Regression scenarios exercise the public turn/worker boundary, temporary database, crash-before-handoff, new replies during downtime, failed rechecks/backoff, early stop, and later clarification resolution.

This review does not certify hosted multi-user readiness. The application remains a local single-owner app. Review of ownership/cardinality and a separately isolated fresh-user demonstration remain distinct activities.

## Scheduling decision status

Hermes source research is in `docs/research/hermes-scheduling.md`. The current runtime still checks schedules within the Telegram polling process. Moving this to an independent single-worker scheduler is a recommendation, not implemented by this repair. Preserve PostgreSQL as the source of truth, case authorization, conservative ambiguous-delivery handling and explicit sleep/offline limitations.
