# 改动日志

> **维护约定**：每完成一轮改动就追加一节，写清「改了什么 / 涉及文件 / 怎么验证」。
> 这份文件是项目的单一改动记录，和 `README.md`（用法）、`docs/design.md`（设计）、
> `docs/known-issues.md`（已知问题）互为补充。

---

## 2026-10-30 · 第 1 轮：基础骨架与核心链路

**改动**
- FastAPI + sqlite3 + Jinja2 骨架，`data/` 集中存数据
- 多厂商 LLM 接入，按「强/便宜」两档绑定任务
- 简历 PDF 解析（pypdfium2 字符级坐标 + 规则切分）
- 渲染导出：设计版 / ATS 版双出口，Chromium print + 回读校验
- 解析复核：疑点驱动 + 对话 patch 确认
- 岗位导入与匹配评分（学历/技能/年限/语言）

**涉及**
`app/main.py`、`app/db.py`、`migrations/001_init.sql`、`app/parse/*`、`app/render/*`、`app/dialogue/*`、`app/match/*`

**验证**：`tools/smoke_m1.py`、`smoke_m2.py`、`smoke_match.py`

---

## 第 2 轮：8 个问题集中修复

**改动**
1. 教育经历拆出「学校 + 专业」：`rules.py::_split_school_major`，专业存 `meta["专业"]`
2. 个人库去掉「身份证号」
3. 个人库去掉「期望薪资」
4. 简历只存一份：`create_resume` 同 kind 原地覆盖，不再产生「（2）（3）」
5. JD 分块剔除「团队介绍/工作职责」：`chunk.py::requirement_text`
6. 搜索分 = 详情分：`runner._score_job` 改用完整 `description`
7. 语义证据加阈值：`semantic.py` `RERANK_THRESHOLD`
8. 教育解析区分学校与专业（同 1）

**涉及**
`app/parse/rules.py`、`app/repo/personal.py`、`app/repo/resumes.py`、`app/rag/chunk.py`、`app/match/scoring.py`、`app/match/semantic.py`、`app/search/runner.py`

**验证**：`smoke_m1/m2/match/rag` 全绿；个人库显示「某某大学 / 软件工程 / 本科」

---

## 第 3 轮：3 个问题（语义标记、评分不一致、生成崩溃）

**改动**
- 「语义匹配不准确」记入 `docs/known-issues.md`，暂缓
- 搜索/详情评分一致性复验（端到端差 0.00）
- **生成定制简历 500**：`tasks.py` 补 `resume_draft` / `resume_critique` 两个任务定义

**涉及**：`app/llm/tasks.py`、`docs/known-issues.md`

---

## 第 4 轮：导入岗位崩溃

**改动**
- `create_job` 幂等：同 `dedupe_key` 已存在则复用，不再触发 `UNIQUE constraint failed`
- 导入路由统计「已导入 / 已存在跳过」

**涉及**：`app/repo/jobs.py`、`app/routes/jobs.py`、`tools/smoke_search_personal.py`

---

## 第 5 轮：4 个体验问题

**改动**
1. 无意义改写：`engine._substance` 归一化，只改标点不算改动
2. 投递引导：`applications.html` 四步说明
3. 「岗位职责」通用过滤：`chunk.py` 支持 `【】` / `一、` / markdown 标题
4. 全局等待反馈：`base.html` 加全屏遮罩 + 表单提交拦截

**涉及**：`app/generate/engine.py`、`app/generate/prompt.py`、`app/rag/chunk.py`、`templates/base.html`、`static/app.css`

---

## 第 6 轮：双 agent 思考过程可视化

**改动**
- 生成改后台线程 + 前端轮询，实时时间线：抽取事实 → 起草 → 评审 → 修订 → 校验

**涉及**：`app/generate/progress.py`、`app/generate/engine.py`、`app/routes/generate.py`、`templates/generate_progress.html`

---

## 第 7 轮：过程配色与按钮布局

**改动**
- 三套配色方案（终端/纸感/玻璃）→ 用户选「纸感」
- 修按钮错位：`.gen-actions` 改纵向两行布局

**涉及**：`static/app.css`、`templates/generate_review.html`

---

## 第 8 轮：对话式 agent（核心形态转变）

**改动**
- 删除一键 `/agent` 页，保留 303 重定向
- 新增 `/chat`：会话持久化（`conversation` / `conversation_message`）
- 规则意图：搜索 / 再找 / 排除 / 定制 / 投递 / 问详情 / 判断 / 查询 / 闲聊
- 新增「适合我吗」LLM 判断：`app/agent/judge.py`
- 指代追踪：显式序号记入 state，供「这个/它」引用
- 修 Playwright Sync API 不能在 asyncio 里跑 → `asyncio.to_thread`

