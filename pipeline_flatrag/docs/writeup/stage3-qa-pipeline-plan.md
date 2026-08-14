# Stage 3 规划：QA 生成框架

## Context

Stage 2 检索已完成，固定使用 bgesmall_size_1536 collection。  
基于 URL 的自动指标（Hit@5、MRR）仅作参考，**人工评判显示召回中存在噪音**（不相关 chunk 被混入）。检索质量问题同时存在于：（1）排名——query 为疑问句而 DB 存陈述句导致语义 gap；（2）精度——program_type 路由粒度太粗，大量不相关页面的 chunk 被一并召回。  

Stage 3 目标：
1. Query 改写：疑问句 → 陈述句，改善向量匹配
2. **页面级精准路由**：用预构建的页面摘要索引（约15个页面组）让 LLM 先定位相关页面，再在页面范围内检索，从源头降噪
3. 相关性过滤 + 重试：若第一轮结果仍无相关 chunk，最多重试3次换其他页面
4. LLM 生成：引用、拒答、时间敏感 disclaimer、Career 图片限制说明

---

## 设计决策

### 放弃 query_router.py，改用 Page Selector

**原 query_router 的问题**：只能区分 in_person / online / general，过滤能力有限。问 in-person 核心课程时，依然会召回几十个 general chunks，其中大量是不相关页面。

**新方案**：预构建 `src/qa/page_summaries.json`，包含约15个页面组，每组有 1-2 句描述 + URL 列表。LLM 读取此摘要，选出 2-3 个最相关页面组，再用 ChromaDB `$in` filter 限定检索范围。

页面组规划（约15个）：
| 页面组 label | 涵盖内容（page_summaries.json 描述要点） |
|---|---|
| main | 项目整体介绍、核心价值主张、项目类型概览 |
| in_person_program | In-Person 项目详情：1年轨道（夏季密集+秋/冬/春学季）与2年轨道的结构差异；必修课 (Data Engineering Platforms, Machine Learning, Leadership)、选修方向、时间表、校园学习体验 |
| online_program | Online 项目详情：在线课程设置、学季安排、异步/同步学习方式、与 In-Person 的区别 |
| course_progressions | 每个学季的具体课程列表（按轨道/学季排布）；核心课程和选修课程的具体名称和顺序 |
| faqs | 涵盖学生可能问到的所有常见问题，包括但不限于：录取标准、背景要求、奖学金申请、住宿安排、项目差异（1年/2年/在线）、职业支持、课程负担、实习政策、毕业要求，以及任何其他项目相关问题 |
| how_to_apply | 申请流程、所需材料（成绩单/推荐信/文书）、申请轮次截止日期 |
| tuition_fees_aid | 学费标准（per-course、每学季、全程总费用）、经济援助形式、奖学金类型和金额 |
| events_deadlines | 招生说明会日期、信息会议、申请截止日期（time-sensitive） |
| career_outcomes | 就业数据概览（注：雇主名单以图片形式呈现，文字内容约250词）、职业支持服务 |
| capstone | 顶点项目介绍、要求、历年项目存档概览 |
| instructors_staff | 教师和教职员工列表、所在分组 |
| faculty_profiles | 约50位教师/staff的个人简介（研究方向、学术背景、行业经验）|
| our_students | 在读学生背景、学生故事 |
| other | 项目其他页面（关于项目、新闻、合作等）|

**page_summaries.json 结构：**
```json
[
  {
    "label": "faqs",
    "description": "Frequently asked questions covering admissions, prerequisites, scholarships, housing, program differences (in-person vs online), and student life.",
    "urls": ["https://datascience.uchicago.edu/.../faqs/"]
  },
  ...
]
```

### 不引入 Reranker

Query Rewriting + Page Selector 的组合已从两个维度减少噪音，reranker 解决相同问题且引入新模型，跳过。

### 工业级做法参考

BM25 + dense hybrid + reranker + contextual compression 是典型工业链路。本项目53页规模，Page Selector 已达到类似的精准路由效果，但实现更简单、更易在 writeup 中说明设计选择。

---

## 实现方案

### 文件结构

