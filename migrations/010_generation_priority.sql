-- 010_generation_priority.sql —— 改写优先级
-- 让终审页能按「有价值 / 仅措辞 / 被拦截」排序展示。

ALTER TABLE generation_item ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal';