**涉及**：`app/agent/*`、`app/routes/chat.py`、`templates/chat.html`、`migrations/009_conversation.sql`

---

## 第 9 轮：模型配置迁移到 .env

**改动**
- `config.load_dotenv()` 读根目录 `.env`
- `secrets.get_secret` 环境变量优先；`registry.resolve` 改为只看 .env
- 删除设置页（`/settings/llm`）与 `llm_provider/llm_binding` 使用
- `.env.example` + `.gitignore`

**涉及**：`app/config.py`、`app/llm/secrets.py`、`app/llm/registry.py`、`.env.example`、`README.md`

---

## 第 10 轮：对话体验重做

**改动**
- 发送改 fetch，不整页刷新；「思考中」三点动画
- 候选卡片：查看详情（展开 JD）/ 定制 / 投递
- 「对话」改名「Agent」，首页 `/` 直达；导航收敛为 Agent + 管理下拉
- 回复内容结构化渲染（小标题加粗、列表、链接）

**涉及**：`app/routes/chat.py`、`templates/chat.html`、`templates/base.html`、`static/app.css`

---

## 第 11 轮：删简历崩溃 + 全局兜底

**改动**
- 删简历崩溃根因：`application.resume_id` 是 `ON DELETE RESTRICT`
- `delete_resume` 先删投递记录再删简历，页面提示「连同 N 条投递记录」
- 全局异常处理器：未捕获错误渲染可读页面，不再裸 500
- 管理下拉方向修正（向左 → 向右）

**涉及**：`app/repo/resumes.py`、`app/main.py`、`static/app.css`

---

## 第 12 轮：双 agent 结构性优化

**改动**
1. 策略层 `app/generate/strategy.py`：先决定改哪几条、为什么
2. 证据驱动：语义证据 + 匹配差距喂给策略与起草
3. 评审加「收益判断」：`ok/edit/noop` + `value`
4. 评审加「漏改」：JD 要但简历没体现
5. 终审按优先级排序（高价值 / 仅措辞）
- 修：修订轮会把合格改写覆盖成违规的 → 修订不覆盖有效改写；默认关闭自动修订
- 修：LLM 爱加从句导致长度超限 → 白名单标字数上限 + prompt 反例 + 守卫给短条目留余量

**涉及**：`app/generate/strategy.py`、`prompt.py`、`engine.py`、`facts.py`、`guard.py`、`migrations/010_generation_priority.sql`

---

## 第 13 轮：建议补写

**改动**
- 新增 `app/generate/suggest.py`：针对 JD 给出「该补什么、为什么、怎么写」
- 存储：`generation_run.suggestions_json`
- 终审页「建议补写」卡片，「写进简历」弹窗里直接写内容后落库

**涉及**：`app/generate/suggest.py`、`app/routes/generate.py`、`app/repo/resumes.py::add_suggested_item`、`templates/generate_review.html`、`migrations/011_generation_suggestions.sql`

---

## 第 14 轮：优化清单 7 项 + 全局暖纸主题

**改动**
- 搜索分页：点 `.btn-next` 翻页；「再找几个」自动翻下一页
- 意图 LLM 兜底：规则认不出时调 `intent_route`
- 个人库补全简历：「从个人库补全」按钮
- 进度落库：`task_run` 表，重启不丢；启动时标记中断
- 死代码清理：`index.html`、`/style-preview`、6 个未用 task
- 模型预加载：启动后台预热 bge + rerank
- 全局暖纸主题：`static/theme.css` 覆盖层（底色 #f5f1ea / 卡片 #fffdf8 / 强调 #7a5c3e）

**涉及**：`app/search/fiftyone.py`、`app/agent/intent.py`、`app/repo/personal.py`、`app/repo/task_runs.py`、`app/main.py`、`static/theme.css`、`migrations/012_task_run.sql`

---

## 第 15 轮：打分重构（项目对口优先）

**改动**
- 新增「项目对口度」维度：JD 每条要求能否被简历项目接住（语义匹配）
- 权重重排：项目对口 0.45 / 年限 0.25 / 方向 0.15 / 技能 0.10 / 语言 0.05
- 新增「方向一致性」维度（简历方向 vs 岗位方向）
- reranker 换 **bge-reranker-v2-m3**（2.2GB），分数为概率尺度，阈值 0.05

**效果**：AI应用 100→69、设备工程 100→58、财务 0→12；MySQL 与 OCR 不再错配

**涉及**：`app/match/semantic.py`、`app/match/scoring.py`、`app/search/runner.py`、`app/rag/rerank.py`

---

## 第 16 轮：6 个体验问题

