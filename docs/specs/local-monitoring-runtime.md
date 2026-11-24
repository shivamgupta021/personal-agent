# Local monitoring runtime

This slice implements the owner's request to borrow independent scheduling from
Hermes while retaining the application's Gmail API and Django persistence. The
owner clarified that normal local use must not require two separate processes.

## Behavior

- `manage.py telegram` starts chat polling and scheduled case monitoring together.
- Monitoring is an independent async loop, not a step after each Telegram request.
- Due work is checked every 60 seconds. Existing per-case cadence, selected-thread
  permissions, reminders, backoff, restart reconciliation and uncertain-delivery
  behavior remain intact. No IMAP, SMTP or Pub/Sub setup is added.
- Chat and monitoring share queued access to the subscription session in this
  process. Separate commands retain the existing fail-fast lock behavior.
- In the combined application, long Telegram responses and notifications must not
  interleave message chunks. Separately managed processes do not share that queue.
- Worker ownership is exclusive per local state directory, with a content-free
  heartbeat. This does not provide distributed ownership across hosts.
- Fatal loop exit stops the combined application visibly. Shutdown cancels both
  loops, closes clients and releases locks. Interrupted turns require manual resend.
- Advanced deployments may use `telegram --chat-only` plus `monitor` separately;
  `monitor --once` performs actual due work and is not a dry run.
- Existing cases, history, monitoring choices, credentials and Gmail drafts are
  preserved. A fresh restart does not mean deleting application data.

## Verification

Public runtime tests cover independent progress, shutdown and fatal exit.
Public subscription operations with temporary credentials verify queued access,
waiting-request cancellation and independent-client rejection without model calls.
Existing public monitoring tests cover reminders, lifecycle invalidation, restart,
notification deduplication and uncertainty. Run the full regression suite and
mypy; then start the merged application and verify the poller and scheduler are
alive. Live user messages validate the Telegram experience separately.

## Deferred

Cross-device exact-draft navigation, portable credential backends, hosted demo
isolation and the broader domain-model review are separate milestones. This slice
makes no schema changes and does not promise a laptop will run while asleep.
