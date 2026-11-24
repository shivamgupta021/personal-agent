# Hermes email and local data: findings for a personal installation

Researched 24 November 2026 against the official [NousResearch/hermes-agent repository](https://github.com/NousResearch/hermes-agent). GitHub's `commits/main` endpoint still resolved to **084538cdb605f3aeacbbeb32903bceefed59a905** during this investigation; all Hermes links below pin that revision. This is research, not an accepted design or implementation milestone. No Hermes runtime, credentials, mailbox, or community plugin was installed or exercised.

## Recommendation

For an open-source application that each person clones and runs themselves, borrow **a private runtime home separate from source code, explicit ownership of persistence, and distinct conversation/memory/domain/job records**. Keep Gmail reply monitoring in the application's deterministic domain service. Hermes's Email gateway is useful for talking *to an agent by email*; it is not the right reference implementation for reliably watching selected grievance correspondence.

Repository packaging and runtime ownership are separate decisions. A main personal-agent repository plus separately distributed grievance/other agent packages can still run as one installation with one owner, one scheduler and one database. Multiple repositories do not require a SaaS tenancy model or a database per agent. These are design recommendations, not claims about Hermes or authorization to split this repository.

## What “Hermes supports email” actually means

| Mechanism | Verified purpose and behavior | Relevance here |
| --- | --- | --- |
| Bundled Email platform plugin | Receives messages addressed to the agent over IMAP, runs conversations, sends replies over SMTP. The official guide recommends a dedicated agent mailbox. | A communication channel, not a read-only personal Gmail monitor. |
| Bundled Himalaya skill | Instructions for using an external `himalaya` CLI with IMAP/SMTP credentials and `~/.config/himalaya/config.toml`. It supports reading and mailbox mutations through terminal commands. | A connector option, with additional external runtime/configuration. |
| Bundled Google Workspace skill | Hermes OAuth setup and `google_api.py`; prefers external `gws` when available, otherwise bundled Python client behavior. Exposes Gmail search/get/send/reply/labels/modify alongside other Google services. | Real Gmail API access exists, but the skill is not a built-in domain-specific background watcher. |
| Community catalog plugins | The `gmail` catalog entry points to `guberm/hermes-gmail-desktop-plugin`; `google-workspace` points to `francoleff/google-workspace-hermes`. Both are explicitly `tier: community`. | Do not attribute third-party plugin behavior to the audited Hermes core. Their implementations were not audited here. |

Sources: [Email guide][email-guide], [Email implementation][email], [Himalaya skill][himalaya], [Google Workspace skill][workspace], [Google API script][google-api], [Gmail catalog entry][gmail-catalog], [Workspace catalog entry][workspace-catalog].

The bundled inbox-triage skill separates provider commands from thread-aware decision policy. It asks for bounded account/folder/time scope, complete relevant threads, known pagination gaps, and explicit mutation authority; it treats email contents as data and warns against retrying ambiguous sends. These are useful product principles. They are instructions to an agent, not proof that every execution path enforces them in code. Its suggested read-and-draft default is also broader than our case-monitoring permission, which authorizes checking and notifying, not drafting. [Triage skill][triage], [local glossary](../../GLOSSARY.md).

## Polling, watches, and availability

The Email plugin has its **own asynchronous polling loop**, with `EMAIL_POLL_INTERVAL` defaulting to 15 seconds. It searches IMAP `UNSEEN`, uses a bounded in-memory UID set, and sends via SMTP. This is independent of scheduling a cron prompt. On initial connection it adds all current mailbox UIDs to that set, skipping existing mail; same-process reconnects reuse a per-account snapshot so outage arrivals remain eligible. The snapshot is not a durable restart cursor. A new process can therefore skip messages that arrived while the process was stopped. The guide calls startup behavior “marks ... seen”; the source specifically snapshots UIDs locally at initial connect. During processing it also sets server Seen flags, including on rejected messages. [Email implementation][email], [guide][email-guide].

That tradeoff suits an agent mailbox avoiding old-message reply floods. It is unsuitable for grievance monitoring: do not use unread status as the source of truth, mutate Seen as a monitoring checkpoint, skip all mail at restart, or treat all messages from one sender as one case. The adapter's `_thread_context` is keyed by sender address; selected Gmail correspondence must retain provider thread/message identities and case ownership. These are recommendations inferred from the implementation and our [domain definitions](../../GLOSSARY.md).

The inspected Google API script has Gmail search/get/send/reply/labels/modify commands, with no `watch`/Pub/Sub/history synchronization command. This is a scoped finding about this bundled path, **not a claim that no external plugin or generic `gws` invocation can implement push**. Cron could periodically invoke a mailbox-reading task, but doing so does not supply a durable message cursor, selected-thread policy, or notification deduplication automatically. See the companion [scheduling investigation](hermes-scheduling.md) for the independent ticker, jobs, attempts and deliveries.

Google's actual Gmail push design uses `users.watch` plus Cloud Pub/Sub. Notifications carry mailbox email/history ID; the consumer retrieves changes using `history.list`. Watches must renew at least every seven days (Google recommends daily). Notifications can be delayed/dropped, so reconciliation remains necessary. Pub/Sub can use a push webhook **or a pull subscription**; a public webhook is not intrinsically required. Google recommends polling synchronization for user-owned installed devices. [Official Gmail push guide](https://developers.google.com/workspace/gmail/api/guides/push).

**Deployment inference:** start with bounded polling of selected correspondence for clone-per-person operation. An independent worker improves availability relative to tying monitoring to Telegram turns, but cannot run on a sleeping/offline laptop. A person can self-host the same installation on an awake host without turning the product into shared SaaS. Add push only for a measured latency/load benefit and an operating environment that can maintain watches and reconciliation.

## Local data ownership and storage

Hermes resolves a runtime home, normally `~/.hermes`, with `HERMES_HOME` overrides and named profiles under the default root's `profiles/<name>`. Profiles bootstrap separate memories, sessions, skills, logs, plans, workspace and cron directories. `persistence_assembly.py` resolves a `ProfileScope` and returns a persistence handle whose repositories act on that profile; SQLite is the currently known/default provider in that assembly. These are useful ownership boundaries, not evidence that arbitrary code or terminal subprocesses are isolated by profile names. [Profiles][profiles], [persistence assembly][assembly], [skill home resolution][home].

| Data category | Verified local representation | Lesson to borrow |
| --- | --- | --- |
| Settings/secrets | Profile `config.yaml` and `.env`; credential stores are separately identified for protection/export handling. | Keep code defaults separate from runtime settings and secrets. |
| Google OAuth | Active home `google_token.json`, `google_client_secret.json`; setup documents temporary `google_oauth_pending.json`. API script resolves the active home. Private JSON writer creates mode-0600 temporary files, fsyncs and atomically replaces. | One explicit credential owner, restricted filesystem writes, reconnect/revoke flow. Mode 0600 is not encryption. |
| Conversation history | SQLite `state.db`: sessions, messages, prompts, usage and runtime records; full-text search indexes. | Conversation storage is not the case database's domain model. |
| Curated memory | `memories/MEMORY.md` and `USER.md`; documented bounded default capacities and a snapshot inserted at session start. | Small durable preferences and notes should remain distinguishable from raw conversation history. |
| Scheduled work | `cron/jobs.json`, execution/delivery SQLite ledgers and output files. | Definition, execution attempt and delivery are different states; retain existing Django persistence rather than add a second store. |
| Portable configuration versus live work | Profile cloning deliberately excludes history and cron; exports exclude credential material and scrub staged text. | Distributing code/agent definitions must not duplicate live monitors or copy user credentials/history. |

Sources: [profile implementation][profiles], [home data classification][layout], [Workspace skill][workspace], [API script][google-api], [private JSON writer][private-json], [schema][schema], [memory guide][memory], [scheduling research](hermes-scheduling.md).

The SQLite schema is considerably richer than “a list of chat messages.” `sessions` includes source, user/chat/thread/session routing identifiers, parent lineage, model/config/prompt references, timestamps, profile, token/cost counts and runtime generations. `messages` references a session and stores role/content, tool call IDs and structured tool calls, timestamps, platform message ID, reasoning/API/display fields and compaction flags. There are separate model-usage rows, runtime epoch, admissions, worker executions and receipts. This is execution/conversation bookkeeping; none of these fields establishes our selected correspondence or monitoring consent rules. [DDL][schema].

Do not copy the entire schema. For our application, retain distinct records for conversation context, explicitly saved preferences, grievance cases/selected correspondence, account connection, monitor/claim, and notification handoff. A reply notification should remain traceable to its owner, account, provider message/thread, case and observation even if chat history is compacted or replaced. This is a domain recommendation based on the separation above and the [glossary](../../GLOSSARY.md).

The execution/delivery distinction is concrete in the cron schemas: an execution stores `id`, `job_id`, source, process identity, status, claim/start/finish times, error and `scheduled_instant`. A delivery is keyed by `execution_id` and stores a job snapshot (`job_json`), content, failure flag, sender ownership and its own status/timestamps/error. Its statuses include `pending`, `delivering`, `delivered`, `failed`, `unknown`, and `suppressed`. Thus a completed check and an uncertain notification send are representable independently. Borrow that distinction, rather than a single Boolean `done`. [Execution schema](https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/executions.py), [delivery schema](https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/cron/delivery_queue.py).

## Memory and multi-agent cautions

Hermes documents automatic memory writes by default, including background review, and optional approval staging. It also explicitly says the memory-tool approval setting is not a filesystem lock: generic file/terminal tools can edit memory files directly. Our saved-preference definition requires explicit request or approval. Borrow bounded memory and visible edit/delete controls; **do not import proactive preference inference or treat a prompt-level gate as enforcement**. The guide warns against independent agent processes sharing the same memory home. [Memory guide][memory], [local saved-preference definition](../../GLOSSARY.md).

For several specialist agents in one personal installation, the simplest recommendation is an orchestrator that owns runtime state and passes scoped services to specialists. Shared owner preferences can be deliberately shared while case-specific data stays scoped to the relevant task. Do not give every separately packaged agent an independent unrestricted writer into one memory directory, credential directory or SQLite schema. If specialists become separate processes, define an application API and execution ownership first; separate source repositories alone are not a reason to do this.

The hosted demo is a separate deployment concern. Keep its runtime data and credentials separate from personal installations; avoid a public shared profile retaining visitors' private mailbox data. Real multi-user hosted access would require additional identity and authorization design, but that requirement should not be imposed on the clone-per-person product merely because a demo exists.

## Next reviewable scope

No application changes are proposed as completed. The useful immediate implementation milestone remains the [independent monitoring worker](hermes-scheduling.md), preserving existing selected-correspondence checks and durable notification state. A later packaging milestone can establish a configurable private application data directory, migration/backup rules and a clean installation with no personal state. Review each milestone separately, as required by this repository's instructions.

Evidence limits: source and official documentation inspection only; no operational reliability or OAuth verification claims. The community catalog entries establish provenance/descriptions, not independently verified plugin behavior. No exhaustive negative claim about all Hermes plugins is made. Provider/account-specific app-password availability was not checked; the Hermes setup guide is not a substitute for Google's current account policies.

[email-guide]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/website/docs/user-guide/messaging/email.md
[email]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/plugins/platforms/email/adapter.py
[himalaya]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/skills/email/himalaya/SKILL.md
[triage]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/skills/email/email-inbox-triage/SKILL.md
[workspace]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/skills/productivity/google-workspace/SKILL.md
[google-api]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/skills/productivity/google-workspace/scripts/google_api.py
[home]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/skills/productivity/google-workspace/scripts/_hermes_home.py
[private-json]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/skills/productivity/google-workspace/scripts/_secure_storage.py
[gmail-catalog]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/plugin-catalog/gmail.yaml
[workspace-catalog]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/plugin-catalog/google-workspace.yaml
[profiles]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/hermes_cli/profiles.py
[layout]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/hermes_cli/home_data_layout.py
[assembly]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/persistence_assembly.py
[schema]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/persistence/sqlite/_schema_ddl.py
[memory]: https://github.com/NousResearch/hermes-agent/blob/084538cdb605f3aeacbbeb32903bceefed59a905/website/docs/user-guide/features/memory.md