**改动**
1. 文件选择按钮：自定义样式（虚线框 + 文件名回显）
2. 导入后引导：解析复核页提示横幅 + 「去 Agent」按钮
3. 默认 70 分门槛去掉：未指定就按分排序不过滤
4. 「定制」解释：帮助文案 + 开场建议胶囊；修「定制是什么意思」被当动作
5. 建议补写空态提示
6. LLM 间歇失败：网络/限流重试 2 次、JSON 解析重试 1 次；失败回复带原因

**涉及**：`templates/resumes_list.html`、`templates/dialogue.html`、`app/routes/resumes.py`、`app/agent/goal.py`、`app/agent/chat.py`、`app/agent/intent.py`、`app/llm/client.py`

---

## 验证基准

所有轮次改动后均跑同一套回归（9 个脚本）：

```bash
python tools/smoke_m1.py samples/简历.pdf
python tools/smoke_m2.py <resume_id>
python tools/smoke_match.py <resume_id>
python tools/smoke_rag.py <resume_id>
python tools/smoke_apply.py
python tools/smoke_search_personal.py
python tools/smoke_dialogue.py <resume_id>
python tools/smoke_scratch.py
python tools/smoke_generate.py
```

当前基线：**138 项断言全绿**。

---

## 第 17 轮：对话页反馈与候选缩略

**改动**
1. 搜索/定制/判断时，思考指示带文案：`正在搜索岗位并匹配打分…（约 10–30 秒）` 等，按输入内容自动切换
2. 候选面板从对话区外移进对话滚动区，并改成**缩略列表**：默认显示 5 条，可「展开全部 N 个 / 收起」
3. 修终审页 Jinja 语法错误（`generate_review.html` 多了一个 `{% endif %}`，导致 `TemplateSyntaxError`）

**涉及**：`templates/chat.html`、`static/app.css`、`templates/generate_review.html`

**验证**：模板全部可编译；`/chat`、`/jobs/{id}/generate` 均 200；回归 138 项全绿。
---

## 第 18 轮：候选面板重做 + 终审实时预览 + 优化建议补跑

**改动**
1. 候选面板重做：卡片容器 + 序号色块 + 悬停浮起操作按钮 + 胶囊式「展开全部」；默认 5 条
2. 思考指示文字竖排修复：flex 最小宽度导致，加 `white-space: nowrap`
3. 思考指示插到候选面板之前，不再跑到列表下方
4. **终审页双栏**：左侧 iframe 实时预览（设计版 / ATS 版切换），右侧终审操作
5. 新增 `GET /jobs/{id}/generate/preview`：按当前采用/手动修改内容渲染简历
6. 优化建议区重命名「优化建议 · 该补什么」，并新增「重新生成建议」按钮 + `POST /suggest/regen`

**涉及**：`templates/chat.html`、`templates/generate_review.html`、`static/app.css`、`app/routes/generate.py`

**验证**：`/chat`、`/jobs/1/generate`、两种 variant 预览均 200；回归 138 项全绿。
---

## 第 19 轮：简历建议意图 + 编辑页实时预览

**改动**
1. 新增 `advice` 意图：「我的简历需要补充什么 / 还能怎么优化」→ 走简历整体建议（不绑定岗位），不再误答岗位队列
   - 规则识别 + LLM 兜底都加了这个意图
   - 新增 `resume_review` 任务与 `suggest.general_review()`
2. **简历编辑页左侧**加「实时预览 / 原稿」切换 + 「设计版 / ATS」切换，预览用 HTML 渲染并自动缩放到栏宽
   - 新增 `GET /resumes/{id}/preview?variant=`
3. 「重新生成建议」按钮（`POST /suggest/regen`）验证可用，能补跑失败的建议
4. 候选面板确认采用 A 方案（紧凑行式列表）

**涉及**：`app/agent/intent.py`、`app/agent/chat.py`、`app/generate/suggest.py`、`app/routes/resumes.py`、`app/routes/generate.py`、`templates/resume_edit.html`、`templates/generate_review.html`、`static/app.css`

**验证**：「我的简历需要补充什么」→ intent=advice，返回 4 条简历级建议；编辑页预览 200 且自动缩放；回归 138 项全绿。
---

## 第 20 轮：移除终审页预览，只保留简历编辑页预览

**改动**
- 终审页（生成定制简历）恢复单栏布局，删掉左侧预览、相关 JS 与 `.review-shell` 样式
- 删除 `GET /jobs/{id}/generate/preview` 路由及 `generate.py` 里不再使用的导入
- 简历编辑页的实时预览保留（`GET /resumes/{id}/preview`）

**涉及**：`templates/generate_review.html`、`app/routes/generate.py`、`static/app.css`

**验证**：终审页 200 且无预览元素；旧预览路由 404；编辑页预览 200；回归 138 项全绿。
---

