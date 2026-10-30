-- 011_generation_suggestions.sql —— 补写建议
-- 存「针对这个岗位，简历该补什么」的可执行建议。

ALTER TABLE generation_run ADD COLUMN suggestions_json TEXT NOT NULL DEFAULT '[]';
