# Model-Profile Controller Implementation Notes

## Overview

This document captures the implementation details of the Model-Profile Controller for P40 runtime profile management on compute01.

## Architecture

### Component Flow

```
Task execution request → ensure_profile(profile)
                          ↓
                    Acquire flock lease
                          ↓
                    Check current model (GET /v1/models)
                          ↓
                    If different:
                      - Call switch wrapper via sudo
                      - Wrapper: symlink update → systemctl restart → verify
                          ↓
                    Verify: health → model ID → smoke test
                          ↓
                    Release lease
                          ↓
                    Return success/failure
```

### Context Manager Pattern

```python
with controller.profile("qwen35-vision") as ctx:
    # Task executes while lease is held
    # Prevents concurrent profile switches
    result = do_inference()
```

**Lock lifetime**: `lock → verify/switch → yield (task executes) → unlock`

This prevents WRONG_MODEL_LOADED from occurring mid-task.

## File-Based Lease

### Location and Permissions

- **Path**: `/run/lock/gtx-broker/p40-profile.lock`
- **Directory**: `root:gtx-broker`, mode `2770` (setgid for group inheritance)
- **Lock file**: mode `0660` (group-readable/writable)

### Implementation

```python
def _acquire_lease(self, blocking: bool = True) -> Optional[int]:
    self._ensure_lock_dir()
    lock_fd = os.open(str(self.LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o660)
    import fcntl
    lock_type = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(lock_fd, lock_type)
        return lock_fd
    except (IOError, OSError):
        if not blocking:
            os.close(lock_fd)
            return None
        raise
```

### Why flock over systemd lock?

1. **Granularity**: systemd provides service-level lock, not task-level lease
2. **Concurrency control**: Multiple workers can request same profile simultaneously
3. **Timeout handling**: flock supports non-blocking mode for retry logic
4. **Process safety**: File lock survives process restart (unlike in-memory state)

## Configuration Source of Truth

### Why .conf files (not YAML)?

1. **Single source**: Systemd can `source` the file directly
2. **Atomic updates**: `ln -sfn` replaces symlink atomically
3. **Simpler parsing**: No YAML dependency, just `KEY=VALUE`
4. **Familiar**: Matches existing llama.cpp config format

### Profile naming convention

```
qwen35-coding      # family qwen3.5, role coding
qwen35-vision      # family qwen3.5, role vision
qwen36-vision      # family qwen3.6, role vision
```

**Format**: `<family>-<role>` (e.g., `qwen3.5-coding` → `qwen35-coding.conf`)

**Not included in name**: quantization, GGUF filename, context size (these are metadata in config)

## Verification Layers

### 1. Systemd Service Active

```python
def _check_service_active(self) -> bool:
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", self.SERVICE_NAME],
        capture_output=True, timeout=10
    )
    return result.returncode == 0
```

**Why first?**: If service isn't running, subsequent checks will fail anyway.

### 2. Health Endpoint

```python
def _check_health(self) -> bool:
    import urllib.request
    with urllib.request.urlopen(f"http://{self.HOST}:{self.PORT}/health", timeout=5.0) as response:
        return response.status == 200
```

**Why?**: Confirms llama-server responded to restart and is listening.

### 3. Model ID Verification

```python
def _get_current_model_id(self) -> Optional[str]:
    import urllib.request
    with urllib.request.urlopen(f"http://{self.HOST}:{self.PORT}/v1/models", timeout=5.0) as response:
        data = json.loads(response.read().decode("utf-8"))
        if "data" in data and len(data["data"]) > 0:
            return data["data"][0].get("id")
    return None
```

**Why?**: Confirms correct model loaded (not just "server running").

**Expected format**: `qwen3.5-35b-ud-q3_k_xl` (from `MODEL_ID` in config).

### 4. Smoke Test

#### Text-only profiles

```python
message = {
    "model": profile.expected_model_id,
    "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
    "max_tokens": 8,
    "temperature": 0
}
```

**Deterministic**: "OK" is unambiguous.

#### Vision profiles

