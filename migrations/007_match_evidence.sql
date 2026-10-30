-- 007_match_evidence.sql —— 语义匹配证据
-- RAG 检索出的「JD 要求 ↔ 简历片段」证据对，存进匹配结果，供界面展示。

ALTER TABLE match_result ADD COLUMN evidence_json TEXT NOT NULL DEFAULT '[]';
