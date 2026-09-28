"""Safe command boundary for temporary P40 model profiles."""

from contextlib import contextmanager
import logging
import os
import shlex
import subprocess
from typing import Iterator, Optional


logger = logging.getLogger(__name__)


class ModelProfileError(RuntimeError):
    """Raised when a P40 profile cannot be applied or restored."""


class P40ModelProfileController:
    """Switch a P40 profile and always restore its configured default.

    The command is deliberately opt-in. Without
    `GTX_P40_MODEL_SWITCH_COMMAND` the boundary is a no-op, preserving the
    existing manually managed service. The command is tokenized without a
    shell; one token must contain `{profile}`.
    """

    def __init__(
        self,
        switch_command: Optional[str] = None,
        default_profile: Optional[str] = None,
        timeout: Optional[float] = None,
    ):
        self.switch_command = switch_command or os.getenv("GTX_P40_MODEL_SWITCH_COMMAND")
        self.default_profile = (
            default_profile or os.getenv("GTX_P40_DEFAULT_MODEL_PROFILE", "p40-coding")
        )
        self.timeout = timeout or float(os.getenv("GTX_P40_MODEL_SWITCH_TIMEOUT", "120"))
        self._active_profile = self.default_profile
        if self.switch_command and "{profile}" not in self.switch_command:
            raise ValueError("model switch command must contain {profile}")

    @property
    def active_profile(self) -> str:
        return self._active_profile

    def switch(self, profile: str, *, force: bool = False) -> bool:
        """Switch to a named profile, or no-op when switching is unconfigured."""
        if not profile:
            raise ValueError("profile is required")
        if not self.switch_command:
            return True
        if not force and profile == self._active_profile:
            return True
        argv = [
            token.replace("{profile}", profile)
            for token in shlex.split(self.switch_command)
        ]
        try:
            completed = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            logger.error("P40 model switch to %s failed: %s", profile, exc)
            return False
        if completed.returncode != 0:
            logger.error(
                "P40 model switch to %s exited %s: %s",
                profile,
                completed.returncode,
                (completed.stderr or completed.stdout).strip(),
            )
            return False
        self._active_profile = profile
        return True

    def restore_default(self, *, force: bool = False) -> bool:
        """Restore the configured default profile after temporary work."""
        return self.switch(self.default_profile, force=force)

    @contextmanager
    def profile(self, profile: str) -> Iterator[None]:
        """Run work under a profile and restore the default in `finally`."""
        if not self.switch(profile):
            self.restore_default(force=True)
            raise ModelProfileError(f"could not switch P40 to {profile}")
        try:
            yield
        finally:
            if not self.restore_default():
                raise ModelProfileError(
                    f"could not restore P40 default profile {self.default_profile}"
                )