```python
test_image = "/usr/local/share/gtx-broker/test-images/receipt_small.jpg"
with open(test_image, "rb") as f:
    image_data = base64.b64encode(f.read()).decode("utf-8")
message = {
    "model": profile.expected_model_id,
    "messages": [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_data}"}},
            {"type": "text", "text": "Extract the total amount from this receipt. Reply with exactly: 29.24"}
        ]
    }],
    "max_tokens": 16,
    "temperature": 0
}
```

**Why 29.24?**: Known answer from test image, not inferred.

**Why base64?**: llama.cpp OpenAI-compatible endpoint accepts inline image data.

## Vision Profile Support

### Problem

Standard systemd unit uses fixed arguments:
```ini
ExecStart=/usr/local/bin/llama-server --model /path/to/model.gguf ...
```

Vision profiles need `--mmproj` and `--mllm yes` flags.

### Solution: Profile Launcher Script

```bash
#!/bin/bash
ACTIVE_CONFIG="/etc/llama-cpp/p40-active.conf"
CONF_FILE=$(readlink -f "$ACTIVE_CONFIG")
source "$CONF_FILE"

BASE_ARGS=(
    "--model" "${MODEL_PATH}"
    "--chat-template" "${CHAT_TEMPLATE}"
    # ...
)

if [[ -n "${MMPROJ_PATH:-}" ]]; then
    BASE_ARGS+=(
        "--mmproj" "${MMPROJ_PATH}"
        "--mllm" "yes"
    )
fi

exec /usr/local/bin/llama-server "${BASE_ARGS[@]}"
```

**Benefits**:
1. No hard-coded profile in systemd unit
2. Vision args only included when `MMPROJ_PATH` defined
3. Atomic config switch via symlink update

### systemd Unit

```ini
[Service]
Type=simple
ExecStart=/home/andyfied/src/gtx-broker/scripts/p40-profile-launcher
```

**Note**: No profile name argument. Launcher reads active symlink.

## WRONG_MODEL_LOADED Handler

### When to trigger

1. **API returns unexpected model ID**: `/v1/models` returns different ID than task expects
2. **Vision path broken**: Vision task fails with "no projector" error
3. **Explicit alert**: External monitoring detects model mismatch

### Implementation pattern

```python
from gtx_broker.scheduler.model_profiles import P40ModelProfileController, ModelProfileError

@task_handler
def handle_wrong_model_loaded(task):
    """Retry task with correct profile."""
    controller = P40ModelProfileController()
    
    try:
        # Ensure correct profile is loaded
        if not controller.ensure_profile(task.expected_profile):
            task.fail(reason=f"Failed to switch to profile {task.expected_profile}")
            return
        
        # Task will retry with correct model
        task.retry(reason="Profile switched successfully")
        
    except ModelProfileError as e:
        task.fail(reason=f"Profile error: {e}")
```

### Using context manager for task execution

```python
def run_task_with_profile(task):
    controller = P40ModelProfileController()
    
    with controller.profile(task.expected_profile):
        # Lease held during entire task execution
        # Prevents concurrent switches
        result = do_inference(task.input)
    
    return result
```

## Testing

### Unit Tests (`test_model_profiles.py`)

```python
def test_context_manager_holds_lock_during_yield():
    """Verify lease covers task execution."""
    with patch.object(controller, '_acquire_lease') as mock_acquire:
        with patch.object(controller, '_release_lease') as mock_release:
            with controller.profile("qwen35-coding") as ctx:
                # Lock acquired, not yet released
                assert mock_release.call_count == 0
            # Lock released after context exits
            assert mock_release.call_count == 1
```

### Integration Tests (`test_profile_integration.py`)

```python
def test_vision_smoke_test_parses_29_24():
    """Vision smoke test correctly validates known answer."""
    # Mock API response with correct answer
    mock_response = {"choices": [{"message": {"content": "The total is 29.24"}}]}
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.read.return_value = json.dumps(mock_response).encode()
        
        result = controller._run_smoke_test("qwen35-vision")
        assert result is True  # Contains "29.24"

def test_vision_smoke_test_rejects_wrong_answer():
    """Vision smoke test fails if response doesn't contain 29.24."""
    mock_response = {"choices": [{"message": {"content": "The total is 15.50"}}]}
    
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.read.return_value = json.dumps(mock_response).encode()
        
        result = controller._run_smoke_test("qwen35-vision")
        assert result is False  # Does not contain "29.24"
```

