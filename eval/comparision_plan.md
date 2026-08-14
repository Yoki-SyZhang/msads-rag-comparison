# 量化 Evaluation 准备工作：questions.json 分类补全 + comparison_runs log 结构扩展

## Context

README.md「目标产出」定义的最终交付物是跑全部统一问题、自动汇总指标、产出第一版对比报告。要做到这一步，有两个前置缺口必须先补上；同时按本轮反馈从 README 和本 plan 中移除 gold URL 命中率，不再把 URL 字符串匹配当作检索质量指标：

1. `eval/questions.json` 的问题范畴没有覆盖 README 自己定义的"按问题类型分析"清单，尤其是结构化页面里的 `accordion`（0 条）和 `table`（0 条），以及"无答案/边界问题"类别整体偏薄。
2. `eval/comparison_runs/` 现有的 log 结构里，"模型本身"和"按问题类型分析"两组指标现在算不出来——没有 token 记录，两条 pipeline 的过程性指标（tool-call 数等）字段不对称，也没有能把一条 run log 可靠地和 `questions.json` 里某一条问题关联起来的 join key。

以下是实测调查结果（读代码 + 跑脚本得到，不是猜测）和对应的具体改法，Part 1 已经按反馈把每一条问题的具体内容都列清楚了。

---

## Part 1：`eval/questions.json` 怎么改

### 目标分布（总计 35 条，不做 schema 迁移，仍是 `id/question/category/difficulty/type/gold_urls/reference_answer/expected_behavior` 8 字段）

| type 大类      | 具体 type 值                        | 数量         | 难度配比（易:中:难 = 2:2:1） |
| -------------- | ----------------------------------- | ------------ | ---------------------------- |
| 单一事实       | `single_fact`                     | 10           | 4 易 / 4 中 / 2 难           |
| 多部分问题     | `multi_fact`                      | 10           | 4 易 / 4 中 / 2 难           |
| 结构化页面     | `table` / `accordion`           | 5            | 2 易 / 2 中 / 1 难           |
| 跨页综合       | `cross_page` / `comparative`    | 5            | 2 易 / 2 中 / 1 难           |
| 无答案/边界    | `out_of_scope` / `missing_info` | 5            | 2 易 / 2 中 / 1 难           |
| **合计** |                                     | **35** |                              |

`categorical`、`time_sensitive` 两个不在 README 目标清单里的 type 值全部改掉（`categorical` 并入 `accordion`）；`image_only` 按反馈整体去掉——两条 pipeline 都不是多模态模型，测图片内容没有意义。改完之后 `type` 字段只会出现 8 个值：`single_fact`/`multi_fact`/`table`/`accordion`/`cross_page`/`comparative`/`out_of_scope`/`missing_info`。

按反馈，**问题的 `category` 不再涉及 `capstone`**——原来 5 条 `category=capstone` 的题目（`K01`~`K05`）全部从最终名单里去掉，不用别的 capstone 题替换。

以下是完整的 35 条最终清单，按 5 个大类分别列出。所有问题及答案都以 `pipeline_grag_v2/docs/page_summaries.json` 所列页面对应的 **2026-04-25 本地抓取语料**为准，不以实施时的网站现状为准。`gold_urls` 只保留为题目来源/provenance，不参与任何指标计算。

`reference_answer` 采用“最小充分答案”：完整覆盖问题要求的必要事实，但不加入无关背景。列表题给出闭合集合，数字题保留单位和必要限定，比较题按同一维度对齐；`missing_info` 和 `out_of_scope` 使用标准化的语料边界答案。“答案依据”列使用本地 processed chunks 中的 **Page > Section/Accordion/Quarter** 路径；边界题没有正向证据时，明确写为对全部 14 页本地语料的全局缺失检查。

难度不是由问句长短决定，而按本地语料中的实际检索负担判断：证据位置数量、页面结构深度、是否需要聚合、以及同页干扰项数量。优先换题满足 4/4/2 或 2/2/1 的难度分布，不通过给简单题上调标签来凑数。这里的 `table` 泛指表格、课程进度矩阵、图表数据等结构化内容，不要求底层必须是 HTML `<table>`。每条题目只保留一个核心信息需求；允许围绕同一个比较维度列举多个对象，但不把两个可独立作答的问题用 `and` 拼在一起。

#### 大类 1：单一事实 `single_fact`（10 条）

