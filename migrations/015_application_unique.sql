-- 015_application_unique.sql —— 一个岗位最多一条投递记录
--
-- 之前的写入是「先查再插」，并发下同一次投递会插两行，统计全错。
-- 先清理历史重复（保留最早那条），再加唯一约束，之后写入走 ON CONFLICT。

DELETE FROM application
WHERE id NOT IN (
    SELECT MIN(id) FROM application GROUP BY job_id
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_application_job ON application(job_id);
