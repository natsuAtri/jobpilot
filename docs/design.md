# JobPilot · 设计文档 v1

> 状态：待确认　·　范围：M0–M3 + 投递记录与复盘　·　不含自动填表（M4）

---

## 1. 定位

**做**：一份简历能进来、能被结构化、能被生成、能被匹配、能被定制、投出去之后能被跟踪复盘。

**不做**：

| 不做 | 原因 |
|---|---|
| 无人值守批量投递 | 平台条款 + 封号风险，维护成本无底洞 |
| 账号池 / 代理池 / 验证码破解 | 同上，且让项目性质变成对抗 |
| 抓取平台全量岗位库 | 高成本、低边际收益，匹配质量才是瓶颈 |
| 简历内容自动"完善" | 造假，面试必翻车 |

**核心判断**：这个工具的杠杆不在"投得多"，而在"投得准 + 不重复劳动 + 有记录"。

---

## 2. 架构

```
┌─────────────────────────────────────────────────────────┐
│  app/web   Jinja2 + htmx + Alpine.js                    │
└──────────────────────────┬──────────────────────────────┘
                           │ REST（Pydantic 契约）
┌──────────────────────────▼──────────────────────────────┐
│  app/server   FastAPI + Uvicorn                         │
│  ┌────────────┬────────────┬────────────┬─────────────┐ │
│  │ parse      │ render     │ match      │ tailor      │ │
│  │ PDF→结构    │ 结构→PDF   │ 打分+学历  │ 改写+守恒   │ │
│  └────────────┴────────────┴────────────┴─────────────┘ │
│  ┌────────────┬────────────────────────────────────────┐ │
│  │ llm        │ platforms（M4）                         │ │
│  │ 注册表+绑定 │ 平台适配器                              │ │
│  └────────────┴────────────────────────────────────────┘ │
└──────────────────────────┬──────────────────────────────┘
                           │
                  ┌────────▼────────┐        ┌──────────────┐
                  │ SQLite (Drizzle)│        │ Chrome/Edge  │
                  │ 单文件，可备份   │        │ 打印 PDF     │
                  └─────────────────┘        └──────────────┘
```

**为什么是单体**：Playwright（M4 填表）与 Chrome 打印 PDF 是同一个引擎，同机同进程；前后端共享 Zod schema，简历结构在「解析 → 生成 → 填表」三处天然对齐，不需要跨语言契约。个人工具，不上分布式。

---

## 3. 技术选型

| 层 | 选择 | 说明 |
|---|---|---|
| 运行时 | Python 3.12 | 单语言，便于阅读与排查 |
| 后端 | FastAPI + Uvicorn | |
| 前端 | Jinja2 + htmx + Alpine.js | 服务端渲染，无构建步骤 |
| 存储 | SQLite（标准库 `sqlite3`）+ 手写 SQL + 薄仓储层 | 不引入 ORM，SQL 直接可读 |
| 契约 | Pydantic v2 | 前后端同一份模型 |
| PDF 解析 | `pypdfium2`（文本+坐标+页面位图） | BSD 许可，见 §3.2 |
| PDF 生成 | HTML/CSS 模板 → 本机 Chromium 命令行 `--print-to-pdf` | 见 §3.3 |
| 浏览器自动化 | Playwright Python（M4，headed + 持久化 profile） | 与 PDF 渲染同一引擎 |
| LLM | OpenAI 兼容协议，多厂商可换 | 见 §7 |

**为什么不引入 ORM**：个人工具、表不到 15 张，SQL 直接写在仓储层里最可读。ORM 的隐式行为（懒加载、级联、会话状态）恰恰是排查问题时最费神的部分。

**为什么不用 PyMuPDF**：PyMuPDF 解析能力更强、API 更好用，但许可是 **AGPL-3.0**。项目计划开源，AGPL 会给二次使用者带来传染性约束。`pypdfium2` 是 BSD/Apache 许可，同样能取文本、坐标与页面位图，代价是 API 更底层、少了些现成的版面分析工具——这些我们自己写，不构成障碍。

### 3.2 PDF 生成：为什么走命令行而不是 Playwright