| id  | category       | 难度 | 问题                                                                                                      | reference_answer                                                                                                        | 答案依据（Page > Section）                                                     |
| --- | -------------- | ---- | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| A01 | admissions     | 易   | How many letters of recommendation do I need for the MS in Applied Data Science?                          | The MS in Applied Data Science program requires two letters of recommendation.                                          | How to Apply > Letters of Recommendation                                       |
| F01 | faculty        | 易   | Who is the director of the MS in Applied Data Science program at UChicago?                                | Greg Green is the Director of the MS in Applied Data Science Program at the University of Chicago.                      | Faculty, Instructors, Staff > Faculty, Instructors > Greg Green                |
| T01 | tuition        | 易   | How much does the MS in Applied Data Science program cost per course?                                     | The listed tuition is $6,384 per course for Summer 2025, Autumn 2025, Winter 2026, and Spring 2026.                     | Tuition, Fees, & Aid > Program Tuition                                         |
| T04 | tuition        | 易   | How are MS-ADS applicants considered for merit scholarships?                                              | Applicants are automatically considered for a merit scholarship once they apply to the MS-ADS program.                  | Tuition, Fees, & Aid > Financial Aid                                           |
| A06 | admissions     | 中   | Under what circumstance must an MS-ADS applicant submit proof of English language proficiency?            | Applicants who do not meet the English Language Proficiency criteria must submit proof of English language proficiency. | How to Apply > English Language Requirement                                    |
| T03 | tuition        | 中   | How is the $1,500 non-refundable enrollment deposit applied?                                              | The $1,500 non-refundable enrollment deposit is credited toward the student's first-quarter tuition balance.            | Tuition, Fees, & Aid > Program Tuition                                         |
| E03 | curriculum     | 中   | In which quarter does Leadership and Consulting for Data Science appear in the sample part-time schedule? | Leadership and Consulting for Data Science appears in Quarter 3 of the sample part-time schedule.                       | Course Progressions > Sample Part-Time Schedule > Quarter 3 • 10 Weeks        |
| C07 | program_format | 中   | At what time do weekday live synchronous classes in the Online MS-ADS program begin?                      | Weekday live synchronous classes begin at 6 p.m. Central Time.                                                          | Online Program > Your Time, Your Advancement                                   |
| T05 | faculty        | 难   | Which MS-ADS faculty member led AI at William Blair after spending 20 years at IBM?                       | The faculty member is Nick Kadochnikov.                                                                                 | Faculty, Instructors, Staff > Faculty, Instructors > Nick Kadochnikov          |
| C14 | curriculum     | 难   | In which quarter of the sample 2-year full-time schedule is Thesis Course I taken?                        | Thesis Course I is taken in Quarter 5 of the sample 2-year full-time schedule.                                          | Course Progressions > Sample 2-Year Full-Time Schedule > Quarter 5 • 10 Weeks |

#### 大类 2：多部分问题 `multi_fact`（10 条）

