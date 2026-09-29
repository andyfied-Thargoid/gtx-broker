-- Migration 003: Worker model_profile naming update
-- Updates old model_profile names to canonical names

UPDATE workers
SET model_profile = 'qwen35-coding'
WHERE profile = 'p40-coding'
AND model_profile IN ('p40-coding', 'p40-qwen35-coding', 'qwen35-coding-old');

UPDATE workers
SET model_profile = 'qwen35-vision'
WHERE profile = 'p40-vision'
AND model_profile IN ('p40-vision-qwen35', 'p40-vision', 'qwen35-vision-old');