M2 只需要「HTML → PDF」，Chromium 命令行 `--headless --print-to-pdf` 就够了：

| | 命令行 | Playwright |
|---|---|---|
| 额外依赖 | 0（复用已装的 Chrome/Edge） | 35MB 包 + 150MB 浏览器 |
| 启动耗时 | 直接起浏览器 | 先起 Node 驱动再起浏览器 |
| 需要的控制力 | 不需要等 JS/网络 | 需要 |

Playwright 留给 M4——那里需要操作 DOM 填表，命令行做不到。届时两者可以共存，互不影响。

### 3.1 字体与 PDF 保真（已实测，非推测）

实测结论：**Chrome 导出 PDF 时，Noto Sans SC 会把「工」「大」的 ToUnicode 写成康熙部首 `U+2F27` / `U+2F24`**。ATS 读出来是 `西北⼯业⼤学` —— 关键词直接失配。

| 字体 | 文本回读 | 结论 |
|---|---|---|
| Noto Sans SC (VF) | ❌ 部首码位 | 禁用 |
| **MiSans** | ✅ 正确 | **默认** |
| Microsoft YaHei | ✅ 正确 | 兜底 |

因此两条硬约束：

1. **字体自带**（`assets/fonts/MiSans-*.otf`，`@font-face` 引用），不依赖系统安装。
2. **导出必须回读校验**：PDF 生成后重新抽文本，与源内容逐字比对，不一致直接报错，不允许静默产出。

> 校验规则：去除空白后字符级 diff，容忍率 0；同时校验页数 = 1 且 MediaBox ≈ A4（594.96×841.92pt）。

---

## 4. 仓库结构

```
resume-agent/
├── app/
│   ├── main.py              FastAPI 入口
│   ├── routes/              路由：简历 / 岗位 / 匹配 / 定制 / 投递 / 设置
│   ├── services/            业务编排
│   ├── models/              Pydantic 模型（唯一真源）
│   ├── db/                  连接、迁移 SQL、仓储函数
│   ├── parse/               PDF → ResumeDoc
│   ├── render/              模板渲染 + 打印 + 回读校验
│   ├── match/               JD 解析、学历判定、维度打分
│   ├── tailor/              选材、排序、改写、三守恒
│   ├── dialogue/            对话式补录（§12）
│   ├── llm/                 厂商适配、任务绑定、prompt 版本
│   └── platforms/           平台适配器（M4 启用）
├── templates/               Jinja2 页面模板 + 简历 HTML 模板
├── static/                  CSS / Alpine / 字体
├── assets/fonts/            MiSans
├── migrations/              编号 SQL 迁移
└── tools/pdf_extract.py     解析可行性验证（已被 app/parse 取代）
```

---

## 5. 数据模型

### 5.1 简历侧

```ts
profile        (id, name, phone, email, location, links_json,
                preferred_cities_json,          -- 搜索偏好，非简历内容
                created_at, updated_at)

resume         (id, profile_id, kind,           -- imported | generated | tailored
                title, base_resume_id,          -- tailored 版指向基底
                job_family,                     -- 后端 / 算法 / 数据 …
                template_id, theme,             -- ink | graphite
                source_file, parse_status,      -- pending | parsed | verified
                created_at)

resume_section (id, resume_id, type,            -- education | experience | project
                order_index)                    --           | skill | award | cert

resume_item    (id, section_id, org, role, location,
                start_date, end_date, order_index, meta_json)

resume_bullet  (id, item_id, text, order_index,
                tags_json,                      -- 技能标签，用于关键词匹配
                strength,                       -- 1..5 责任强度，见 §6.5
                metrics_json,                   -- 抽取出的数字，用于守恒校验
                source_bullet_id)               -- 改写溯源；NULL 表示原始事实
```

**要点**：`resume_bullet` 的 `source_bullet_id` 与 `metrics_json` 是三守恒的执行基础——没有它们，改写校验只能靠 LLM 自述，等于没有校验。

### 5.2 岗位侧

