-- 005_job_type.sql —— 岗位类型 + 匹配备注
ALTER TABLE job ADD COLUMN job_type TEXT NOT NULL DEFAULT 'unknown'
    CHECK (job_type IN ('campus', 'social', 'intern', 'unknown'));

ALTER TABLE match_result ADD COLUMN notes_json TEXT NOT NULL DEFAULT '[]';
