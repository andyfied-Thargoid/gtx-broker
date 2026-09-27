# Telegram image ingress

`gtx-image-ingress --json-stdin` is the broker-owned handoff for the optional
Hermes Telegram media hook. It reads one JSON event from standard input and
writes one JSON result to standard output.

The event must include `source_path`. It may include `chat_id`, `message_id`,
`user_id`, `kind`, `caption`, `media_group_id`, `schema`, and `prompt`.
`schema` is `receipt` by default. Use `image_description` for non-receipt
images; the broker stores that selection in the task payload and validates the
corresponding structured response. Receipt tasks default to
`requires_review=true`; callers may explicitly set `requires_review=false` only
for workflows that have separately been approved for unattended processing. A
caller-provided `prompt` is permitted for an approved schema but does not bypass
output validation.

The command validates the file with the storage contract, verifies image
content, copies it atomically below the configured storage root, records quality
metrics, and queues an idempotent nightly vision task. Replaying the same
chat/message/file event returns the existing task. Unknown schemas are rejected
before anything is staged.

Set `GTX_SCHEDULER_DB` to the scheduler database path when using a non-default
storage root. The storage root is the parent of the database's `metadata`
directory; the default database is
`/mnt/scratch/gtx-images/metadata/tasks.db`.

Hermes should invoke the command only after its normal authorization and media
download checks. Configure its optional
`platforms.telegram.extra.broker_image_ingress_command` hook with the command
and preserve the JSON result for the user-facing acknowledgement. If the
command is unavailable or returns a rejection, Hermes should retain its normal
temporary-cache behavior and report that processing could not be queued.

The broker's VisionReviewService is the storage/scheduler boundary for the
authorized review UI: it lists `awaiting_review` tasks and records an approval
or rejection before making the scheduler transition. A Telegram-facing adapter
must enforce the Hermes approval list before calling those methods.

The scheduler daemon moves accepted input from `incoming/` to `processing/`
before a worker reads it, then moves it to `processed/` on completion or back
to `incoming/` for retry. Retention cleanup remains a separate operational
task; this integration does not claim a fixed deletion interval.