| id  | category   | 难度 | 问题                                                                                        | reference_answer                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | 答案依据（Page > Section）                                                                                                                                                                                                                                    |
| --- | ---------- | ---- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A07 | admissions | 易   | Which standardized test scores are optional for MS-ADS applicants?                          | GRE and GMAT scores are optional: neither test is required, but applicants may submit either or both scores.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | FAQs > Application Process > Is the GRE or GMAT required for the Master's in Applied Data Science program?; FAQs > Application Process > I took the GRE and/or GMAT and want to include my score(s)                                                           |
| C02 | curriculum | 易   | Which core courses are scheduled in Quarter 1 of the sample part-time program?              | Quarter 1 includes Statistical Models for Data Science and Data Engineering Platforms for Analytics or Big Data and Cloud Computing as core courses.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Course Progressions > Sample Part-Time Schedule > Quarter 1 • 10 Weeks                                                                                                                                                                                       |
| C12 | curriculum | 易   | Which sample elective courses are listed for the in-person MS-ADS program?                  | The listed sample electives are Advanced Computer Vision with Deep Learning; Advanced Machine Learning and Artificial Intelligence; Applied Generative AI: Agents and Multimodal Intelligence; Bayesian Machine Learning with Generative AI Applications; Causal Models for Data Science; Data Science for Algorithmic Marketing; Data Science for Healthcare; Data Visualization Techniques; Digital Marketing Analytics in Theory and Practice; Deep Reinforcement Learning; Generative AI: Principles and Applications; Machine Learning Operations; Next-Gen NLP: LLM and Agentic AI in Practice; Optimization and Simulation Methods for Data Science; Quantitative Finance: Methods and Applications; Real Time Intelligent Systems; and Supply Chain Optimization. | In-Person Program > Sample Elective Courses > Accordion                                                                                                                                                                                                       |
| C15 | campus     | 易   | Which downtown Chicago buildings host In-Person MS-ADS classes?                             | In-Person MS-ADS classes are held at NBC Tower and the Gleacher Center in downtown Chicago.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | In-Person Program > Your Engagement; In-Person Program > By and For Data Science Innovators; Explore the MS-ADS Campus > Page Overview                                                                                                                        |
| A04 | admissions | 中   | What application materials do I need to submit for the MS in Applied Data Science?          | Applicants submit two letters of recommendation, a candidate statement, a resume or CV, a programming supplement, a virtual portfolio video, unofficial transcripts, and the application fee. Applicants who do not meet the English Language Proficiency criteria must also submit proof of English proficiency.                                                                                                                                                                                                                                                                                                                                                                                                                                                         | How to Apply > Letters of Recommendation; Candidate Statement; Resume/CV; Programming Supplement; Programming Supplement > Virtual Portfolio; English Language Requirement; Transcripts from all previous colleges and universities attended; Application Fee |
| C03 | curriculum | 中   | What kinds of electives are available in the MS-ADS online program?                         | The listed Online sample electives are Advanced Computer Vision with Deep Learning; Advanced Machine Learning and Artificial Intelligence; Bayesian Machine Learning with Generative AI Applications; Data Science for Algorithmic Marketing; Data Science for Healthcare; Data Visualization Techniques; Digital Marketing Analytics in Theory and Practice; Quantitative Finance: Methods and Applications; Generative AI: Principles and Applications; Machine Learning Operations; Next-Gen NLP: LLM and Agentic AI in Practice; Real Time Intelligent Systems; Deep Reinforcement Learning; and Supply Chain Optimization.                                                                                                                                           | Online Program > Sample Elective Courses > Accordion                                                                                                                                                                                                          |
| T02 | tuition    | 中   | What financial aid options are available for MS-ADS students?                               | Funding options described in the corpus include partial-tuition merit scholarships, outside scholarships, student loans and federal-loan eligibility, international-student funding resources, employer tuition benefits, and student employment.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | Tuition, Fees, & Aid > The Data Science Institute Scholarship, MS in Applied Data Science Alumni Scholarship; Other Scholarships; Financial Aid                                                                                                               |
| F03 | faculty    | 中   | Which courses does Arnab Bose teach in the MS-ADS program?                                  | Arnab Bose teaches Machine Learning, Machine Learning Operations, Time Series Analysis and Forecasting, and Data Science in Healthcare.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Faculty, Instructors, Staff > Faculty, Instructors > Arnab Bose, PhD                                                                                                                                                                                          |
| F04 | faculty    | 难   | Which MS-ADS instructors have worked at Google?                                             | Greg Green previously served as a Director at Google, and Mike Anderson is a Staff Data Scientist at Google.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | Faculty, Instructors, Staff > Faculty, Instructors > Greg Green; Faculty, Instructors, Staff > Faculty, Instructors > Mike Anderson                                                                                                                           |
| C10 | curriculum | 难   | How are the six core courses sequenced across the sample full-time and part-time schedules? | In the full-time schedule, Quarter 1 has Statistical Models for Data Science, Leadership and Consulting for Data Science, and Data Engineering Platforms for Analytics or Big Data and Cloud Computing; Quarter 2 has Machine Learning I and Time Series Analysis and Forecasting; Quarter 3 has Machine Learning II. In the part-time schedule, Quarter 1 has Statistical Models for Data Science and Data Engineering Platforms for Analytics or Big Data and Cloud Computing; Quarter 2 has Machine Learning I and Time Series Analysis and Forecasting; Quarter 3 has Machine Learning II and Leadership and Consulting for Data Science.                                                                                                                             | Course Progressions > Sample Full-Time Schedule > Quarters 1–3; Course Progressions > Sample Part-Time Schedule > Quarters 1–3                                                                                                                              |

#### 大类 3：结构化页面 `table` / `accordion`（5 条）

| id  | category   | 难度 | sub-type      | 问题                                                                                       | reference_answer                                                                                                                                                                                                                                                       | 答案依据（Page > Section）                                                                                                                                                                  |
| --- | ---------- | ---- | ------------- | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| C01 | curriculum | 易   | `accordion` | Which six core courses are required in the In-Person MS-ADS program?                       | The six core courses are Time Series Analysis and Forecasting; Statistical Models for Data Science; Machine Learning I; Machine Learning II; Data Engineering Platforms for Analytics or Big Data and Cloud Computing; and Leadership and Consulting for Data Science. | Course Progressions > Curriculum Details; In-Person Program > Core Courses > Accordion                                                                                                      |
| C09 | curriculum | 易   | `accordion` | Which noncredit courses are required or optional in the In-Person MS-ADS program?          | Career Seminar is required. Introduction to Statistical Concepts, R for Data Science, Python for Data Science, and Advanced Linear Algebra for Machine Learning are optional. Brush up on the Basics is an optional preparation resource, not a noncredit course.      | In-Person Program > Noncredit Courses > Accordion; the five named course sections; Brush up on the Basics (Optional resource)                                                               |
| C08 | curriculum | 中   | `table`     | In the sample full-time schedule, which quarter includes Machine Learning I?               | Machine Learning I is included in Quarter 2 of the sample full-time schedule.                                                                                                                                                                                          | Course Progressions > Sample Full-Time Schedule > Quarter 2 • 10 Weeks                                                                                                                     |
| C13 | curriculum | 中   | `table`     | How many academic quarters does the sample full-time schedule span?                        | The sample full-time schedule spans four academic quarters, Quarter 1 through Quarter 4; the five-week Prequarter is not an academic quarter.                                                                                                                          | Course Progressions > Sample Full-Time Schedule                                                                                                                                             |
| S01 | students   | 难   | `table`     | How does the age distribution differ between the Autumn 2025 In-Person and Online cohorts? | In-Person: ages 21–24, 72%; 25–29, 20%; 30–34, 5%; 35+, 3%. Online: ages 21–24, 44.4%; 25–29, 14.8%; 30–34, 25.9%; 35+, 14.8%.                                                                                                                                   | Our Students > Embedded Data Visualizations > In-Person Program Age Breakdown; Online Program Age Breakdown; Our Students > Meet a Few of Our Students > Age Breakdown (In-Person & Online) |