```ts
job            (id, platform, external_id, url, title, company, industry,
                city, salary_min, salary_max, salary_months,
                experience_years, education_req,
                description_raw, description_parsed_json,
                posted_at, fetched_at, source,        -- url | search
                dedupe_key)                            -- UNIQUE(platform, external_id)

job_filter     (id, profile_id, cities_json,           -- 用户偏好，不进匹配分
                salary_min, experience, remote,
                include_keywords, exclude_keywords,
                updated_at)
```

### 5.3 匹配侧

```ts
match          (id, job_id, resume_id,
                total, dimensions_json, weights_json,
                education_status, education_soft,
                strengths_json, gaps_json,
                model, prompt_version, created_at)

-- UNIQUE(job_id, resume_id)
```

```ts
type EducationCheck = {
  requirement: string                      // JD 原文，如「硕士及以上」
  candidate: string                        // 候选人最高学历
  status: 'below' | 'meet' | 'exceed' | 'unknown'
  soft: boolean                            // JD 写的是「优先/加分」→ true
}

type FitScore = {
  total: number                            // 0-100
  dimensions: {
    experience: number                     // 年限
    skills: number                         // 必备技能覆盖
    domain: number                         // 领域相关
    language: number
    seniority: number                      // 职级
  }
  weights: Record<string, number>          // 可按岗位族调整
}
```

### 5.4 投递与复盘

```ts
application    (id, job_id, resume_id, status, channel,
                submitted_at, form_snapshot_json, notes, updated_at)

application_event (id, application_id, at, type, note)
```

状态机：

```
draft → submitted → viewed → replied → interview → offer
                                          └────→ rejected
              └──────────────────────────────────→ withdrawn
```

> 首版 `submitted` 由用户手动标记；M4 接自动填表后由流程写入。看板因此不被 M4 阻塞。

### 5.5 LLM 配置

```ts
llm_provider   (id, label, base_url, api_key_ref, models_json,
                capabilities_json,              -- {vision, jsonSchema, maxContext}
                enabled)
llm_binding    (task, provider_id, model, params_json)   -- 任务 → 模型
prompt_version (id, task, version, body, created_at)     -- prompt 版本化
```

`api_key_ref` 存的是引用（本地密钥文件 / 环境变量名），不把明文 key 写进 SQLite。

---

## 6. 核心流程

### 6.1 简历解析

```
PDF ──pypdfium2──> 文本块(x, y, w, h, 字号, 字体) + 页面位图
                     │
                     ├─ 行聚类（y 容差 3.5pt，同基线合并）
                     ├─ 列检测（x 分布聚类 → 识别双栏/侧边栏）
                     └─ 版面角色推断（字号+位置 → 姓名/章节标题/条目/正文）
                     │
                     ▼
              带位置的文本骨架
                     │
                     ▼  LLM（长上下文 + 强 JSON）
              ResumeDoc（section → item → bullet）
                     │
                     ▼
              逐字段置信度 → 低置信度高亮 → 人工校对
```

**已有的实证**：`tools/pdf_extract.py` 已独立实现 PDF 文本抽取（含 ObjStm、Type0/Identity-H、ToUnicode、CTM 矩阵），在真实简历上取全了内容。过程中暴露的三个真实难点，会直接决定 `packages/parse` 的实现：

1. **坐标翻转**——生成器常用 `cm` 翻转坐标系，不做 CTM 跟踪，内容顺序会整个反掉。
2. **嵌套字典**——正则截断会把 `/Resources` 切碎，必须用配对扫描。
3. **拼接**——`某某大学软件工程/本科2021.9-2025.6` 是一整行，标题/专业/学历/时间要 LLM 拆分，不能靠正则。

**扫描件**：渲染成图 → 视觉模型直读，不走 OCR 管线（对图标、双栏、印章更鲁棒）。

**校验优先于准确率**：解析永远做不到 100%。所以界面是「左 PDF 页面图 / 右结构化表单 / 低置信度高亮」，改完标 `verified` 才允许用于投递。

### 6.2 简历生成与导出

```
ResumeDoc + Template + Theme ──> HTML ──Chrome printToPDF──> PDF
                                                    │
                                                    ▼
                                          回读校验（§3.1）
```

两个出口：

