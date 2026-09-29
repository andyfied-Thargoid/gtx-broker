# Scheduler status API

The scheduler daemon starts a small operator API on loopback by default:

    http://127.0.0.1:11439

The bind address is intentionally fixed to 127.0.0.1. The API has no
authentication layer and must not be exposed through a public or shared network
interface. Access from another machine should use an authenticated tunnel or a
separate access-controlled proxy.

Set GTX_STATUS_API_PORT before starting the daemon to select another local TCP
port. Invalid values outside 1-65535 fall back to 11439.

## Endpoints

- GET /status/{task_id} returns task state, routing metadata, timestamps, and
  the five most recent task events.
- GET /queue returns counts for queued, claimed, running, succeeded, failed,
  and total tasks.
- GET /queue?task_id={task_id} returns an estimated position for a queued task.
  The estimate uses priority and creation time; it does not model schedule
  windows, review priority, or worker availability. See
  docs/queue-position-limitation.md.
- POST /cancel with {"task_id": "..."} cancels tasks in queued or claimed state.
  It rejects tasks that are already running or terminal.

All responses are JSON. The API is intended for local scheduler/broker tooling,
not as a general-purpose public service.