#### 大类 4：跨页综合 `cross_page` / `comparative`（5 条）

| id  | category       | 难度 | sub-type        | 问题                                                                                                             | reference_answer                                                                                                                                                                                                                                                         | 答案依据（Page > Section）                                                                                                                                                     |
| --- | -------------- | ---- | --------------- | ---------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| X04 | curriculum     | 易   | `comparative` | Are the required core courses the same for the In-Person and Online MS-ADS programs?                             | Yes. Both programs require Time Series Analysis and Forecasting; Statistical Models for Data Science; Machine Learning I; Machine Learning II; Data Engineering Platforms for Analytics or Big Data and Cloud Computing; and Leadership and Consulting for Data Science. | In-Person Program > Core Courses > Accordion; Online Program > Core Courses > Accordion; Course Progressions > Curriculum Details                                              |
| X05 | program_format | 易   | `comparative` | Which study-load options are available in both the In-Person and Online MS-ADS programs?                         | Both the In-Person and Online MS-ADS programs offer full-time and part-time study options.                                                                                                                                                                               | Course Progressions > Flexible Formats; In-Person Program > Tailor Your Data Science Journey; Online Program > Academic Rigor Meets Work/Life Balance                          |
| C05 | curriculum     | 中   | `comparative` | What's the difference between the 1-year 12-course program and the 2-year thesis track?                          | The 1-year program requires 12 courses and is available full-time or part-time. The 2-year thesis track requires 18 courses, is full-time only, spans six academic quarters, includes more electives and two independent-study courses, and requires a master's thesis.  | In-Person Program > 1-Year 12 Course Program (Full- and Part-Time Options); In-Person Program > 2-Year Thesis Track, 18 Course Program (Full-Time Only)                        |
| A05 | admissions     | 中   | `cross_page`  | Which MS-ADS program options are visa-eligible for international students?                                       | Only the full-time In-Person MS-ADS program is visa-eligible. The Online program does not provide visa sponsorship.                                                                                                                                                      | How to Apply > International Students; FAQs > Online Program > Do I need to be a US citizen or permanent resident to apply to Master's in Applied Data Science Online Program? |
| X03 | curriculum     | 难   | `cross_page`  | Which sample elective courses are listed for the In-Person MS-ADS program but not for the Online MS-ADS program? | The In-Person list additionally includes Applied Generative AI: Agents and Multimodal Intelligence; Causal Models for Data Science; and Optimization and Simulation Methods for Data Science.                                                                            | In-Person Program > Sample Elective Courses > Accordion; Online Program > Sample Elective Courses > Accordion                                                                  |

#### 大类 5：无答案/边界问题 `out_of_scope` / `missing_info`（5 条）

| id  | category | 难度 | sub-type         | 问题                                                                                                   | reference_answer                                                                                                                                                                             | 答案依据（Page > Section）                                                                                                  |
| --- | -------- | ---- | ---------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| E01 | edge     | 易   | `out_of_scope` | What is the capital of France?                                                                         | This question is outside the scope of the supplied MS-ADS program corpus.                                                                                                                    | All 14 local pages (global corpus check): no page or section covers general knowledge about France                          |
| E02 | edge     | 易   | `missing_info` | What is the dress code for the program?                                                                | The supplied MS-ADS corpus does not state a program dress code.                                                                                                                              | All 14 local pages (global corpus check): no page or section mentions a dress code                                          |
| E04 | edge     | 中   | `out_of_scope` | How does the UChicago MS in Applied Data Science compare to Northwestern's MS in Data Science program? | The supplied corpus does not contain information about Northwestern's MS in Data Science program, so it cannot support this comparison.                                                      | All 14 local pages (global corpus check): no section provides facts about Northwestern's MS in Data Science program         |
| E06 | edge     | 中   | `missing_info` | What is the average class size for MS-ADS core courses?                                                | The supplied MS-ADS corpus does not state the average class size for core courses.                                                                                                           | All 14 local pages (global corpus check): no page or section gives class-size data                                          |
| E05 | edge     | 难   | `out_of_scope` | Between UChicago and Stanford, which school's program is better for someone focused on NLP?            | The supplied corpus does not contain information about Stanford's program or an evidence-based criterion for deciding which program is better for NLP, so it cannot support this comparison. | All 14 local pages (global corpus check): no section provides Stanford program facts or a UChicago–Stanford NLP comparison |

