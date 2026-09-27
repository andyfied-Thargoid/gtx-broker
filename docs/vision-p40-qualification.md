# P40 vision qualification

Date: 2026-09-27

The first live integration check used the P40 vision profile with:

- Qwen3.5 35B A3B Q2 model from `/mnt/scratch/models/Qwen3.5-35B-A3B-UD-Q2_K_XL.gguf`;
- the BF16 projector from `/mnt/scratch/models/qwen35-vision/Qwen3.5-35B-mmproj-BF16.gguf`;
- llama.cpp on `127.0.0.1:11436`, with reasoning disabled for structured output;
- the receipt and image files staged under `/mnt/scratch/gtx-images/incoming/`.

The temporary vision profile was stopped after testing and the normal P40 coding
service was restored. The GTX broker on port 11438 remained healthy throughout.

| Input | Result | Wall time | Observation |
| --- | --- | ---: | --- |
| Asda receipt | success | 12.56 s | Correct merchant/date/currency/total, but duplicate product and promotion lines require review/deduplication. |
| Yoghurt bottle | success | 5.13 s | Returned an empty receipt payload with no invented totals. |
| Flowers, bed | failed validation | 5.11 s | Correctly rejected as non-receipt input. |
| Flowers, home | failed validation | 5.11 s | Correctly rejected as non-receipt input. |

This is not acceptance for unattended financial extraction. Receipt results need
human review until duplicate-line handling and confidence/quality checks are
implemented. The broker now has an explicit `image_description` schema for
non-receipt work, but it remains opt-in and still requires workflow-specific
review before unattended use.

## Remaining qualification checks

- connect the Telegram/Hermes caller to the explicit schema selection;
- connect Hermes authorization and user-facing acknowledgement to the broker's
  human-review approval path;
- verify scheduler persistence of a successful structured result and retry state
  for an unavailable worker;
- measure model load, unload, and restore times during the 00:00–06:00 window;
- run a repeated receipt set and compare duplicate-line rate, total accuracy, and
  latency before enabling unattended processing;
- test restart and retry behavior without interrupting the always-loaded GTX
  broker;
- record the exact model/projector profile whenever a benchmark is run.

The deterministic handler tests now cover unsupported file content, symlinks,
oversized images, malformed/fenced/non-object responses, receipt validation, and
the opt-in general image-description schema. Those tests do not start or query
the P40.
