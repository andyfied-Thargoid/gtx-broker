# Review routing policy

Repository review is Codex-primary. Air Review is a failover reviewer only
when the Codex reviewer is unavailable; a substantive Codex rejection is not
silently replaced by Air Review.

## Boundaries

- Coding review gates call Codex first.
- If the Codex review command is unavailable, times out, exits unsuccessfully,
  or cannot be started, the adapter attempts Air Review once.
- The result records both the Codex failure evidence and the Air Review
  outcome, including the reviewer identity.
- Generic scheduler review tasks default to `codex-review` and may select
  `air-review` only as availability failover.
- Neither reviewer may modify the candidate worktree; a worktree mutation is
  a failed review, not a failover trigger.
- Final Codex verification remains a separate gate for coding tasks.

## Deployment boundary

This policy is implemented on the isolated branch
`feat/codex-review-primary-air-failover`. It is not deployed to the live
Broker service. Before activation, configure and qualify the Codex review
command, then verify the Air Review command as the fallback. Existing image
queue state is unrelated and remains unchanged.