### 从现有 31 条里整体移除的题目

以下现有题不进入最终的 35 条名单（内容仍然有效，只是被更贴合目标分布/反馈意见的题目替代，不需要删除站点内容，只是不再放进 `questions.json`）：`A02`、`A03`、`C04`、`F02`、`F05`、`K01`、`K02`、`K03`、`K04`、`K05`、`R01`、`R02`、`S02`、`X01`、`X02`。

其中 `K01`~`K05` 五条原本全部是 `category=capstone`——按反馈彻底不再问 capstone 相关的问题，`capstone` 不再作为最终 35 条里任何一条的 `category`。`R01`（薪资）按反馈换成了 `F03`；`R02` 是唯一的 `image_only` 题，按反馈"两条 pipeline 都不是多模态模型，不测图片内容"整体去掉。这两处调整的副作用是 `career` 这个 `category` 在最终 35 条里也不再出现（不强行凑回来，属于反馈的直接结果，不是遗漏）。

### 覆盖检查

按 `category`（内容域）统计最终 35 条：curriculum 13、admissions 5、edge 5、faculty 4、tuition 4、program_format 2、campus 1、students 1——共 8 个内容域被覆盖到。`capstone`、`career` 按反馈不再出现。难度分布逐大类严格为：10 条组 4 易 / 4 中 / 2 难，5 条组 2 易 / 2 中 / 1 难；这些标签由上面的本地证据位置、结构深度、聚合量和干扰项说明支撑。

---

## Part 2：`eval/comparison_runs/` log 结构怎么扩展

### 现状（实测两个真实 run 文件得到的准确结构）

```
{timestamp, question, results: {<pipeline_id>: {
  pipeline_id, display_name, answer, sources, steps, evidence,
  time_sec, low_confidence, error, raw_debug: {...}, progress_summary (仅 grag_v2)
}}}
```

`raw_debug` 目前**两条 pipeline 不对称**：GRAG v2 有一个现成的 `metrics` 子对象（`agent_turns, tool_calls, judge_calls, llm_calls, valid_fetch_count, invalid_fetch_count, accepted_evidence_count, stop_reason, fallback_triggered`）；FlatRAG 的 `raw_debug` 只是整个原始 pipeline 返回 dict 的直接转存，没有归一化的 `metrics` 块。两边都**没有 token 记录**——`pipeline_grag_v2/agent/deepseek_client.py` 只累加了 `call_count`，OpenAI 兼容接口返回的 `response.usage`（prompt/completion/total tokens）现在被直接丢弃；FlatRAG 更分散，`client.chat.completions.create(...)` 直接散落在 4 个文件共 9 处调用点（`qa_pipeline.py` x6、`page_selector.py`、`query_router.py`、`ui/app.py`），没有任何一层统一的 client 包装。log 里也**没有 `question_id`**——只存了问题原文字符串，要按类型聚合分析必须靠文本模糊匹配回 `questions.json`，questions.json 一旦被编辑（比如本 plan Part 1 的重排）历史 run 就可能匹配失真。

**架构上的一个前提，按反馈明确一下**：`ui/app.py`（Streamlit）在这套 evaluation 框架里的角色，仅限于"跑两条 pipeline + 把原始数据写进 `eval/comparison_runs/`"，不负责任何指标计算或展示。所有指标（包括下面第 2、4 点）的计算和图表展示，都放在一个新建的 ipynb notebook 里完成——那个 notebook 才是"对比实验"的实际执行和展示载体，UI 只是产生原始 log 的工具。这个 notebook 本身的搭建不在本 plan 范围内（后续阶段），但下面的 log 字段设计以"喂给这个 notebook 用"为目标。

### 按 README「目标产出」的每一组指标，逐项对应改法

**1. 平均 latency** —— 已经够用（`time_sec` 每条 pipeline 都有），不需要改 log 结构，聚合在后续的 ipynb notebook 里做。

**2. 平均 cost** —— 缺失，需要新增 token 记录。**按反馈，"cost"就用喂给 LLM 的 token 总数来体现，不换算成美元**（不管是在 log 阶段还是在 notebook 报告阶段都不做价格换算）：

- GRAG v2 侧：`DeepSeekClient.chat_json`/`chat_text` 里读 `response.usage.prompt_tokens/completion_tokens/total_tokens` 并累加（API 本来就返回这个字段，现在只是没读），改动集中在 `pipeline_grag_v2/agent/deepseek_client.py` 一个文件的 2 个方法。
- FlatRAG 侧：没有现成 client 包装类，9 个调用点分散在 4 个文件。建议不做大重构，加一个轻量的、每次 request 开始时重置的累加器（模块级或传入 pipeline run 的一个小对象），在这 9 处调用后各加一行读 `response.usage` 累加——机械性改动，不改变调用逻辑本身。
- 两边 adapter 最终都在 `raw_debug.usage = {"prompt_tokens", "completion_tokens", "total_tokens", "llm_calls"}` 里暴露出来，notebook 直接读 `total_tokens` 作为"cost"这一项的数值即可。