## 第 21 轮：解析鲁棒性（规则 + LLM 精修）+ 聊天空白气泡

**问题**：导入一份机械专业简历，解析完全错乱（姓名识别成「⚫ 民族：汉」、只切出 3 个章节、要点粘连）。

**根因**
1. 姓名规则取「前 5 行里 2–6 字、无数字」→ 命中「⚫ 民族：汉」；真实姓名与「个人简历」同行被跳过
2. 章节标题带装饰点（`教育背景 . . .`）导致匹配失败；`竞赛经历`/`英语能力及技能` 不在词典
3. 项目符号 `➢` 不在 bullet 词典
4. 全局左边界会把「整体缩进」的章节全判成 bullet
5. 字间距被误判成空格（`2021 .09`、`1 00`）

**改动（A 规则层）**
- 姓名改为「按 segment 取字号最大的 2–4 个汉字」，排除「个人简历」等标题词
- 章节词典补：竞赛经历 / 英语能力及技能 / 语言能力 / 技能证书 / 获奖经历 / 科研经历 等
- 标题分类前剔除装饰性标点（`.` `·` `|`）
- bullet 补 `➢ ➤ ▸ ► ⚫ ○ ▪ ▫ ★ ✓`
- 每个章节单独计算左边界（避免整体缩进被误判）
- 新增 `_tighten()`：收紧数字/日期/中文之间的伪空格；`_strip_gpa()` 去掉 GPA 尾巴
- 续行合并：支持 bullet 后的缩进续行，同时保留「标题+右对齐日期」不合并的保护

**改动（B LLM 精修）**
- 新增 `app/parse/refine.py`：把「带坐标的视觉行 + 规则骨架」交给模型重排，输出结构化 JSON 并按 schema 校验；失败自动退回规则结果
- 导入时若已配置模型则始终精修（约 5–10 秒），任务 `resume_parse` 注册为强模型档

**同时修**：Agent 页「空白气泡 + 没有提示词」
- 空候选面板仍渲染边框 → `.cand-panel:empty { display: none }`
- 开场白判断用 `children.length`，但候选面板现在也在容器里 → 改为判断 `.msg` 是否存在

**效果**（同一份简历）
| | 修复前 | 修复后 |
|---|---|---|
| 姓名 | ⚫ 民族：汉 | 张三 |
| 章节 | 3 个 | 教育/竞赛/项目/实习/自我评价/技能 6 个 |
| 学历 | 缺失 | 某海外高校 · 硕士（在读）· 机械工程 · GPA --/100 |
| 邮箱 | 无 | zhangsan@example.com |

**验证**：回归 138 项全绿（m1 20 / m2 15 / match 28 / rag 7 / apply 13 / search 19 / dialogue 13 / scratch 10 / generate 13）。
---

## 第 22 轮：清理测试残留 + 修复多页简历导出

**问题**：张三的简历里看到「李四」的名字。

**排查结论**：数据库里搜不到任何「李四」（profile / personal_data / resume 全部是张三）。
实际是 `data/export/` 里残留了 4 个 **我跑回归测试时 smoke_m2 生成的李四 PDF**，
用户打开旧文件看到的就是它。

**改动**
1. 删除 `data/export/` 下所有测试产物、`data/uploads/` 下两个孤儿上传文件
2. `smoke_m2.py` 的产物改写到 `data/tmp/m2/`，不再污染用户下载目录

**顺带发现并修复的真实 bug**：张三的简历内容超过一页，导出被「单页设计」校验拦下，
返回 500「导出失败」——这大概也是用户去看旧文件的原因。
- `verify.py` 改为**允许最多 2 页**（>2 页才拦，并在消息里提示精简）
- 2 页时在备注里说明「内容较多，输出 2 页」

**验证**：导出 resume 1 → 200 / 2 页 / 首行「张三」；smoke_m2 全部通过；列表页与编辑页均已无「李四」。
---

## 第 23 轮：技术报告

**新增** `docs/technical-report.md`：面向开源读者的精简版技术报告（约 8 页），含 3 张截图。
- 覆盖：架构、解析两段式、匹配权重、双 agent、对话意图、工程实践、实测数据、已知限制、设计取舍
- 截图存放 `docs/images/`，截图用的临时岗位与生成运行已删除
- README 顶部增加报告入口

**验证**：图片相对路径正确；演示数据已清理（剩余岗位 1 个、简历 2 份均为用户数据）。
---

## 第 24 轮：开源准备（清理个人数据 + 仓库化）

**安全问题（已修复）**
- `.env.example` 里硬编码了一个真实 API Key → 改为占位符
- 建议：用过这个 key 的话去服务商后台**吊销重建**

