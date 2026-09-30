# Fix Misleading Image-Submission Reply

## Issue
When an image is sent to the broker, the immediate reply gives incorrect or misleading information rather than accurately reporting that the image has been accepted for later identification by the P40 image worker.

## Problem Analysis
Current flow in `telegram_chat_bot.py` (lines 2117-2136):
- `route_to_vision_if_available()` is a stub that doesn't actually queue images
- Returns `None` and logs "Vision routing requested but issue #40 is not implemented"
- User gets no feedback about their image being queued

Expected behavior:
1. After successful submission, reply with: "Image received and queued for identification [job_id: xxx]"
2. If queueing fails, return explicit failure message
3. Deliver actual identification result separately when P40 worker finishes

## Implementation Plan

### Phase 1: Update attachment_pipeline.py
- Modify `route_to_broker()` to actually submit the job to the gtx-broker HTTP API
- Return job_id and status on success/failure
- Add proper error handling

### Phase 2: Update telegram_chat_bot.py
- Modify `route_to_vision_if_available()` to call the updated broker routing
- Send immediate "queued" reply with job_id when submission succeeds
- Return `None` only on actual failure (not on success while awaiting processing)

### Phase 3: Add result delivery mechanism
- Create polling endpoint or webhook for result retrieval
- Update bot to periodically check for completed job results
- Send final identification result when available

## Files to Change
1. `~/src/telegram-chat-bot/attachment_pipeline.py` - Add broker API integration
2. `~/src/telegram-chat-bot/telegram_chat_bot.py` - Fix reply logic
3. Create test cases for queued status reporting

## Acceptance Criteria
- [ ] Image submission returns "queued" status with job_id
- [ ] Failed queueing returns explicit error message
- [ ] Actual result delivered separately after processing
- [ ] Tests cover success, queue failure, delayed completion, failed identification