**3. 过程指标（tool-call 数 / loop 数 / evidence 数）** —— GRAG v2 已有，FlatRAG 缺。给 `ui/adapters/pipeline_flatrag_adapter.py` 补一个同名结构的 `raw_debug.metrics`，从它已有的 `agent_steps`/`evidence`/`attempts` 直接算出来：

```python
"metrics": {
    "tool_calls": sum(len(s.get("tool_calls", [])) for s in agent_steps),
    "loop_count": len(agent_steps),
    "evidence_count": len(evidence),
    "llm_calls": <累加器读数，见上>,
    "judge_calls": None,          # FlatRAG 概念上不存在，保留 key 但填 None
    "valid_fetch_count": None,
    "invalid_fetch_count": None,
    "fallback_triggered": None,
}
```

两边共用同一组 key 名（不适用的填 `None`），notebook 里可以用同一段代码读 `results[pid]["raw_debug"]["metrics"]["tool_calls"]`，不需要按 pipeline 分支处理。

**4. RAGAS 结果指标**（Context Precision/Recall/Relevancy、Faithfulness、Answer Relevancy、Answer Correctness）—— 这些都需要额外一次 LLM-judge/RAGAS 调用，比较慢，不适合在 Streamlit "Compare" 点击时同步算。**按反馈，这些指标的计算和展示统一放在后续新建的 ipynb notebook 里做，作为对比实验本身的执行和展示**，不经过 `ui/app.py`。comparison_runs 该存的东西其实已经在存了（完整 answer 文本 + evidence/sources，这正是 RAGAS 打分所需的原始输入）。这一步只需要加一个能可靠 join 回 `questions.json` 的 key（见第 5 点），notebook 里再用这个 key 去读 `reference_answer` 做真正的打分。

**5. 按问题类型分析** —— 需要稳定的 join key，而不是靠问题原文字符串模糊匹配。在 `_write_run_log()` 的 payload 顶层加两个字段：

```python
"question_id": "A01" | null,       # 来自下拉框选择时有值，手打问题时为 null
"question_meta": {                  # 写入时刻从 questions.json 拷贝的快照，不是引用
    "category": "...", "type": "...", "difficulty": "...", "expected_behavior": "..."
}
```

用快照而不是引用的原因：以后 questions.json 改了，历史 run 文件的分类信息不会跟着变，保证回溯分析时"跑的时候是什么类型"是准的。

**6. 商业性表现（最适合场景/失败案例）** —— 定性内容，不需要改 log 结构；按反馈，具体做法放在 Part 3 的第 4 段（见下）。

### 另外建议加一个版本标记

`schema_version: 2` 放在每个新 run log 文件顶层——现有 28 个历史文件不会有 `usage`/`metrics`/`question_id`/`question_meta` 这些新字段，notebook 里靠这个字段区分新旧格式，遇到旧文件缺字段时能优雅跳过而不是报错。

### 改动落点汇总