| 出口 | 差异 |
|---|---|
| **设计版**（墨蓝 / 石墨） | 保留主题色、章节竖块、照片 |

一页压缩：内容超页时提示，可自动收紧行距与字号（下限 8.8pt），不静默截断。

### 6.3 岗位获取与筛选（M3）

首版只做**手动粘贴 URL** 导入。自动搜索放 M5。

```
URL ──适配器取页面──> 正文抽取 ──LLM 解析──> Job
                                         │
                                         └─ 每个抽取字段附 JD 原文片段（防幻觉）
```

筛选在列表层完成，**不进入匹配分**：

```ts
JobFilter.cities        // 用户选的城市
JobFilter.salaryMin
JobFilter.experience
JobFilter.remote
```

### 6.4 匹配

三步，顺序不能变：

**① 学历判定（独立，不进分数）**

```ts
EducationCheck.status =
  JD「硕士及以上」 && 你本科        → 'below'
  JD「硕士优先」   && 你本科        → 'below' + soft = true   // 只标记不劝退
  JD「本科及以上」 && 你本科        → 'meet'
  JD 未提                          → 'unknown'
```

**② 维度打分（可加和的部分）**

| 维度 | 判定方式 |
|---|---|
| experience | 规则：JD 年限区间 vs 简历时间轴 |
| skills | 规则 + 词表：JD 必备技能 vs `bullet.tags`（Java=Java，DL∈{TensorFlow,PyTorch}） |
| domain | LLM：项目所属领域 vs JD 行业 |
| language | 规则：CET/雅思等 |
| seniority | 规则 + LLM：职级与年限的匹配 |

**③ 产出**

```ts
{
  education: EducationCheck,          // 单独呈现
  score: FitScore,                    // 分维度 + 总分
  strengths: [{ jdQuote, resumeQuote }],
  gaps: [{
    what: string,
    type: '未体现' | '不具备' | '无法判断',   // ← 三者行动完全不同
    action: string
  }]
}
```

**`gaps.type` 是这块的核心。** 「未体现」= 改简历；「不具备」= 放弃或补；「无法判断」= 去问 HR。混成一个「缺失」，这个功能既不帮你改简历也不帮你筛岗位。

**没有一票否决 → 学历必须显式可见。** 学历标记出现在**列表卡片**上，不能只藏在详情页，否则高分低学历的岗位会被一路推到前面。

### 6.5 定制改写

**两级粒度**：

```
岗位族基底（3-5 份）      后端 / 算法 / 数据 / 全栈
        ↓ 派生（只记录 diff）
逐岗变体                  选材 + 排序 + 3-5 条改写
```

基底是稳定资产。逐岗变体存「相对基底动了什么」，这样面试前能一眼看出「投这家时我强调了什么」。

**产出四件事，按性价比排序**：

1. **选材** —— 哪些 item 进这一版
2. **排序** —— 最相关的提到第一位（收益远大于润色形容词）
3. **改写** —— 每条 bullet 换更贴 JD 的说法
4. **技能栏重排** —— JD 要的技术前移

**三守恒闸门**（机器校验，不依赖 LLM 自觉）：

| 闸门 | 规则 | 拦截的典型错误 |
|---|---|---|
| 数字守恒 | 源 bullet 的数字集合 ⊆ 改写后，值不可变 | 「QPS 3200」→「QPS 5000」 |
| 实体守恒 | 技术名/公司/项目名集合只能缩不能扩 | 凭空多出「Kubernetes」「千万级用户」 |
| 强度守恒 | 改写后强度 ≤ 原强度 | 「参与」升格为「主导」 |
| 长度约束 | ≤ 原文 1.25 倍 | 塞关键词塞到句子变形 |

责任强度分级：

```
1 参与  2 配合  3 负责  4 主导  5 从零搭建
```

实现：`tailor` 包输出改写候选 → 四个纯函数校验器逐条跑 → 不通过的回退原句并标记原因 → 人工在「左原右改」界面逐条确认。

**定制版永不覆盖基底**：`resume.base_resume_id` 指回，随时可退回干净版本。

### 6.6 投递与复盘

