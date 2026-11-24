# Hermes scheduling: findings and local application recommendation

Researched 24 November 2026. Research input, not an accepted design or implementation milestone. The official project is [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent), whose repository identifies its homepage as hermes-agent.nousresearch.com. All Hermes source links below are pinned to [commit 084538cdb605f3aeacbbeb32903bceefed59a905](https://github.com/NousResearch/hermes-agent/commit/084538cdb605f3aeacbbeb32903bceefed59a905), committed 24 November 2026. Local comparison was read at personal-agent commit `375fb498f55d32ce9cbdb76b0c902e665cac2dd3`.

## Recommendation

**Adopt the separation of durable schedules, an independent ticking process, execution ownership, and delivery state. Keep our existing Django domain services and database.** Hermes's useful lesson is that scheduling must keep progressing independently of chat requests; adopting Hermes itself, its JSON store, its generic prompt jobs, or its entire recovery machinery is unnecessary for our case-monitoring workflow.

This is an engineering inference from the implementation comparison below. Start with one reviewable milestone: a dedicated, single-worker Django management command driving the existing monitoring service, with explicit startup/shutdown and a durable scheduler heartbeat. Keep concurrency at one until ownership leases and stale completion fencing are strengthened. A local service still cannot check Gmail while the Mac sleeps; reliable deadlines during sleep require an awake host, not a different cron syntax.

## Verified Hermes architecture

### Creating and storing work

Hermes offers CLI/chat management and an agent-facing scheduling tool. It accepts relative one-shots, recurring intervals, five-field cron expressions, and ISO timestamps. Jobs can include a prompt, attached skills, delivery destinations, model/provider overrides, a work directory, and an optional script. Script-only jobs can bypass the model. These are general autonomous tasks rather than domain-specific email checks. [User guide][guide], [job creation and parsing][jobs].

Schedules and mutable job state live in the active profile's `cron/jobs.json`, using locked read-modify-write operations, temporary-file replacement and directory fsync. Per-profile paths matter; `~/.hermes/cron/` is only the default profile example. Execution attempts have a separate SQLite `cron/executions.db` ledger; deliveries use `cron/deliveries.db`. Outputs are written under `cron/output/`. Thus “Hermes stores cron in a JSON file” is incomplete: current reliability also depends on durable attempt and delivery records. [Job store][jobs], [execution ledger][executions], [delivery queue][delivery].

### What wakes it, and what must stay alive

The default `InProcessCronScheduler` runs a background loop with a 60-second interval, independently of individual chat requests. Each pass dispatches with `sync=False`, records heartbeat/success/error information, and waits on a stop event. An overrun or suspend gap reanchors the loop rather than repeatedly executing zero-delay ticks. A supervisor can restart a crashed ticker thread. This supervises a thread inside a live process; it is not a machine wake mechanism. [Provider loop][provider], [ticker supervisor][supervisor].

The gateway owns agent execution. `run_job()` routes an agent job through `run_canonical_job()` unless already executing in its gateway-owned session. An external `hermes cron tick` is headless: without a live gateway it refuses agent jobs, leaves their due instant untouched, and does not spawn an unmanaged daemon. Script-only jobs can run from that external tick. The user guide offers installation as a persistent gateway service. [Execution routing][scheduler], [tick admission][tick], [user guide][guide].

The documented hosted alternative, Chronos, arms one remote one-shot at each job's next due instant and invokes an authenticated gateway webhook. That hosted arrangement can wake a scaled-to-zero deployment; it is not evidence that the local gateway wakes a sleeping Mac. The provider interface separates triggering from execution, and unavailable named providers fall back to the built-in ticker with a warning. **Scope:** the hosted wire contract was read in official documentation, not exercised or audited end to end. [Official runtime documentation][internals], [provider resolution][provider].

### Dispatch, concurrency and crash recovery

A per-profile tick lock prevents concurrent scan/dispatch sections. Recurring schedules advance before dispatch and carry a provisional `pending_slot`; a subsequent scan can restore an unclaimed slot when its owner is gone. Execution records carry occurrence identity (`scheduled_instant`), and completed-occurrence checks prevent repeating completed slots. Fire claims and heartbeats protect live work. These are explicit crash-window policies, not a blanket exactly-once guarantee for arbitrary external side effects. [Tick][tick], [due evaluation and claims][jobs], [occurrence recovery][occurrences].

Jobs enter a persistent thread pool keyed by profile; in-flight guards prevent overlapping submission of the same job. `HERMES_CRON_MAX_PARALLEL` or `cron.max_parallel_jobs` selects the limit; setting one requests serial execution. Although the resolver's comment calls an absent limit “unbounded,” the actual code passes `None` to `ThreadPoolExecutor`, so the Python executor chooses its default. Do not copy that comment as a literal unlimited-concurrency guarantee. [Pool and submission implementation][scheduler].

Each agent execution has its own session and receives the saved job prompt and attached context. The implementation constructs the agent with `skip_memory=False`, so fresh conversation history does **not** imply no persistent memory. It separately disables background review. [Agent construction][scheduler], [prompt preparation][prompt].

Execution attempts can finish completed, failed, cancelled, unknown, or missed. The ledger is explicitly an audit record, not a retry queue; abandoned work is reconciled using process ownership evidence. Admission journals support reconciliation when gateway acceptance is uncertain, and the job can be paused until a terminal receipt resolves the ambiguity. This avoids assuming a lost connection means nothing executed. [Ledger][executions], [gateway admission and reconciliation][authority].

### Missed deadlines and time zones

The current source gives a more precise policy than some prose documentation:

| Situation | Behavior |
| --- | --- |
| Recurring occurrence late within its grace | Run late. Default grace is half its period, clamped to 120 seconds–2 hours. |
| Recurring backlog beyond grace | By default collapse missed work into one catch-up run; record skipped/collapsed occurrences. No replay storm of every historical slot. |
| Explicit per-job `catch_up` | Overrides the inherited catch-up decision. |
| Explicit `grace_minutes`, without explicit `catch_up` | Past that limit, skip rather than use global catch-up. |
| One-shot more than 120 seconds late | Normally retire as missed; explicit `catch_up: true` allows a late run. Recorded dispatch holds/store outages also have special recovery allowances. |

Missed-slot counting is capped at 1,000 and records that the count is a lower bound when capped. These are source-level facts, not a promise that every missed task is useful to execute later. [Missed-run policy][missed], [grace calculation and due scan][jobs].

Cron jobs can pin an IANA timezone; absent a pin they follow the profile timezone. The pin must flow through both next-run calculation and due evaluation. Invalid persisted timezone/grace values degrade with warnings rather than killing the ticker. The pin uses `ZoneInfo`; scheduling uses cron iteration. **Unverified:** this investigation did not establish every DST fold/gap behavior from tests, so a product should specify and test ambiguous/nonexistent wall-clock deadlines rather than infer a guarantee from timezone support alone. [Timezone validation][time], [calculation and evaluation][jobs].

### Delivery, retries and security boundaries

Agent completion and delivery completion are distinct. Targeted output associated with an execution is queued durably; gateway housekeeping claims and sends it through live adapters. States progress from pending to delivering and then delivered, failed, suppressed or unknown. If the sender dies mid-send, the outcome becomes unknown and is not automatically retried. A completed model run can therefore have `delivery_queued` or `delivery_failed` status. This favors avoiding duplicate external sends over guaranteed receipt. [Queue][delivery], [documented write-back lifecycle][internals].

There is a narrow automatic rerun ladder for recurring jobs that fail with transient network/DNS errors before any completed model API call: delays of 5, 15 and 30 minutes, unless the natural next occurrence is sooner; it is configurable with `cron.retry_unreachable`. This is not permission to retry a partially executed arbitrary task. Scripts may also precede model execution, so “zero model calls” alone should not be copied as a proof that an entire workflow had no side effects. The default agent watchdog measures inactivity (600 seconds, configurable), while scripts have a separate default 3,600-second timeout. [Retry classification][retry], [agent watchdog][scheduler], [script timeouts][script].

Cron disables interactive messaging/clarification toolsets and, by default, recursive scheduling. An explicit configuration can enable agent-managed scheduling; configured toolset denials still apply. Scripts must resolve to files within the active profile's scripts directory, including checks against traversal and symlink escapes. These restrictions are useful safeguards, but directory validation and process isolation are not a sandbox or a domain authorization policy. This investigation did not audit every tool approval branch. [Toolset restrictions][scheduler], [script validation][script].

For this project, a schedule must never widen consent: case monitoring authorizes checking selected correspondence and notifying the owner, not drafting, sending company email, or resolving a case. That distinction is already in [our glossary](../../GLOSSARY.md).

## Comparison with this application

| Concern | Current personal-agent behavior | Implication |
| --- | --- | --- |
| Trigger | `TelegramService.run()` awaits `poll_once()`, including all accepted owner turns, then awaits the background tick. Telegram errors skip that tick. | Long conversations and Telegram outages can delay monitoring. Monitoring can also delay the next chat poll. |
| Work scope | `MonitoringWorker.tick()` drains pending notifications, claims up to five checks serially, runs domain reply checking, and drains deliveries again. | Already a useful bounded service boundary; preserve it. |
| Persistence | Django `CaseMonitor`, reminders and `MonitorNotification` records persist scheduling and notification state. | Do not add a parallel JSON source of truth. |
| Claiming | A transaction reserves a check by moving `next_check` forward 300 seconds. Job validity uses generation, owner deletion epoch, active state and selected correspondence. | Supports restart retry and domain cancellation, but a time-only reservation is not full long-running-worker ownership. |
| Retry | Unverified checks increase an exponential delay capped at 3,600 seconds; a verified check schedules the next check 300 seconds later. | Appropriate bounded polling behavior, distinct from arbitrary agent-task retries. |
| Delivery | `attempted=True` is persisted before send; failed/ambiguous handoffs are not blindly resent. Consent/account/case validity is rechecked. | Closely matches Hermes's conservative delivery boundary; retain it. |
| Availability | Owner-facing text already says the local bot and Mac must be running/awake. | A standalone worker improves independence, not sleep-time availability. |

Sources: [Telegram service](../../telegram_bot/service.py), [command wiring](../../telegram_bot/management/commands/telegram.py), [monitoring service](../../gmail/monitoring.py), [monitoring store](../../gmail/monitoring_store.py), [monitoring constants](../../gmail/monitoring_constants.py).

**Concurrency inference:** after a check exceeds its five-minute reservation, another worker can claim the same monitor without changing its generation. The older job can still satisfy `locked_job()`. Before enabling multiple workers, add a distinct claim token/lease and reject stale completions, with renewal or a defined execution deadline. Existing generation/epoch checks protect consent and case changes; they do not identify every successive execution.

## Proposed next learning milestone, then pause for review

Build only the independent runtime boundary: a thin Django management command calling a class-based scheduler service, which injects the existing monitoring worker and clock, runs bounded ticks independently of Telegram, and shuts down cleanly. Remove the competing Telegram-owned tick when selecting this deployment mode. Persist last heartbeat, last successful tick and a sanitized last failure; make stopped/stale status visible. Keep one worker/process owner and sequential checks for this milestone.

Validate observable behavior: a long Telegram request cannot prevent a due check; restart retains due work; repeated tick failure remains visible and the loop recovers; shutdown leaves handoffs honest; stopped monitoring/deleted owner state prevents notifications. Test a sleeping/offline gap with the existing bounded catch-up rather than replaying every five-minute interval.

Later, only when required: strengthen execution fencing before concurrency; distinguish occurrence time from lease expiration; choose explicit late-reminder rules with the owner; deploy on an awake host for reliable deadlines. A generic arbitrary-prompt scheduler, external queue, distributed workers, or Chronos-style hosted trigger is not justified by the current single-owner workload. These are recommendations, not implementation authorization.

## Evidence limits and documentation conflicts

This was a read-only source investigation; no Hermes process, remote scheduler, real Gmail check or delivery was run. No claim of end-to-end operational validation is made. The official `cron/AGENTS.md` says `skip_memory=True`, while current agent construction passes false. The developer guide also describes all expired one-shots as never resurrected, whereas current `missed_runs.py` includes explicit opt-in and outage/hold exceptions. The report follows executable source at the pinned commit where those sources disagree. Avoid relying on an older “just a 60-second JSON cron loop” description.

[jobs]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/jobs.py
[scheduler]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler.py
[tick]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler_tick.py
[provider]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler_provider.py
[supervisor]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler_thread.py
[executions]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/executions.py
[delivery]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/delivery_queue.py
[authority]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler_authority.py
[occurrences]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/occurrences.py
[missed]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/missed_runs.py
[time]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/jobs_time.py
[retry]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/unreachable_retry.py
[script]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler_script.py
[prompt]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/scheduler_prompt.py
[guide]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/website/docs/user-guide/features/cron.md
[internals]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/website/docs/developer-guide/cron-internals.md