**清理的个人数据**
- 数据库、上传的简历 PDF、导出件、浏览器登录态、日志 → 全部删除（`data/` 只留 `.gitkeep`）
- 根目录的简历 PDF、`samples/` 下的简历样本与渲染件、`docs/shots/` 12 张旧截图 → 删除
- `docs/parse_dump.txt`、`docs/rules_dump.json`、`tools/resume_text.txt`（解析调试产物）→ 删除
- 以上文件已整体备份到项目外：`Documents/jobagent-personal-backup-<时间戳>/`

**文本脱敏**
- 文档与代码注释里的真实姓名/电话/邮箱/学校 → 换成「张三」「13800000000」「example.com」「某某大学」
- 全文扫描确认无残留

**截图重拍**
- 用合成简历（张三，无真实信息）重新截取 Agent 页、终审页、编辑页，旧截图已覆盖

**仓库化**
- `.gitignore` 重写：忽略 `data/*`（保留 `.gitkeep`）、`models/`、`.venvlib/`、`.env`、`*.pdf`、`samples/*`、pip 临时目录
- 新增 `LICENSE`（MIT）
- `requirements.txt` 拆成核心 + `requirements-rag.txt`（语义检索可选，不装会自动降级）
- `README.md` 重写：截图、快速开始、技术要点、项目结构、验证、平台限制、隐私说明、文档索引
- 用临时 git 仓库做过 `git add -A` 验证：**141 个文件**，无 `.env` / 数据库 / PDF / 模型权重

**验证**：空数据库启动正常（12 个迁移全跑通），`/chat` `/resumes` `/jobs` `/personal` 均 200。
---

## 第 25 轮：定名 JobPilot + 迁到 D 盘

**改名**
- 项目名定为 **JobPilot**（求职副驾）
- 全量替换显示名：`求职 Agent` / `求职 agent` → `JobPilot`
  - 覆盖 `templates/*.html`（标题、导航品牌）、`app/main.py`（FastAPI title、错误页）、`run.py`、README、两份文档
  - 品牌样式保留两色：`Job` + `<em>Pilot</em>`
- `LICENSE` 版权方改为 `JobPilot contributors`

**迁移**
- 项目从 `C:\Users\Lenovo\Documents\JobPilot` 迁到 **`D:\workspace\JobPilot`**（C 盘紧张）
- 迁移释放 C 盘 3.4 GB（2.7 → 5.9 GB 可用）
- 迁移后验证：`BASE_DIR` / `DB_PATH` 正确指向 D 盘，`/chat` 200，品牌名已生效

**截图**
- 品牌改名后重拍 3 张文档截图（仍使用合成简历张三）

**注意**：旧工作目录 `C:\Users\Lenovo\Documents\deepseek-harness\default-workspace` 已清空，
以后在该目录下打开的会话需要改到 `D:\workspace\JobPilot`。
---

## 第 26 轮：修复语义依赖版本冲突 + 降级不再静默

**问题**
1. 岗位匹配报 `AttributeError: 'BertConfig' object has no attribute 'sub_configs'`
2. 搜索结果第一个岗位出现 100 分

**根因（两者同源）**

`sentence-transformers 6.x` 要求 `transformers >= 5.0`，而用户的启动环境（Anaconda）
里是 `transformers 4.41`、且没有 `sentence-transformers`。`run.py` 在缺依赖时把
`.venvlib` **追加**到 `sys.path`，于是 transformers 用了 Anaconda 的 4.41、
sentence-transformers 用了 `.venvlib` 的 6.2 → 版本冲突。

模型不可用后，`project_relevance` / `direction_similarity` 返回 None，
这两个维度被跳过，权重重新归一化到「关键词+年限+语言」——三项满分就是 **100 分**。

**改动**
1. `semantic.py`：三个入口统一包 try/except，模型挂了返回空结果而不是抛异常；
   新增 `degraded()` 标记，并只打印一次告警（含修法），避免静默降级
2. `routes/jobs.py`：匹配路由再兜一层；语义降级时给 `MatchResult.notes` 追加提示
   「本次只用三项打分，分数会偏高」，详情页用现有 banner 展示
3. `run.py`：检测「当前环境已有 transformers + 又叠加 .venvlib」才警告（纯 .venvlib
   运行不再误报），并指向 `tools/check_env.py`
4. 新增 `tools/check_env.py`：打印依赖版本/来源、校验版本兼容、实际跑一次
   embedding 与 rerank，给出可执行的修法
5. `requirements-rag.txt`：锁定 `transformers>=5.0,<6` + `sentence-transformers>=6.0,<7`，
   并注明这个坑
6. README 增加故障排查小节