首版：用户在岗位详情点「标记已投」→ 选用了哪份简历 → 写 `application` 记录。

看板：

- **漏斗**：已投 → 已查看 → 有回复 → 面试 → Offer
- **归因**：按 `岗位族 × 简历版本` 统计回复率
- **时间轴**：某公司从投递到回复的间隔

**注意**：回复率需要样本量，早期不要据此调权重（投 10 家得出的结论还不如直觉）。但数据从第一天就要记，否则三个月后无法复盘。

---

## 7. LLM 层

### 7.1 不绑厂商，且按任务挑模型

```ts
interface LLMProvider {
  id: string
  capabilities: { text: true, vision: boolean, jsonSchema: boolean, maxContext: number }
  chat(req): AsyncIterable<Chunk> | Promise<Result>
}
```

配置是一张**任务绑定表**，用户可自行改：

| 任务 | 能力要求 | 说明 |
|---|---|---|
| 简历结构化抽取 | 长上下文 + 强 JSON | 质量敏感 |
| 扫描件识别 | vision | 多模态 |
| JD 解析 | 便宜、量小 | 可缓存 |
| 匹配打分 | 便宜、快 | 量大 |
| 简历改写 | 最强推理 | 质量敏感 |
| 表单字段识别（M4） | 便宜、快 | 结果沉淀成配置 |

### 7.2 三条纪律

1. **prompt 版本化** —— 每次匹配/改写记录 `model + prompt_version`，否则历史无法复盘。
2. **抽取必须带原文引用** —— 每个从 JD 抽出的要求都附原文片段，否则 LLM 会编出 JD 里没有的条件。
3. **敏感字段本地处理** —— 手机号、身份证、住址用正则识别并在送模型前脱敏，回填在本地做。

---

## 8. 页面

| # | 页面 | 核心交互 | 里程碑 |
|---|---|---|---|
| 1 | 档案 | 分步表单 | M1 |
| 2 | 导入校对 | 左 PDF 图 / 右表单 / 低置信度高亮 | M1 |
| 3 | 简历编辑 | 结构化编辑 + A4 实时预览 | M2 |
| 4 | 模板与配色 | 切换模板/主题即时重排 | M2 |
| 5 | 导出 | 设计版（两种配色）+ 回读校验结果 | M2 |
| 6 | 岗位库 | 筛选栏（城市优先）+ 列表（含学历标记） | M3 |
| 7 | 岗位详情 | 匹配分维度 + 学历判定 + 差距与行动 | M3 |
| 8 | 定制工作台 | 左原右改 diff，逐条接受/拒绝 | M3 |
| 9 | 投递看板 | 漏斗、归因、时间轴 | M3.5 |
| 10 | 设置 | LLM providers、任务绑定、搜索偏好 | M0 |

---

## 9. 里程碑与验收

### M0 骨架
交付：仓库结构、SQLite 迁移、LLM 注册表与任务绑定、设置页。
验收：能配置两家 provider，切换任务绑定后调用成功。

### M1 解析
交付：导入、解析、校对、存档。
验收：**导入你的真实简历 PDF，校对界面正确呈现全部字段，改三处后保存，重开一致。**

### M2 生成
交付：模板引擎、双配色、实时预览、双出口导出、回读校验、**对话式补录（补录 + 从零创建两种模式）**。
验收：**导出设计版 PDF；回读校验通过；全文可提取且字符与源一致。另：清空简历后能纯靠对话补出完整一份并导出。**

### M3 岗位与匹配
交付：URL 导入、JD 解析、学历判定、维度打分、差距分析、定制工作台。
验收：**贴一个真实岗位 URL → 看到学历判定、分维度得分、至少三条差距且能为每条归类（未体现/不具备/无法判断）→ 定制出 3 条改写全部通过三守恒 → 导出定制版 PDF。**

### M3.5 投递与复盘
交付：标记已投、状态流转、漏斗与归因看板。
验收：**记录 5 条投递并推进状态，看板正确汇总。**

