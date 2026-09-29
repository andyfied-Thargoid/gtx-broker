# GTX Broker Test Images

This directory contains deterministic test images for vision profile smoke tests.

## receipt_small.jpg

A simple deterministic test image simulating a receipt with the value "29.24".

Used by:
- `tests/test_model_profiles.py::test_vision_smoke_test_success_with_known_answer`
- `scripts/p40-switch-profile` (multimodal smoke test)
- `gtx_broker/scheduler/model_profiles.py::P40ModelProfileController._run_smoke_test`

### Verification
The vision smoke test verifies:
1. Image exists at `/usr/local/share/gtx-broker/test-images/receipt_small.jpg`
2. API returns response containing "29.24"

### Regeneration
```python
from PIL import Image

img = Image.new('RGB', (100, 100), color='white')
# ... draw crude "29.24" text ...
img.save('test-images/receipt_small.jpg', 'JPEG', quality=85)
```
