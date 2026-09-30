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
- GET /controls returns the durable scheduler controls, P40 state, and recent
  control events.
- POST /controls changes exactly one control. Use `{"schedule":"off"}` to
  stop new claims, `{"planning":"on"}` to drain the P40 for operator
  planning, or `{"mode":"night"}` to run the night dispatch policy outside
  the normal clock window. Optional `actor` and `reason` fields are recorded
  in the audit trail. Turning schedule off does not change planning mode; the
  response always includes both values so automation can restore the prior
  planning state safely.
- POST /cancel with {"task_id": "..."} cancels tasks in queued or claimed state.
  It rejects tasks that are already running or terminal.

Planning mode prevents new P40-bound work from being claimed while allowing
the active task to finish. The P40 state is `draining` while busy and changes
to `ready` when it becomes free. Review and slow-coder work can continue while
planning mode is enabled. Schedule off prevents all new claims and is
independent of planning mode.

All responses are JSON. The API is intended for local scheduler/broker tooling,
not as a general-purpose public service.