### M4 自动填表（下一期）
交付：Playwright 持久化会话、字段映射引擎、预填 + 人工提交。
验收：**对同一平台连续两次投递，第二次字段识别复用配置，无需重新调模型。**

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| 解析准确率不足 | 下游全错 | 校对界面为主，准确率为辅；`verified` 门禁 |
| 字体 ToUnicode 错位 | 复制 / 搜索 / 解析读错字 | 自带 MiSans + 回读校验（已实证） |
| JD 解析幻觉 | 匹配跑偏 | 每项附原文引用 + JSON schema 校验 |
| 改写造假 | 面试翻车 | 三守恒机器校验 + 人工逐条确认 |
| 平台风控（M4） | 封号 | 半自动、headed、低频、人工提交 |
| LLM 成本 | 用不起 | 任务分级绑定 + JD 解析缓存 + 改写只跑入选条目 |
| 隐私 | 简历外泄 | 本地存储、密钥引用、送模型前脱敏 |

---

## 11. 对话式补录

**定位**：对话不是一套独立的聊天系统，而是 `ResumeDoc` 的**第二个输入通道**。解析出来的简历和聊出来的简历，最终落在同一套结构里。

两个入口：

| 入口 | 场景 | 初始状态 |
|---|---|---|
| 「补充内容」 | 已有简历，往里加实习/项目/技能 | 以现有 ResumeDoc 为底 |
| 「从零创建」 | 手上没有简历 | 空 ResumeDoc + 建档问卷 |

```
用户输入
   │
   ├─ 意图分类：补录 / 修改某条 / 提问 / 结束
   │
   ▼
读当前 ResumeDoc 的缺口 gap（时间缺失、角色缺失、无量化结果…）
   │
   ▼
生成下一个问题（一次只问一件事）
   │
   ▼
用户回答 ──> LLM 抽成 patch（结构化增量）
   │
   ▼
复述确认：「我理解你在 X 项目里负责 Y，做了 Z，对吗？」
   │
   ▼
用户确认 ──> 写入 ResumeDoc ──> 缺口重算 ──> 下一问
```

四条硬规则：

1. **状态外置** —— 对话的记忆是 `ResumeDoc`，不是消息列表。关掉页面再打开，进度不丢。
2. **增量落库** —— 每轮产出的是 patch，不是自由文本。抽取失败就重新问，不写入。
3. **可溯源** —— 对话产生的每个 bullet 记 `source='chat'` 与 `user_utterance` 原话，供改写时的三守恒校验追溯。
4. **缺口驱动** —— 不问通用问卷，先扫 ResumeDoc 缺什么再问。有项目标题没时间、有职责没数字的，优先问数字。

追问优先级（按信息增益排序）：

```
时间区间 > 角色/职责边界 > 量化结果 > 技术栈 > 团队规模 > 主观评价
```

```ts
dialogue_session (id, profile_id, resume_id, mode,       -- supplement | scratch
                  state_json,                            -- 当前缺口快照
                  created_at)
dialogue_turn    (id, session_id, role, content,
                  patch_json,                            -- 本轮抽出的增量
                  accepted, created_at)
```

**为什么不用「纯聊天」形态**：自由对话产出的是一大段文字，还得再抽一次结构，错误会累积两遍。让每轮直接产出结构化 patch，只有一次转换，且失败当场可见。

---

## 12. 幻觉控制

按环节列，每条都是可实现的机制。

### 抽取（PDF→结构、JD→字段）

1. **强制原文引用** —— 每个字段必须带 `source_quote`，无引用即置 `null`。
2. **Schema 校验** —— Pydantic 类型/枚举/必填，不合法直接拒绝并重试，不修补。
3. **二次抽取比对** —— 同输入跑两次，不一致的字段降置信度进校对界面。
4. **拒绝而非猜测** —— 信息不足返回 `null`，交给人工，不让模型"合理推测"。
5. **低 temperature**。

### 改写（定制简历）

1. **三守恒**（数字 / 实体 / 强度），见 §6.5。
2. **逐条溯源** —— 每条改写结果 `source_bullet_id` 必填，追不回的丢弃。
3. **禁止二次改写** —— 改写结果不得作为下一轮改写的输入，避免漂移累积。

### 对话