| 文件                                                 | 改动                                                                                                                                                                                                                                      |
| ---------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pipeline_grag_v2/agent/deepseek_client.py`        | 累加`response.usage` 到 `prompt_tokens`/`completion_tokens`/`total_tokens`                                                                                                                                                        |
| FlatRAG 9 个调用点（`qa_pipeline.py` 等 4 个文件） | 加一个轻量累加器，每处调用后读一次`response.usage`                                                                                                                                                                                      |
| `ui/adapters/pipeline_flatrag_adapter.py`          | 新增归一化`raw_debug.metrics` 块 + `raw_debug.usage`                                                                                                                                                                                  |
| `ui/adapters/pipeline_grag_v2_adapter.py`          | 现有`raw_debug.metrics` 里补 `usage`                                                                                                                                                                                                  |
| `ui/app.py` `_write_run_log()`                   | 新增`question_id`、`question_meta`、`schema_version`                                                                                                                                                                                |
| 新增`eval/comparison_lib.py`                       | 把`ui/app.py` 里的 `_load_questions`/`_write_run_log`/`_slug` 三个函数抽出来放进这个不依赖 Streamlit 的共享模块；`ui/app.py` 改成从这里 import，Part 3 的 notebook 也从这里 import——避免 UI 和 notebook 各写一套写 log 的逻辑 |

---

## Part 3：最终对比输出 —— `eval/reports/comparison_evaluation.ipynb`

这是消费 Part 1（35 条分类问题）+ Part 2（log 里的 `usage`/`metrics`/`question_id`/`question_meta` 字段）的下一阶段产出，按反馈把结构定下来：notebook 是"对比实验"本身的执行和展示载体，不是简单读现成 log 的分析脚本——它自己先把全部问题跑一遍。

### 整体结构（4 段，按反馈的顺序）

**第 1 段 —— 批量跑一遍全部问题，存到本地**

- 读 `eval/questions.json`（Part 1 改完后的 35 条）
- 跑之前生成一个 `run_batch_id`（本次 notebook 执行的 ISO 时间戳即可），依次对每条问题跑两条 pipeline：直接 import `ui/adapters/pipeline_flatrag_adapter.FlatRAGAdapter` 和 `ui/adapters/pipeline_grag_v2_adapter.GRAGv2Adapter`（跟 `ui/app.py` 用的是同一套 adapter，不重新实现调用逻辑），每条结果调用 `eval/comparison_lib._write_run_log()` 写入 `eval/comparison_runs/`，并在 log 顶层写入这个 `run_batch_id`（沿用 Part 2 定的字段结构：`question_id`/`question_meta`/`usage`/`metrics`/`schema_version`）
- 开跑前先按 `run_batch_id` 扫一遍 `eval/comparison_runs/`，跳过本次 batch 里已经成功写过 log 的 `question_id` + pipeline 组合，只补跑缺的部分——35×2 条、每条可能多轮 LLM 调用，一次完整跑下来是几百次调用量级，notebook 中途因超时/限流失败要按正常情况预留，不该导致已经花掉的调用全部作废；这个跳过逻辑只认本次 `run_batch_id`，不会和历史 `pipeline_grag`/旧版本的 run 文件混在一起
- 这一批结果同时保留在 notebook 内存里（一个 list），后面几段直接用这份内存数据做聚合，不用再从磁盘按时间戳筛选去重
- 单条 pipeline 调用失败时（`error` 字段非空）保留该行、后续统计按 `error is not None` 过滤掉，不让单条失败中断整个批跑，也不让后面几段的聚合代码因为缺字段而报错

**第 2 段 —— 按问题类型（`type`）统计两条 pipeline 的指标**

- 对第 1 段的批量结果打分，按 type 分两条路径，不再对全部 8 个 type 值套用同一套 6 指标：
  - `single_fact`/`multi_fact`/`table`/`accordion`/`cross_page`/`comparative`（30 条）：补齐标准 6 个 RAGAS 指标（Context Precision / Context Recall / Context Relevancy / Faithfulness / Answer Relevancy / Answer Correctness，用 `reference_answer` 做参照）——这里才是 RAGAS/LLM-judge 真正跑的地方
  - `out_of_scope`/`missing_info`（5 条）：不套用标准 6 指标。这两类题目本来就没有正向证据可检索，Context Precision/Recall 在没有 gold context 的情况下没有意义，硬套只会产出误导性的分数。改成一个确定性判定，不需要额外 LLM 调用：检查回答是否命中 agent 设计里固定的两句拒答话术之一（"I can only answer questions about..." 对应 `out_of_scope`；"I couldn't find relevant information..." 对应 `missing_info`），分类为「正确拒答」「拒答但话术类型用错」「没有拒答、编了内容」三种之一
  - 两条路径的打分结果落进同一张打平的中间表：标准 6 指标那 30 行填 6 个分数列，边界 5 行改填 `refusal_correct` 分类列，两边互相留空——仍然是"一张表"，只是不同 type 填充不同列
  - 这张打平表打完分之后落盘缓存一份（比如 `eval/reports/scored_results_<run_batch_id>.csv`），后面调图表/文字不用重新触发 RAGAS/judge 调用
- 加上已经现成的 `time_sec`/`usage.total_tokens`/`metrics.tool_calls`/`metrics.loop_count`/`metrics.evidence_count`
- 按 `question_meta.type` 分组，两条 pipeline 并排聚合成两张表：标准 6 指标的 6 行（`single_fact`/`multi_fact`/`table`/`accordion`/`cross_page`/`comparative`）+ 边界题拒答正确率的 2 行（`out_of_scope`/`missing_info`）；每一行都带上样本量 n
- 表格下面接一个 markdown cell，写这一段的统计总结（哪条 pipeline 在哪类问题上明显更强/更弱，是否符合 README 开头提的"GRAG 应该在 accordion/table/cross_page 上更强"的预期）；n < 5 的分组（`table`/`accordion`/`cross_page`/`comparative` 拆到具体 type 基本都是这个量级）在总结里明确标注为方向性观察，不作为结论下判断

**第 3 段 —— 按难度整体统计**

- 同一批已经打好分的结果，改成按 `question_meta.difficulty`（`easy`/`medium`/`hard`）分组；标准 6 指标只对有分数的 30 条（非 `out_of_scope`/`missing_info`）取均值，边界题不能因为没有 RAGAS 分数就整行从统计里消失，单独在同一张表里加一列"该难度下的拒答正确率"
- 表格下面同样接一个 markdown cell 写统计总结（比如难度越高，延迟/token 差距是否被拉大，哪条 pipeline 在 hard 题上退化更明显），同样标注每个难度桶的 n（大约 14/14/7）

**第 4 段 —— 商业性表现：各自最适合的场景和失败案例**

- 沿用第 2 段已经打平的中间结果，仅在有标准 6 指标的 30 条范围内（边界题的失败模式是分类型的，不适合和连续质量分混排，单独处理见下）按一个综合质量分（比如 Faithfulness / Answer Relevancy / Answer Correctness 的均值，具体加权方式留到实现时定）排序
- **对两条 pipeline 分别**取自己排名最高的 7 条和最低的 7 条问题（每条 pipeline 各 top 7 + bottom 7），列成表格：问题文本、`type`、`difficulty`、综合质量分、关键指标（比如 Answer Correctness、latency）
- 额外加一张**分歧表**：按 `|综合质量分_flatrag − 综合质量分_grag_v2|` 排序取分差最大的若干条——两条 pipeline 各自的 top/bottom 7 大概率高度重合（回答的是同一批题，难易程度本身相关），分歧表才是直接回答"两条 pipeline 谁在什么场景上更强"这个比较型问题的地方
- 边界题（`out_of_scope`/`missing_info`）单独一张小表：两条 pipeline 各自的拒答正确率，以及"一条 pipeline 正确拒答、另一条编了答案"这类分歧 case，不并入上面的 top7/bottom7/分歧表
- 表格下面接一个 markdown cell，分析这些 case 里有没有共同模式——比如某条 pipeline 的 bottom 7 是不是集中在某个 `type`（例如 GRAG v2 在 `out_of_scope` 上失分，或 FlatRAG 在 `accordion` 上失分），从而具体回答"FlatRAG、GRAG v2 各自最适合的场景和失败案例是什么"

### 关键设计点

- **第 2、3、4 段共用同一份"已打分"的中间结果**（一个打平的表格：每行 = 一条 run 在一条 pipeline 下的一套指标 + `type` + `difficulty`），只是分组/排序维度不同，避免重复计算或重复调用 RAGAS；这份打平表本身也落盘缓存（见第 2 段）。
- notebook 依赖 `eval/comparison_lib.py`（新增，见上表）和两个 adapter，不依赖 Streamlit，可以独立用 `jupyter nbconvert --execute` 跑。
- RAGAS/LLM-judge 打分调用固定 `temperature=0`，保证同一批 log 重新跑评分能拿到同样的数字，避免"第一版对比报告"里的数字随 kernel 重跑漂移。
- markdown 总结先由代码把关键数字（比如"GRAG v2 在 accordion 类问题上的 Context Recall 比 FlatRAG 高 X pp"）格式化好、打印成一段草稿文字，再手动过一遍改成最终叙述——不要求全自动生成不可编辑的文本。

---

## 验证方式

1. `questions.json` 改完后：`python -c "import json; json.load(open('eval/questions.json', encoding='utf-8'))"` 确认合法 JSON；重新跑一遍分布统计脚本，确认每个 type/difficulty 数量符合 Part 1 的目标表格，且没有任何一条 `category=capstone` 或 `type=image_only`。
2. log 结构改完后：`streamlit run ui/app.py`，对至少一条新增的 `accordion`、一条 `table`、一条 `out_of_scope` 问题各跑一次 "Compare"，检查生成的 `eval/comparison_runs/*.json` 里 `question_id`/`question_meta`/`usage`/`metrics` 是否都正确出现、两条 pipeline 的 `metrics` key 集合是否一致；确认旧的 28 个历史文件没有被覆盖/损坏，仍能正常 `json.load`。
3. notebook 搭好后：`jupyter nbconvert --to notebook --execute eval/reports/comparison_evaluation.ipynb` 能跑通不报错；确认第 1 段产出的 35×2 条新 run 文件都带 `schema_version:2` 和同一个 `run_batch_id`；中途手动 kill 一次 kernel 重跑，确认第 1 段能跳过本次 batch 里已完成的 `question_id` + pipeline 组合而不是全部重来；确认第 2 段落盘的打分缓存文件存在且能被重新读取；确认第 2 段标准指标表恰好有 6 行（带 n 列）、边界题拒答正确率表恰好有 2 行；第 3 段按 difficulty 表格恰好有 3 行且带 n 和拒答正确率列；第 4 段两条 pipeline 各自的 top 7/bottom 7 表格行数正确（各 14 行，取自有标准指标的 30 条范围内）、分歧表和边界题小表均非空；确认第 2、3、4 段下面各自都有一个非空的 markdown 总结 cell。

本 plan 不涉及：`comparison_summary.md` 单独生成逻辑、RAGAS 具体打分 prompt/library 选型的实现细节——notebook 的整体结构已经在 Part 3 定下来，实现层面的细节属于下一步动手阶段。README「目标产出」只同步删除 gold URL 命中率这一项。