```
src/qa/
├── page_summaries.json  # 页面组摘要索引（新建，静态文件）
├── page_selector.py     # LLM 页面选择器（新建，替代 query_router）
├── qa_pipeline.py       # 主流水线（新建）
├── prompt_templates.py  # Prompt 模板（新建）
└── demo.py              # 独立可运行 demo（新建）

tests/
└── validate_stage_3.py  # 验证脚本（新建）
```

复用（需确认接口兼容性）：
- `src/retrieval/retriever.py` — **需小改**：当前 `where` 参数支持 `{"program_type": {"$ne": ...}}`（`$ne` 操作符），新 pipeline 改为 `{"source_url": {"$in": [...]}}`（`$in` 操作符）。两者均为 ChromaDB 标准 where 语法，接口本身不变，但需在实现前用 ChromaDB 1.5.5 验证 `$in` 对字符串字段的支持，并在 `retriever.py` 顶部注释中说明支持的 filter 格式。
- `src/retrieval/query_router.py` — **不再在主流水线中使用**，保留文件不删除（Stage 2 eval 依赖它）

---

### Pipeline 完整流程

```
用户 query
  ↓
① Query Rewriting（1次 LLM 调用）
    Prompt: "Rewrite this question as a factual declarative statement. Do not add any assumptions."
    → rewritten_query（用于向量检索，比疑问句更贴近 DB 陈述文本）
  ↓
② Page Selector（1次 LLM 调用）
    输入：rewritten_query + page_summaries.json 全部条目
    Prompt: "Select the 2-3 most relevant page groups for this query. Return their labels as JSON list."
    → selected_labels → 展开为 selected_urls（URL 列表）
  ↓
③ 检索 + 质量判断循环（最多3次）
    状态：accumulated_chunks = []，attempt = 1，tried_labels = initial_labels
    completeness = "none"

    while attempt ≤ 3:
      a. Retriever.retrieve(rewritten_query, top_k=7,
                            where={"source_url": {"$in": selected_urls}})
         → new_chunks（7个候选）
      b. LLM 质量判断（1次 LLM 调用，核心设计）：
            输入：
              - 原始 query
              - new_chunks（附带 index 0-6）
              - accumulated_chunks（前几轮已保留的内容，可能为空）
            Prompt:
              "Given the query and the retrieved chunks, judge:
               1. completeness: 'full' (new chunks + accumulated fully answer the query),
                                 'partial' (partially answer, some info present),
                                 'none' (no useful information found)
               2. useful_indices: list of chunk indices from new_chunks that contain useful info
               Return JSON: {completeness: ..., useful_indices: [...]}"
            → completeness，useful_indices
      c. accumulated_chunks += [new_chunks[i] for i in useful_indices]
      d. 退出条件：
            if completeness == "full": break
            if completeness == "partial" and len(accumulated_chunks) ≥ 2: break
            （否则：completeness=="none" 或 partial 但 accumulated 不够）
      e. 换页重试：
            LLM 从 page_summaries 重新选页面（排除 tried_labels）
            tried_labels += new_labels；attempt += 1；更新 selected_urls

    最终判断：
      if len(accumulated_chunks) == 0:
          low_confidence = True
          context_chunks = new_chunks（用最后一轮的全部7个）
          confidence_note = "NOTE: After 3 retrieval attempts, no strongly relevant content was found. This answer may be incomplete or inaccurate."
      else:
          low_confidence = (completeness == "partial")
          context_chunks = accumulated_chunks（跨轮次累积的相关 chunk）
          confidence_note = "" if completeness == "full" else "NOTE: Retrieved information may only partially address the question."
  ↓
④ Context 组装
    每个 context_chunk 格式化：
      [来源：{page_title} | {section_breadcrumb}]
      {text}
    若任意 chunk.content_type == "time_sensitive"，在其文本前加 [TIME-SENSITIVE]
    context_str = 所有格式化块拼接
    user_prompt_prefix = confidence_note（空字符串或 NOTE:... 提示）
  ↓
⑤ LLM 答案生成（1次 LLM 调用）
    system prompt（见下方）+ context_str + confidence_note + 原始 query
  ↓
⑥ 返回 dict：
    {
      "answer": str,
      "sources": [{"page_title": ..., "source_url": ...}],  # deduplicated
      "low_confidence": bool,
      "completeness": "full" | "partial" | "none",
      "attempts": int
    }
```

