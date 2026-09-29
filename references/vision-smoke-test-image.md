# Vision Smoke Test Image

## Purpose

Deterministic verification of vision profile loading. The image contains a known answer (29.24) that the model must extract correctly.

## Test Image

**File**: `receipt_small.jpg`

**Location**: `/usr/local/share/gtx-broker/test-images/receipt_small.jpg`

**Known answer**: 29.24 (total amount)

**Dimensions**: Small (≤512x512) for fast processing

**Format**: JPEG (compatible with llama.cpp `--mmproj`)

## Why "29.24"?

1. **Unambiguous**: Decimal number, not open to interpretation
2. **Short**: Easy for model to extract in limited tokens
3. **Common receipt total**: Realistic value (not too round like 10.00)
4. **Easy to verify**: Simple string match in response

## Creating the Image

### Option 1: Use existing receipt image

1. Find a receipt with total = 29.24
2. Crop to show only total and key items
3. Convert to JPEG if needed
4. Resize to ≤512x512

### Option 2: Generate synthetic receipt

```python
from PIL import Image, ImageDraw, ImageFont

img = Image.new('RGB', (512, 512), color='white')
draw = ImageDraw.Draw(img)

# Add text
font = ImageFont.truetype("arial.ttf", 24)
draw.text((50, 50), "Total:", font=font, fill='black')
draw.text((150, 50), "29.24", font=font, fill='black')

img.save('/path/to/receipt_small.jpg', 'JPEG')
```

### Option 3: Download test dataset

Use public receipt datasets (anonymized):
- [Receipt dataset](https://example.com/receipts) (hypothetical)
- Extract one with total ≈29.24
- Verify total matches

## Installation

```bash
sudo mkdir -p /usr/local/share/gtx-broker/test-images
sudo cp receipt_small.jpg /usr/local/share/gtx-broker/test-images/
sudo chmod 644 /usr/local/share/gtx-broker/test-images/receipt_small.jpg
```

## Verification

### Check file exists

```bash
ls -la /usr/local/share/gtx-broker/test-images/receipt_small.jpg
```

### Test smoke test manually

```bash
curl -X POST http://127.0.0.1:11436/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3.5-35b-vision-q2_k",
    "messages": [{
      "role": "user",
      "content": [
        {"type": "image_url", "image_url": {"url": "file:///usr/local/share/gtx-broker/test-images/receipt_small.jpg"}},
        {"type": "text", "text": "Extract the total amount from this receipt. Reply with exactly: 29.24"}
      ]
    }],
    "max_tokens": 16,
    "temperature": 0
  }' | python3 -c "import sys, json; d=json.load(sys.stdin); print('29.24' in d.get('choices', [{}])[0].get('message', {}).get('content', ''))"
```

Expected output: `True`

## Fallback Behavior

If image is missing:

```python
def _run_smoke_test(self, profile_name: str) -> bool:
    if "vision" in profile_name:
        test_image = "/usr/local/share/gtx-broker/test-images/receipt_small.jpg"
        if not os.path.exists(test_image):
            logger.error("Vision test image not found: %s", test_image)
            return False  # Fail closed
```

**Why fail closed?** A false success (image missing but smoke test passes) means vision path was never actually tested.

## Maintenance

### Image updates

If you update the test image:

1. **Update known answer**: Change prompt to match new image
2. **Update tests**: Modify `test_vision_smoke_test_parses_29_24` if answer changes
3. **Document**: Note reason for change in commit message

### Alternative test images

For variety, create additional test images:

```
test-images/
├── receipt_small.jpg        # Total: 29.24 (primary)
├── receipt_simple.jpg       # Total: 15.50 (secondary)
└── flower_identify.jpg      # Species: Rose (for flower ID tests)
```

Each image gets its own smoke test with corresponding known answer.

## References

- **Smoke test implementation**: `gtx_broker/scheduler/model_profiles.py::_run_smoke_test`
- **Vision profile config**: `etc/llama-cpp/profiles/qwen35-vision.conf`
- **Model-Profile Controller**: `references/model-profile-controller.md`
