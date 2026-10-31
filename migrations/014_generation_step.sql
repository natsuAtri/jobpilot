-- 014_generation_step.sql —— 生成任务的步骤检查点
-- 每完成一步（策略/起草/评审/建议）就落库一份，失败重跑时直接复用，
-- 不用把前面的 LLM 调用再烧一遍。

CREATE TABLE IF NOT EXISTS generation_step (
    run_id       INTEGER NOT NULL,
    step         TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (run_id, step)
);
