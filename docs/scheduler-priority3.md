# Scheduler Priority 3 implementation

This stage makes the execution policy durable in the broker rather than
leaving it only in the planning notes.

Implemented:

- task schedule metadata for immediate, batch, and nightly work;
- explicit review tags and review-worker selection;
- P40-aware queue ordering for coding and vision tasks when they are eligible;
- image-window gating so batch work waits while vision tasks remain pending;
- an explicit unavailable `air-review` worker profile, with no silent fallback
  to the P40 or GTX;
- durable batch epochs and a review barrier;
- persisted review evidence on an epoch.

The follow-on integration stage is included in the same branch:

- `ImageQualityGate` records dimensions, orientation, brightness, clipping,
  focus, content hash, and deterministic `pass`/`degraded`/`needs_review`/
  `reject` status;
- vision output contracts now distinguish receipt extraction from the opt-in
  general `image_description` schema, with actual image-content validation;
- receipt ingress defaults to durable human review, and
  `VisionReviewService` records approval or rejection before the scheduler
  reaches a terminal state;
- `gtx-image-ingress --json-stdin` validates and atomically stages a Telegram
  image, records source metadata, applies the quality gate, and creates an
  idempotent nightly vision task;
- `CodingHandler` provides an explicit P40 executor/worktree/test boundary and
  rejects successful-but-dirty worktrees when a commit is required;
- `ReviewHandler` provides an explicit read-only Air-review command boundary,
  validates structured findings, and fails if the reviewer changes the
  worktree.
- daemon restart reconciliation keeps scheduler and durable storage outcomes
  recoverable when either side is interrupted;
- configurable retention removes only terminal processed/rejected inputs and
  stale staging directories.

P40-backed tasks are preferred within the eligible queue. The policy still
keeps image work ahead of ordinary batch work during 00:00-06:00, and general
conversation remains a GTX task. The scheduler does not switch models or
touch the GTX service.

Not implemented by this stage:

- production model-profile switching;
- a live Air reviewer runtime;
- a configured production coding executor;
- the upstream Hermes adapter hook (the broker command is ready and the Hermes
  checkout documents the optional hook);
- a Telegram-facing review acknowledgement/approval adapter;
- live Air/Coder-Next services;
- general image-description schemas: the current vision handler remains
  receipt-specific until separate prompts and validation are added.

Those remain separate stages with their own worker and integration tests.
