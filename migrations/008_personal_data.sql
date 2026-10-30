-- 008_personal_data.sql —— 个人数据库
-- 一个可扩展的「字段 → 值」库。从简历自动填充一部分，投递填表时
-- 遇到库里没有的字段就询问用户、填写后回写，逐步完善。

CREATE TABLE personal_data (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT 'manual',  -- resume | manual | apply
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