### Smoke Test Image

**Location**: `/usr/local/share/gtx-broker/test-images/receipt_small.jpg`

**Known answer**: 29.24 (total amount)

**Purpose**: Deterministic verification of vision path (not just "some answer returned").

## Common Pitfalls

### 1. Hard-coded profile in systemd unit

❌ **Wrong**:
```ini
ExecStart=/path/to/p40-profile-launcher qwen35-coding
```

✅ **Right**:
```ini
ExecStart=/path/to/p40-profile-launcher
```

**Why**: Launcher resolves active symlink. Hard-coding defeats atomic switching.

### 2. Lock released before task execution

❌ **Wrong**:
```python
def ensure_profile(self, profile_name):
    fd = self._acquire_lease()
    try:
        self._switch_profile(profile_name)  # Switch happens here
        return True
    finally:
        self._release_lease(fd)  # Released before task
```

✅ **Right**:
```python
@contextmanager
def profile(self, profile_name):
    fd = self._acquire_lease()
    try:
        self._switch_profile(profile_name)
        yield  # Lock held during task execution
    finally:
        self._release_lease(fd)
```

### 3. Generic exception in timeout test

❌ **Wrong**:
```python
with patch("subprocess.run", side_effect=Exception("timeout")):
```

✅ **Right**:
```python
with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["test"], timeout=10)):
```

**Why**: Production code catches `TimeoutExpired`, not generic `Exception`.

### 4. Not mocking subprocess in verification test

❌ **Wrong**:
```python
with patch("os.path.exists", return_value=True):
    result = controller.ensure_profile("qwen35-vision")  # Calls real wrapper!
```

✅ **Right**:
```python
with patch("os.path.exists", return_value=True):
    with patch("subprocess.run", return_value=MagicMock(returncode=0)):
        result = controller.ensure_profile("qwen35-vision")
```

**Why**: Avoids calling real root-owned wrapper in tests.

### 5. Duplicate --mmap flag for vision

❌ **Wrong**:
```python
BASE_ARGS = ["--mmap", ...]  # Already includes --mmap
if vision:
    BASE_ARGS += ["--mmap", "--mmproj", ...]  # Duplicate!
```

✅ **Right**:
```python
BASE_ARGS = ["--model", ..., "--mmap", ...]  # Single --mmap
if vision:
    BASE_ARGS += ["--mmproj", ...]  # Only add mmproj
```

## Deployment Checklist

- [ ] Install sudo rule: `/etc/sudoers.d/gtx-broker-profile-switch`
- [ ] Update controller to use `sudo -n` for wrapper
- [ ] Create `gtx-broker` group and set lock directory permissions
- [ ] Install switch wrapper: `/usr/local/sbin/compute01-maint/p40-switch-profile`
- [ ] Install profile launcher: `scripts/p40-profile-launcher`
- [ ] Install systemd unit and reload daemon
- [ ] Install test image: `/usr/local/share/gtx-broker/test-images/receipt_small.jpg`
- [ ] Verify profile configs exist: `/etc/llama-cpp/profiles/*.conf`
- [ ] Test manual switch: `sudo /usr/local/sbin/compute01-maint/p40-switch-profile qwen35-vision`
- [ ] Wire up WRONG_MODEL_LOADED handler in daemon/alert loop

## References

- **PR**: https://github.com/andyfied-agent/gtx-broker/pull/17
- **Controller implementation**: `gtx_broker/scheduler/model_profiles.py`
- **Switch wrapper**: `scripts/p40-switch-profile`
- **Profile launcher**: `scripts/p40-profile-launcher`
- **Systemd unit**: `etc/systemd/system/llama-qwen35.service`
- **Profile configs**: `etc/llama-cpp/profiles/*.conf`
- **Tests**: `tests/test_model_profiles.py`, `tests/test_profile_integration.py`
- **Deployment notes**: `docs/deployment-notes.md`