**验证**
- Anaconda 环境：`check_env.py` 明确报出版本冲突；`semantic_evidence` 返回 `[]`、
  `degraded()` 为 True（不再 500）
- DSH runtime 环境：`degraded()` 为 False，语义维度正常参与打分
- 回归 118 项全绿（m1 需要样本 PDF，本次跳过）
---

## 第 27 轮：意图修偏 + Agent 页双栏 + 管理精简

**1. 「我打算投第三个 帮我把简历优化一下」被识别成投递**
- 原因：tailor 的触发词只有「定制/改简历/生成简历」，不含「优化」，于是落到 apply
- 改动：tailor 增加 `优化 / 润色 / 打磨`，并放宽「简历 + 修改/调整」；
  由于 tailor 判断在 apply 之前，这句话现在正确识别为「定制第 3 个」
- 回归用例：优化第 3 个→tailor、我的简历需要优化吗→advice、投第 3 个→apply

**2. 候选列表挡住对话**
- 原结构：候选面板在对话滚动区内部，一多就把对话挤走
- 新结构：Agent 页改成两栏 —— 左侧对话、右侧独立「候选岗位」卡片（sticky，可独立滚动）
- 侧栏窄，候选行改成两行式：第一行 标题 + 分数，第二行 公司地点 + 操作
- 无候选时显示引导文案；窄屏（<1120px）自动上下堆叠

**3. 管理菜单去掉「岗位」「投递」**
- 找岗位、看分数、投递都走 Agent，管理里只保留「简历」「个人库」
- `GET /jobs` 改为 303 跳 `/chat`
- 岗位详情页与投递记录页保留（Agent 的链接会用到），只是不再暴露入口

**涉及**：`app/agent/intent.py`、`templates/chat.html`、`templates/base.html`、`static/app.css`、`app/routes/jobs.py`、`docs/images/agent-chat.png`

**验证**：意图分类用例全部正确；`/jobs` → 303；导航下拉只剩简历/个人库；Agent 页截图确认两栏布局。
---

## 第 28 轮：简历证件照（个人库上传 + 从 PDF 自动提取）

**新增**
- `app/parse/pdf.py::extract_photo()`：从 PDF 首页取像素面积最大的位图作为证件照；
  DCTDecode 直接复用原始 JPEG，其它格式转 JPEG；小于 4000 像素的忽略（图标/线条）
- `app/repo/personal.py`：`set_photo / photo_path / photo_data_uri / delete_photo`，
  文件存 `data/uploads/photo.jpg`，个人库用 `photo` 键记录
- `/personal` 新增「证件照」卡片：预览 + 上传（JPG/PNG/WebP，≤8MB）+ 删除
- 导入简历时若个人库还没有照片，自动从 PDF 提取一张
- 渲染链路支持照片：`build_context(..., photo=)` → `render_html(..., photo=)`；
  导出、预览、页面快照三处都带上（data URI，导出时不用再读文件）
- 简历模板的 `.photo` 位（25mm×35mm，虚线占位样式）现在会真正渲染；**ATS 版自动隐藏照片**

**验证**：从用户 PDF 提取到 600×786 证件照；个人库显示正常；设计版预览/导出含照片，
ATS 版不含；奖项章节在两种版本都正常。
---

## 第 29 轮：照片排版调优 + 个人库表格精简

- 简历头部：联系方式移进左侧信息块，与姓名贴在一起；照片 25×35mm → **19×26mm**，
  文本块与照片垂直居中，不再在联系方式下方留一大块空白
- 个人库：去掉「字段 / 值 / 状态」表头行
---

## 第 30 轮：从「脚本」到「agent」——可观测 + 工具循环 + 失败处理

**1. 可观测性（migration 013 `agent_trace`）**
- 每次用户输入生成一个 turn_id，记录 intent / tool / llm / error / verify / result
- 含延迟与 token；写库失败不影响主流程
- LLM 调用自动记录（task、model、tokens、耗时）

**2. 意图评估集 `tools/eval_intent.py`**
- 30 条真实说法，带期望意图与参数；门禁式退出码
- 用它抓到并修好：「定制 3」裸序号、「改下简历」措辞
- 当前 30/30

**3. 工具调用循环（`app/agent/tools.py` + `client.chat_with_tools`）**
- 规则命中 → 直连（0 次模型调用，行为不变）
- 规则未命中 / 复合请求（然后、顺便、多个动作词）→ 模型自己调工具，最多 3 步
- 7 个只读工具：搜索/翻页/排除/问详情/判断适配度/列候选/简历建议
- 实测真实模型自动串联 3 个工具（查 2 个详情 + 判断适配度）并给出排序建议
- 工具循环失败自动回退到规则动作

