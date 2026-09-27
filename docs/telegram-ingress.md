# Telegram image ingress

`gtx-image-ingress --json-stdin` is the broker-owned handoff for the optional
Hermes Telegram media hook. It reads one JSON event from standard input and
writes one JSON result to standard output.

The event must include `source_path`. It may include `chat_id`, `message_id`,
`user_id`, `kind`, `caption`, and `media_group_id`. The command validates the
file with the storage contract, copies it atomically below the configured
storage root, records quality metrics, and queues an idempotent nightly vision
task. Replaying the same chat/message/file event returns the existing task.

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

The scheduler daemon moves accepted input from `incoming/` to `processing/`
before a worker reads it, then moves it to `processed/` on completion or back
to `incoming/` for retry. Retention cleanup remains a separate operational
task; this integration does not claim a fixed deletion interval.
