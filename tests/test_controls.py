"""Tests for durable operator controls and P40 drain behaviour."""

from gtx_broker.scheduler import Scheduler, SchedulerConfig


def make_scheduler(tmp_path):
    return Scheduler(SchedulerConfig(db_path=str(tmp_path / "tasks.db")))


def test_controls_default_and_persist(tmp_path):
    scheduler = make_scheduler(tmp_path)

    assert scheduler.get_control_state() == {
        "schedule": "on",
        "planning": "off",
        "mode": "day",
        "p40": "available",
    }

    scheduler.set_control("planning", "on", actor="hermes", reason="planning session")
    scheduler.set_control("schedule", "off", actor="hermes")

    restarted = make_scheduler(tmp_path)
    state = restarted.get_control_state()
    assert state["schedule"] == "off"
    assert state["planning"] == "on"
    assert state["p40"] == "ready"
    assert [event["control"] for event in restarted.get_control_events()] == [
        "schedule", "p40_ready", "planning"
    ]


def test_schedule_off_blocks_claims_without_changing_planning(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("coding-1", "coding", {"task": "test"})

    scheduler.set_control("planning", "on")
    scheduler.set_control("schedule", "off")
    assert scheduler.get_next_task() is None
    assert scheduler.claim_task("coding-1") is None
    assert scheduler.get_control_state()["planning"] == "on"

    scheduler.set_control("schedule", "on")
    assert scheduler.get_control_state()["planning"] == "on"
    assert scheduler.get_next_task() is None


def test_planning_blocks_p40_but_allows_slow_coder_at_night(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("p40-work", "coding", {"task": "interactive"})
    scheduler.add_task("vision-work", "vision", {"task": "receipt"})
    scheduler.add_task(
        "night-work", "coding", {"task": "maintenance"},
        mode="batch", schedule_type="nightly",
    )

    scheduler.set_control("planning", "on")
    scheduler.set_control("mode", "night")
    pending = scheduler.get_pending_tasks(limit=None)
    assert [task["id"] for task in pending] == ["night-work"]
    assert scheduler.claim_task("p40-work") is None
    assert scheduler.claim_task("night-work")["id"] == "night-work"


def test_planning_rechecked_between_claim_and_start(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("p40-work", "coding", {"task": "interactive"})

    assert scheduler.claim_task("p40-work") is not None
    scheduler.set_control("planning", "on")
    assert not scheduler.start_task("p40-work", "p40-coding")
    assert scheduler.get_task("p40-work")["state"] == "claimed"
    assert scheduler.get_control_state()["p40"] == "draining"


def test_p40_ready_is_reported_after_active_task_finishes(tmp_path):
    scheduler = make_scheduler(tmp_path)
    scheduler.add_task("p40-work", "coding", {"task": "interactive"})

    claimed = scheduler.claim_task("p40-work")
    assert claimed is not None
    assert scheduler.start_task("p40-work", "p40-coding", "qwen3.5-35b-ud-q3_k_xl")
    scheduler.set_control("planning", "on", actor="operator")
    assert scheduler.get_control_state()["p40"] == "draining"

    assert scheduler.complete_task("p40-work", result={"ok": True})
    assert scheduler.get_control_state()["p40"] == "ready"
    ready = [event for event in scheduler.get_control_events() if event["control"] == "p40_ready"]
    assert ready

    scheduler.set_control("planning", "off")
    scheduler.set_control("planning", "on")
    ready = [event for event in scheduler.get_control_events() if event["control"] == "p40_ready"]
    assert len(ready) == 2


def test_repeated_planning_on_is_idempotent(tmp_path):
    scheduler = make_scheduler(tmp_path)

    scheduler.set_control("planning", "on")
    scheduler.set_control("planning", "on")

    ready = [event for event in scheduler.get_control_events() if event["control"] == "p40_ready"]
    assert len(ready) == 1