**4. 失败澄清（脚本和 agent 的关键差别）**
- 序号越界不再静默取第 1 个 → 明确告知只有 N 个
- 没有候选时投递/定制 → 引导先搜索
- 关键词为空 → 反问方向

**5. 搜索自动放宽**
- 城市无结果 → 放宽到全国；仍无 → 只保留关键词首词
- 放宽后**明确告诉用户放宽了什么**，不偷偷换条件

**6. 生成检查点（migration 014 `generation_step`）**
- 策略/起草/评审/建议每一步完成即落库
- 失败重跑时复用已完成步骤（实测：第二次只调评审+建议，省掉策略与起草两次调用）
- 终审页提示「上次中断，重新生成会从断点继续」

**7. 回复一致性校验（`app/agent/verify.py`）**
- 不做「让模型自评」这种花架子，只做确定性检查：
  回复里的「xx 分」必须在候选里、「第 N 个」不能越界
- 不一致只记 trace，不篡改回复

**测试**：新增 `tools/smoke_agent.py`（33 条断言），合计 **151 条断言全绿**，意图评估 100%。
---

## 第 31 轮：工程质量（ruff + mypy + CI）

**静态检查**
- 新增 `pyproject.toml`：ruff（line-length 120，E/F/I 规则）+ mypy（python 3.12，检查 `app/`）
- ruff 修掉 38 个问题（未使用导入、导入顺序、分号多语句等），当前 **0 问题**
- mypy 修掉 34 个类型问题，当前 **0 问题（83 个文件）**，其中几处是真实隐患：
  - `int(cur.lastrowid)` 可能为 None（12 处统一加 `or 0`）
  - `rerank.active_name()` 在模型未就绪时会 AttributeError
  - 表单值可能是 `UploadFile` 却直接 `.strip()` / `json.loads`
  - `locate_bullet` 变量复用导致类型混淆

**CI**
- 新增 `.github/workflows/ci.yml`：push/PR 自动跑 ruff + mypy +
  6 个不依赖模型与浏览器的测试（意图评估、agent、生成、投递、个人库、从零创建）
- `smoke_m2`（需本机 Chrome）与 `smoke_match`/`smoke_rag`（需 2.3GB 模型）不进 CI，README 已说明

**配套**
- 新增 `requirements-dev.txt`（ruff + mypy）
- README 加 CI 徽章、开发说明、以及「改意图规则要同步更新 GOLDEN 语料」的提醒
- 新增 `docs/roadmap.md`：把匹配分校准（A1 基准集 / A2 本地洞察 / A3 个人权重）
  与其他待办记录下来，并注明两项已否决的想法及原因
---

## 第 32 轮：路由反转 —— agent 优先

**问题**：三个 bug（「3 个」被忽略、多序号只取第一个、「第三个呢」变岗位介绍）根因相同——
规则先挑了大部分请求，agent 只处理剩下的。规则一旦参与理解，新说法就会持续翻车。

**改动**
- `handle` 改为：精确指令走快路径，**其余全部交给 agent**
  - 快路径只保留 6 类：投/定制第 N 个、再找几个、排除 X、现在有什么、第 N 个的要求
  - 快路径还要求：≤16 字、无「和/然后/顺便/之前」等连词
- agent 拿到「简历摘要 + 候选清单 + 上一轮动作 + 上一轮用户请求 + 最近 3 轮对话」
- 工具扩充到 9 个：新增 `tailor_resume` / `apply_job`——**只能提议，用户确认后才执行**
- `search_jobs` 支持 `count`（「找 3 个」）；`judge_fit` / `ask_job_detail` 支持 `indices` 数组
- 新增 `app/agent/plan.py`：从简历推导搜索关键词（「和我简历匹配」不再拿「我简历」去搜）

**修掉的真 bug**
- 工具循环里调 `_do_judge` 会嵌套 `asyncio.run`，在已运行的事件循环中直接抛
  RuntimeError，导致「判断服务报错」。工具改为在线程中执行。

**实测（真实模型）**
- 「找 3 个和我简历匹配的岗位」→ 模型自主搜了两次（AI 应用 / 视觉算法），
  回复引用简历里的技能说明为什么对口
- 「第二个和第三个是否适合我」→ 两个都判断
- 「第三个呢」→ 继承上下文，给出分析而不是岗位介绍
- 「你好」3.5s（agent），「现在有什么」0.1s（快路径）

**评测**
- 新增 `tools/eval_agent.py`：15 条对话，验证「模型选没选对工具」，当前 **15/15**
- 新增 14 条快路径/多序号/省略句断言，合计 **165 条断言全绿**
---

## 第 33 轮：修复「处理中」卡死 + 定制改为后台执行

