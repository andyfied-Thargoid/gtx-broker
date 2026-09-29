# GTX Broker Deployment Notes for compute01

## Profile Switching Setup

### 1. Sudo Rule for Wrapper Script
Create `/etc/sudoers.d/gtx-broker-profile-switch` with:
```
andyfied ALL=(root) NOPASSWD: /usr/local/sbin/compute01-maint/p40-switch-profile
```

### 2. Update Controller to Use Sudo
In `model_profiles.py`, change:
```python
result = subprocess.run(
    [self.SWITCH_WRAPPER_PATH, profile_name],
    ...
)
```
to:
```python
result = subprocess.run(
    ["sudo", "-n", self.SWITCH_WRAPPER_PATH, profile_name],
    ...
)
```

### 3. Lock Directory Permissions
Create system user/group or use existing:
```bash
sudo groupadd -r gtx-broker
sudo usermod -a -G gtx-broker andyfied
sudo mkdir -p /run/lock/gtx-broker
sudo chown root:gtx-broker /run/lock/gtx-broker
sudo chmod 2770 /run/lock/gtx-broker
```

### 4. Install Scripts and Service
```bash
sudo cp scripts/p40-switch-profile /usr/local/sbin/compute01-maint/
sudo chmod +x /usr/local/sbin/compute01-maint/p40-switch-profile
sudo cp scripts/p40-profile-launcher ~/src/gtx-broker/scripts/
sudo chmod +x ~/src/gtx-broker/scripts/p40-profile-launcher
sudo cp etc/systemd/system/llama-qwen35.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart llama-qwen35.service
```

### 5. Install Test Image (for vision smoke tests)
```bash
sudo mkdir -p /usr/local/share/gtx-broker/test-images
sudo cp test-images/receipt_small.jpg /usr/local/share/gtx-broker/test-images/
```

## Profile Configuration

Each profile has a `.conf` file in `/etc/llama-cpp/profiles/`:

**Coding Profile (qwen35-coding.conf):**
```
MODEL_PATH=/mnt/scratch/models/qwen3.5/Qwen3.5-35B-A3B-UD-Q3_K_XL.gguf
CHAT_TEMPLATE=qwen3.5
QUANTIZATION=Q3_K_XL
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=4
MODEL_ID=qwen3.5-35b-ud-q3_k_xl
```

**Vision Profile (qwen35-vision.conf):**
```
MODEL_PATH=/mnt/scratch/models/qwen3.5/Qwen3.5-35B-A3B-Vision-Q2_K.gguf
CHAT_TEMPLATE=qwen3.5-vision
QUANTIZATION=Q2_K
GPU_LAYERS=99
CONTEXT_SIZE=262144
SERVER_SLOTS=4
MODEL_ID=qwen3.5-35b-vision-q2_k
MMPROJ_PATH=/mnt/scratch/models/qwen3.5/qwen3.5-vision-bf16.mmproj
MMPROJ_TYPE=bf16
```

## Verification

1. Check active profile:
```bash
readlink /etc/llama-cpp/p40-active.conf
```

2. Check service status:
```bash
systemctl status llama-qwen35.service
```

3. Test profile switch (requires sudo):
```bash
sudo /usr/local/sbin/compute01-maint/p40-switch-profile qwen35-vision
```

4. Verify smoke test works:
```python
from gtx_broker.scheduler.model_profiles import P40ModelProfileController
controller = P40ModelProfileController()
assert controller.ensure_profile("qwen35-vision")
```

## WRONG_MODEL_LOADED Handler

Wire up the alert handler to call `ensure_profile()`:

```python
# In daemon or alert handler
@task_handler
def handle_wrong_model_loaded(task):
    controller = P40ModelProfileController()
    if controller.ensure_profile(task.expected_profile):
        task.retry(reason="Profile switched successfully")
    else:
        task.fail(reason="Failed to switch profile")
```