1. **只说用户说过的** —— 推断性内容必须标为「待确认」，不进正式字段。
2. **绑定原话** —— 每个 fact 存 `user_utterance`。
3. **复述确认** —— 写入前把理解回述一遍让用户点头。
4. **数字缺失就追问** —— 不估算、不填空。

### 匹配

1. **硬性条件用规则** —— 学历、年限、语言由代码判定，不交给模型。
2. **判定必带双证据** —— LLM 判定必须同时给出 JD 原文与简历原文。
3. **总分由维度加权** —— 不让模型直接吐一个总分。

### 界面层（最后一道闸）

1. 一切写入数据库的内容必经人工确认。
2. 低置信度字段高亮，不默认折叠。

### 明确无效，不要指望

| 做法 | 为什么无效 |
|---|---|
| prompt 里写「不要编造」 | 对抽取类几乎无约束力，必须靠引用与校验 |
| 让模型自评置信度 | 校准很差，只能当排序信号，不能当门禁 |
| 多轮自我批评 | 会引入新幻觉，且成本翻倍 |

---

## 13. 上下文管理

核心思路：**上下文的大小由结构决定，不靠事后截断。**

1. **状态外置** —— 上下文里放「简历摘要」，不放原始 PDF 文本，也不放完整聊天记录。
2. **任务拆分** —— 解析、匹配、改写是三次独立调用，各自只带所需数据。
3. **逐条处理** —— 定制改写一次只处理一条 bullet + JD 的相关片段，不整份简历全上。
4. **JD 分块** —— 长 JD 只取「工作职责」与「任职要求」两段。
5. **滑动窗口** —— 对话保留最近 N 轮原文，更早的压成摘要。
6. **预算表** —— 每类调用给定输入预算，超出按优先级裁剪，不交给模型自己决定。
7. **缓存** —— JD 解析结果按 URL hash 落库；简历 tags 预处理一次存库。

| 任务 | 输入预算 | 装什么 |
|---|---|---|
| 简历解析 | 8k | 该页文本块 + 坐标 |
| JD 解析 | 4k | 职责 + 要求两段原文 |
| 匹配打分 | 4k | 简历摘要（每条 bullet 一行）+ JD 要点 |
| 单条改写 | 1k | 该 bullet + 原文 + 命中的 JD 关键词 |
| 对话一轮 | 3k | 简历摘要 + 最近 5 轮 + 待确认清单 |
| 表单字段识别（M4） | 1k | 单个表单区的 DOM 摘要 |

---

## 14. 资源占用

### 内存（本机实测 + 估算）

| 项 | 占用 | 来源 |
|---|---|---|
| 无头 Chromium（空白页，10 进程） | **510 MB** | 实测 |
| Python + FastAPI + Uvicorn 常驻 | 70–100 MB | 估算 |
| SQLite | 5–15 MB | |
| PyMuPDF 解析单页 | +30–60 MB | |
| Playwright 驱动进程 | +60–90 MB | 仅导出时 |
| 浏览器界面标签页（独立进程） | 150–250 MB | |

场景峰值：

| 场景 | 峰值 |
|---|---|
| 服务空转 | **90–120 MB** |
| 导入并解析一份简历 | 150–200 MB |
| 导出 PDF（拉起 Chromium） | **500–650 MB** |
| M4 自动填表（有头，2–4 标签） | 700–1000 MB |

**大头是 Chromium，且只在导出/填表时出现。** 服务本身百兆级。

### 磁盘

| 项 | 占用 |
|---|---|
| Python 运行时 + 依赖 | ~150 MB |
| Playwright 自带 Chromium | ~400–500 MB |
| SQLite + 生成的 PDF | 通常 < 100 MB |
| **合计** | **约 0.7–0.9 GB** |

---

## 15. 待定

1. 多用户 / 多人共用一台机器 —— 当前按单用户设计，`profile` 表已预留。
2. 海外平台适配器优先级 —— M5 再定，`platforms` 接口先留。
3. 照片存储与元数据清理（EXIF 含拍摄地点）—— M2 实现时补。
4. 权重是否开放给用户手调 —— 先固定，看复盘数据再决定。
