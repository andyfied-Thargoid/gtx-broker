from unittest.mock import patch

import pytest

from gtx_broker.scheduler.model_profiles import (
    ModelProfileError,
    P40ModelProfileController,
)


def test_profile_restores_default_after_successful_work():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv[-1])
        return type("Completed", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    controller = P40ModelProfileController(
        switch_command="switch-p40 {profile}", default_profile="default", timeout=1
    )
    with patch("gtx_broker.scheduler.model_profiles.subprocess.run", side_effect=fake_run):
        with controller.profile("vision"):
            assert controller.active_profile == "vision"
    assert calls == ["vision", "default"]
    assert controller.active_profile == "default"


def test_profile_failure_is_explicit_and_does_not_run_work():
    controller = P40ModelProfileController(
        switch_command="switch-p40 {profile}", default_profile="default", timeout=1
    )
    failed = type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "failed"})()
    with patch("gtx_broker.scheduler.model_profiles.subprocess.run", return_value=failed):
        with pytest.raises(ModelProfileError):
            with controller.profile("vision"):
                raise AssertionError("work must not run")


def test_unconfigured_profile_boundary_is_a_noop():
    controller = P40ModelProfileController(default_profile="default")
    with controller.profile("vision"):
        assert controller.active_profile == "default"
    assert controller.active_profile == "default"
