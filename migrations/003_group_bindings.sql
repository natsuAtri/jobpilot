-- 003_group_bindings.sql —— 绑定从「按任务」收敛成「按档位」
-- 设计变更：用户不需要知道简历解析、JD 解析各绑哪个模型，
-- 只暴露「强模型 / 便宜模型」两档。
-- 迁移时保留用户已有的选择，从原任务绑定里挑一条继承过去。

INSERT INTO llm_binding(task, provider_id, model, params_json)
SELECT 'strong', provider_id, model, params_json
FROM llm_binding
WHERE task IN ('resume_parse', 'vision_ocr', 'resume_rewrite')
  AND NOT EXISTS (SELECT 1 FROM llm_binding WHERE task = 'strong')
ORDER BY CASE task WHEN 'resume_parse' THEN 0 WHEN 'vision_ocr' THEN 1 ELSE 2 END
LIMIT 1;

INSERT INTO llm_binding(task, provider_id, model, params_json)
SELECT 'cheap', provider_id, model, params_json
FROM llm_binding
WHERE task IN ('jd_parse', 'match_score', 'dialogue')
  AND NOT EXISTS (SELECT 1 FROM llm_binding WHERE task = 'cheap')
ORDER BY CASE task WHEN 'jd_parse' THEN 0 WHEN 'match_score' THEN 1 ELSE 2 END
LIMIT 1;

DELETE FROM llm_binding
WHERE task IN ('resume_parse', 'vision_ocr', 'resume_rewrite',
               'jd_parse', 'match_score', 'dialogue');
