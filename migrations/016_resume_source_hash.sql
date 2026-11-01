-- 016_resume_source_hash.sql —— 记住导入的文件指纹
--
-- 以前导入是「同一 kind 只保留一份、原地覆盖」，导入第二份简历会把第一份冲掉。
-- 现在改成：不同文件 = 不同简历，只有「同一份文件重复导入」才复用原来那条。
-- 用 PDF 内容的 sha256 判断「同一份文件」。

ALTER TABLE resume ADD COLUMN source_hash TEXT;

CREATE INDEX IF NOT EXISTS idx_resume_source_hash ON resume(source_hash);
