-- Migration 002: persist the worker selected for a review task.

ALTER TABLE tasks ADD COLUMN review_worker TEXT;
ALTER TABLE batch_epochs ADD COLUMN review_result TEXT;