**卡死根因（前端）**
- `base.html` 的全局表单处理注册在**捕获阶段**，先于聊天页自己的 `preventDefault()`
  执行：于是打开了整屏遮罩、禁用了按钮，而聊天页走 fetch 不跳转，遮罩永远没人关。
- 修复：全局处理改到冒泡阶段并检查 `e.defaultPrevented`；聊天输入框加 `data-busy="skip"`；
  `send()` 加 `finally` 兜底清除遮罩与按钮禁用。

**卡死根因（后端）**
- `_do_tailor` 原先同步跑 `engine.generate`，一次定制要 30–60 秒，HTTP 请求一直挂着。
- 改为复用后台任务机制：`generate._start()` 起线程，对话**立刻返回**并给进度链接。
- 实测：`定制1和2` 从 30 秒+ 降到 **0.17 秒**，两个后台任务各自完成。

**「定制1和2」支持**
- 快路径新增多序号模式：`定制 1 和 2` / `定制第 1、3 个`
- 一次可以起多个定制任务，每个都有独立的进度链接

**测试**
- 新增 6 条断言（快路径识别、立刻返回、每岗位一个链接、进队列）
- 合计 **171 条断言全绿**
---

## 第 34 轮：定制只确认一次 + 投递由 agent 记录

**1. 「帮我定制1和2的简历」要确认两次**
- 原因：agent 对两个岗位各调用一次 `tailor_resume`，每次返回一条确认
- 修复：`tailor_resume` 支持 `indices`，一次调用覆盖多个岗位，合并成一条确认
  - 「要针对第 1 个「A」、第 2 个「B」定制简历吗？一次确认即可。回复「定制1和2」…」
- 快路径本来就支持「定制1和2」，确认后直接并行起两个后台任务

**2. 投递后让用户去「投递页」点「已投递」**
- 问题：用户在浏览器里投完，还得回来看不见的页面点一下，数据才记上
- 修复：新增工具 `record_application`
  - 用户说「投好了」「已投递」时，agent 自己调工具写进投递表
  - 不指定序号时默认用最近一次打开的那个岗位（`last_apply_index`）
  - 已有记录则更新，不会重复插入
- `apply_job` 的提示改为：「投完回来说一句「投好了」，我直接帮你记上」

**测试**
- 新增 5 条断言（投递记录、去重、合并确认）+ 2 条对话评测用例
- 合计 **176 条断言全绿**，`eval_agent.py` **17/17**

---

## 第 35 轮：Agent 层加固（按《Agent 层改造任务书》执行）

**确定性 bug**
- T1 候选身份：位置 → 会话内递增 `cid`；追加候选只往末尾放，旧位置永不漂移；
  满 30 个明确提示；记录投递前必须有 `last_apply_cid`
- T2 原话透传：`_run_tool` 增加 `utterance`，「这个」「那个」不再静默落到第 1 个；
  问薪资不再倾倒整段 JD
- T3 「按你的简历」提示此前被 `note = ""` 覆盖，从未生效过
- T4 定制任务 in-flight 去重；`get_or_create_run` 捕获 `IntegrityError` 回查

**agent 接口**
- T5 工具返回 `ToolResult(ok/intent/text/data)`：模型看数据，用户看文案；
  `_do_ask_detail` / `_do_judge` / `_do_advice` 拆出 `*_of` 版本
- T6 单轮预算（150s / 6 次模型 / 8 次工具）+ 只对 `LLMError` 降级，
  代码缺陷不再被吞成规则回复
- T7 `record_application` 从工具面下架（JD 注入无处落点），改由规则意图承接；
  提议/执行留痕 `pending` / `performed`
- T10 `verify` 补上第三条：声称「已记录 / 已打开 / 已提交定制」必须有对应动作

**可测性**
- T8 新增 5 个夹具 + `smoke_agent_replay.py` 进 CI；`record_agent.py` 本地录制
- T9 `eval_agent` 由「交集」改「精确 / 子集 + 禁止集」，17 条用例，真实模型 17/17

**LLM 客户端**
- T11 一轮复用一个连接池（此前每步新建 AsyncClient）
- T12 读 `finish_reason`（截断提示）、清洗 `tool_calls`、`response_format` 自动降级
- T13 尊重 `Retry-After`；`LLMError.kind` 区分 network / auth / rate_limit / server / format / config

**安全与工程**
- T16 JD 标为 `sections_untrusted`；前端链接加 scheme 白名单（javascript: 被拦下）
- T17 替身模式不再把 JSON 甩给用户
- T18 `run.py` 校验 Python 版本，3.11 下给出可操作提示而不是 `ArgsKwargs`
- T14（删死代码）按用户要求推迟到后续版本

**测试**：断言 176 → **234**；意图 30/30；对话评测 17/17；ruff / mypy 均 0 问题。