**每次查询 LLM 调用次数**：
- 正常路径（1次，fully answered）：1（改写）+ 1（页面选）+ 1（质量判断）+ 1（生成）= **4次**
- 最坏路径（3次均失败）：1 + 1 + 3（质量判断）+ 最多2次换页选择 + 1（生成）= **最多8次**

---

### page_selector.py

```python
class PageSelector:
    def __init__(self, summaries_path):
        # load page_summaries.json
    
    def select(self, query: str, exclude_labels: list[str] = []) -> tuple[list[str], list[str]]:
        # LLM call → returns (selected_labels, selected_urls)
        # exclude_labels: 已试过的页面组，不再选
    
    def urls_for_labels(self, labels: list[str]) -> list[str]:
        # 展开 label → URL 列表
```

---

### prompt_templates.py：系统 Prompt 设计

**System prompt 规则：**
1. **范围限定**：只基于提供的 context 作答；问题与 MSADS 项目无关时，明确拒绝
2. **拒答（通用）**：context 不含足够信息时，回复固定措辞：  
   "I don't have enough information about that based on the available program materials."
3. **拒答（Career 特殊）**：涉及雇主名单、校友就业公司、实习企业时，主动说明：  
   "The Career Outcomes page primarily presents employer and alumni data as logo images, which this system cannot access. For detailed outcomes, please visit: https://datascience.uchicago.edu/education/masters-programs/ms-in-applied-data-science/career-outcomes/"  
   可以补充 context 中有的文字信息，但不捏造具体公司名。
4. **时间敏感**：context 中出现 `[TIME-SENSITIVE]` 标记时，在对应答案段落后加：  
   "Note: This information reflects the program website as scraped on [scraped_at]. Please verify current deadlines directly."
5. **引用格式**：每条答案结尾 `Source: [page_title](source_url)`，多来源则多行
6. **PII 保护**：不输出电话、地址（已在数据层 redact，此为保险层）

---

### validate_stage_3.py

7项检查：
1. 导入 `QAPipeline` 不报错
2. 对 "What is the capital of France?"（off-topic）→ 拒绝回答
3. 对 Q10（着装要求，无 ground truth）→ 拒答，不捏造
4. 对 Q02（截止日期）→ 答案包含 `[TIME-SENSITIVE]` 相关 disclaimer
5. 对 Q04（毕业设计）→ sources 中包含 `capstone` URL
6. 对 "Which companies hire MSADS graduates?" → 答案包含 career outcomes URL 引导
7. pipeline 返回值格式正确（dict 含 `answer`、`sources`、`low_confidence`、`attempts`）

---

### demo.py

交互式命令行，显示 answer + sources + attempt 次数：
```
Question: What are the core courses for in-person students?
[Attempt 1/3] Selecting pages: in-person-program, course-progressions
[Filtering 7 chunks → 4 relevant]
Answer: ...
Sources: In-Person Program | https://...
         Course Progressions | https://...
```

---

## 关于 Top-k

初始检索 **top_k=7**，LLM 过滤后保留 ≤5 个 chunk 进入答案生成。  
页面级 `$in` 过滤已大幅降低噪音，7个候选量足够在过滤后剩余充足相关内容。

---

## 不在 Stage 3 范围内

- eval/questions.json 扩展到 20 题 → Stage 5
- LLM-as-judge 打分 → Stage 5  
- Streamlit UI（多轮对话） → Stage 4
- 无限 ReAct 循环 → 不做（固定 max_attempts=3 已够用）

---

## 验证方式（End-to-End）

```bash
conda activate adsp-nlp-backup
python src/qa/demo.py                  # 手动测试5个问题，检查答案质量、引用、disclaimer
python tests/validate_stage_3.py      # 7项自动检查，全部 PASS
```

手动核查清单：
- [ ] off-topic 拒绝
- [ ] Q10 拒答（无捏造）
- [ ] Q02 含时间敏感 disclaimer
- [ ] Career 问题含图片限制说明 + URL
- [ ] 引用格式正确（含 source URL）
- [ ] 答案无 hallucination（可在 context 中找到原文依据）
- [ ] 重试机制至少手动触发一次验证（选一个不在任何页面组的问题）
