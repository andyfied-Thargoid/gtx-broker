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

P40-backed tasks are preferred within the eligible queue. The policy still
keeps image work ahead of ordinary batch work during 00:00-06:00, and general
conversation remains a GTX task. The scheduler does not switch models or
touch the GTX service.

Not implemented by this stage:

- live model-profile switching;
- the Air reviewer runtime;
- coding repository execution;
- Telegram/Hermes image handoff;
- image-quality metrics and retention cleanup.

Those remain separate stages with their own worker and integration tests.
