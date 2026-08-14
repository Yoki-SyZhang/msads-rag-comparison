 

# 本实验在做什么

RAG 问答系统对比实验 —— 用 UChicago MS in Applied Data Science 官网作为同一知识域，构建并比较两条 RAG 路线：

* **FlatRAG** ：把网页清理成带 section metadata 的扁平 chunks，存入 ChromaDB，以向量检索为核心，再由手写 DeepSeek agent 选择检索工具、判断证据并生成答案。
* **GRAG v2** ：保留网页 DOM 层级，把页面构造成 DOM-aware 知识图谱；同时建立向量、BM25 和图结构索引，agent 以图导航（`inspect_page`/`fetch_graph_content`）为主循环，混合检索只作为熔断后的一次性兜底。

研究重点不是把两个项目重构成一个系统，而是：

> 保留两套系统各自的 indexing、retrieval 和 agent 设计，只在外层建立统一 adapter、UI、问题集和日志，以比较架构本身的表现。

希望回答的核心问题是：

* FlatRAG 是否在单页、直接事实问题上更快、更稳定？
* GRAG 是否在 accordion、tab、课程层级和跨 section 问题上更强？
* 图结构带来的质量提升，是否值得它增加的延迟、复杂度和失败面？
* 两者的引用质量、证据路径、低置信度行为有什么差异？

### Comparison Rules：

- 为了控制变量，当前比较路径统一使用 `deepseek-chat`
- Even if the two pipeline URL configs are not unified, Evaluation questions should only cover URLs present in both pipelines' indexed corpora.

### Run The Comparison UI

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY="your_deepseek_api_key"
streamlit run ui/app.py
```

On Windows PowerShell:

```powershell
pip install -r requirements.txt
$env:DEEPSEEK_API_KEY="your_deepseek_api_key"
streamlit run ui/app.py
```

The UI writes normalized comparison logs to:

```text
eval/comparison_runs/
```

### Project Layout

```text
pipeline_flatrag/        # Existing FlatRAG project
pipeline_grag_v2/        # Existing GRAG project, graph-navigation-first agent on DeepSeek
ui/app.py                # Streamlit side-by-side comparison UI
ui/adapters/             # Output-normalizing adapters
eval/questions.json      # Shared question set converted from FlatRAG retrieval tests
eval/comparison_runs/    # Generated comparison logs
comparision_plan.md      # Current implementation plan
```

---

## 目标产出

用全部统一问题分别运行两条 pipeline，自动汇总指标，并产出第一版`eval/reports/comparison_summary.md`

具体应包括：

* 模型本身：

  - 平均latency

  * 平均cost
* 模型功能性表现：

  * 过程：
    * tool-call 数
    * loop 数
    * evidence 数
  * 结果(RAGAS指标)：
    * 对召回的评价：

      * Context Precision 检索出来的文档是否大多是相关的（少噪音)
      * Context Recall 检索出来的文档是否覆盖了回答所需的信息
      * Context Relevancy 检索内容和问题是否相关
    * 对LLM生成的评价：

      * Faithfulness 回答是否忠于检索内容（是否幻觉）
      * Answer Relevancy 回答是否真正回答了用户问题
      * Answer Correctness 回答是否正确（通常需要 Ground Truth）
* 按问题类型分析(`eval\questions.json`应该涉及如下范畴)：

  * 单一事实 `single_fact`
  * 多部分问题 `multi_fact`
  * 结构化页面 `table` / `accordion`
  * 跨页综合 `cross_page` / `comparative`
  * 无答案/边界问题 `out_of_scope` / `missing_info`
* 商业性表现：FlatRAG、GRAG 各自最适合的场景和失败案例。

---



# 两种 RAG 模式的详细对比

### 总览

**FlatRAG**

```
网页
→ requests/少量 Playwright
→ HTML 清理和 section 提取
→ section 内字符切块
→ contextual prefix + embedding
→ ChromaDB
→ query rewrite
→ agent 调用结构浏览/section fetch/vector search
→ LLM judge 筛选 chunks
→ 证据拼接
→ DeepSeek 生成带引用答案
```

**GRAG v2**

```
网页
→ requests（代码保留 Playwright 能力，但当前默认集合不触发）
→ 解析 DOM 组件和层级
→ Page/Section/Accordion/Tab/Course 等 KG 节点
→ 节点附属 character chunks（900 chars/90 overlap）
→ Chroma + BM25 + knowledge graph
→ query rewrite
→ page summary 常驻 system prompt 作为长期记忆（不是工具调用）
→ agent 图导航循环：inspect_page ⇄ fetch_graph_content
→ LLM judge 逐 node 筛选 chunk/结构证据，Summarizer 每轮判断是否 sufficient
→ 仍不足且触发熔断条件时：一次性 scoped 混合检索兜底
→ DeepSeek 生成带引用答案
```

两边都有手写 agent loop。真正区别是：

* FlatRAG 的结构主要存在于 **chunk metadata** 中。
* GRAG v2 的结构是可以独立导航和检索的 **一等图节点与边**，agent 以图导航为主循环，混合检索退居为熔断后的一次性兜底手段。

### 网页抓取与文本清理

**FlatRAG**：

> 扁平化 -> 需要了解每页结构针对处理，适合少量文本；规则大量绑定当前 DOM，维护成本较高，特殊页面需要不断补专用解析逻辑。

```
Page
└── Section
    ├── section
    ├── section_breadcrumb
    ├── hierarchy
    ├── content_type
    └── text
```

* 清理逻辑：平成 section 序列，需要为每种特殊页面单独写一个 `_clean_xxx` 函数，否则特殊结构（如表格转 Markdown，提取 `datavizData_*` 图表 JSON，不同嵌套展开逻辑的accordion不同处理）
* 网页结构编码：遍历 DOM，按标题层级（h1-h4）生成**metadata**如 `section_breadcrumb`，`content_type`。结构化数据如accordian走专门的 JSON→**自然语言转换**->保留可读性。
* chunk 化：优先段落/句子切分，绝不跨 section。太短的 split 会合并到相邻 chunk。chunk_size由实验确定，最终 chunk 是纯粹的"文本 + 扁平 metadata"记录,存进 ChromaDB。

**GRAG v2：**

> 结构化 -> **泛化性更好，方便debug。**但递归解析逻辑本身更复杂，需要思考“以什么结构保存通用性强+方便模型理解”

```
Page
└── Section
    ├── Accordion
    │   └── AccordionItem
    │       └── Chunk
    ├── ProgressionTab
    │   └── Quarter
    │       └── CourseGroup
    │           └── CourseItem
    │               └── Chunk
    └── Table
        ├── TableRow
        └── Chunk
```

* 清理逻辑：直接把 DOM 结构解析成**图节点**，保留完整树形结构——`parse_element` 递归识别各种组件类型，用一套通用的组件识别逻辑（`is_accordion`/`is_tab_group`/`is_people_directory`…)统一处理
* 网页结构：每个组件对应一个节点类型，把父子关系写成图的边
  * 可视化->检查结构拆解情况，方便debug
  * 输出了KG page structure tree
    ```
    Program: MS in Applied Data Science
    └── HAS_PAGE → Page: Course Progressions
        ├── HAS_SECTION → Section: Curriculum Overview
        │   └── HAS_CONTENT → Chunk: ...
        │
        └── HAS_TAB_GROUP → ProgressionTabs: Course Progression Tabs
            └── HAS_TAB → ProgressionTab: Sample Full-Time Schedule
                ├── HAS_CONTENT → progression_tab_index chunk
                │
                └── HAS_QUARTER → Quarter: Quarter 1 – 10 Weeks
                    ├── HAS_CONTENT → quarter_index chunk
                    │
                    └── HAS_COURSE_GROUP → CourseGroup: Core
                        ├── HAS_CONTENT → course_group_index chunk
                        │
                        └── HAS_COURSE → CourseItem: Statistical Models
                            └── HAS_CONTENT → course_description chunk
    ```
* chunk 化：chunk (900 字符、90 字符重叠，与 FlatRAG 同一套字符级切块单位) 是图的**叶子节点**挂在某个结构节点下面，每个 chunk 保留 `path`。
  * GRAG 会额外生成"索引型" chunk——即"这个 accordion 包含哪些子项"的摘要文本，供检索时能命中"目录级"问题。（`knowledge_graph.json`Chunk 作为图节点存在+`chunks.json`Chunk 作为扁平检索记录存在）
    它们既参与 hybrid retrieval，也能被 `fetch_node_chunks` 沿图取回。
  * | 类型                      | 挂载节点       | 内容                                   |
    | ------------------------- | -------------- | -------------------------------------- |
    | `accordion_index`       | Accordion      | accordion 包含哪些 item                |
    | `progression_tab_index` | ProgressionTab | schedule/tab 包含哪些 quarter          |
    | `quarter_index`         | Quarter        | quarter 包含哪些 course groups/courses |
    | `course_group_index`    | CourseGroup    | group 包含哪些 courses                 |

### 存储的数据结构

**FlatRAG**：

每个chunks在ChromaDB record 是 text + page metadata + section metadata + content metadata

```
id:
    chunk_id

document:
    [Page: page_title | Section: section_breadcrumb]
    chunk text

embedding:
    BGE-small 384-dimensional vector

metadata:
    source_url
    page_title
    section
    section_breadcrumb
    content_type
    page_class
    program_type
    scraped_at

 {
  "chunk_id": "education__...__application_process__1",
  "page_title": "Faqs",
  "section_breadcrumb": "Faqs - Accordion Overview > Application Process",
  "text": "Application Process: ...",
  "source_url": "https://.../",
  "content_type": "accordion_overview",
  "page_class": "must",
  "scraped_at": "2026-04-25T...",
  "program_type": "general"
}
```

`page_summaries.json`

```
[
  {
    "label": "how_to_apply",
    "description": "Application requirements, materials and process...",
    "urls": [
      "https://.../how-to-apply/"
    ]
  },
  {
    "label": "faqs",
    "description": "Frequently asked questions...",
    "urls": [
      "https://.../faqs/"
    ]
  }
]
```

**GRAG v2：**

`knowledge_graph.json`

```
{
  "nodes": [...],
  "edges": [...]
}

Program node
{
  "id": "program:msads",
  "type": "Program",
  "label": "MS in Applied Data Science",
  "acronym": "MSADS"
}

Page node
{
  "id": "page:fe9d95e97a7ee76f",
  "type": "Page",
  "label": "In-Person Program",
  "url": "https://...",
  "raw_file": "raw/education__...json",
  "page_class": "must",
  "fetched_at": "...",
  "aliases": [...]
}

Section node
{
  "id": "section:f9ec3a741e1e38ce",
  "type": "Section",
  "label": "Tailor Your Data Science Journey",
  "heading_tag": "h2",
  "url": "https://...",
  "page_title": "In-Person Program",
  "path": [
    "In-Person Program",
    "Tailor Your Data Science Journey"
  ]
}

Accordion item node(还有其他特殊结构node)
{
  "id": "accordion_item:<hash>",
  "type": "AccordionItem",
  "label": "Is the GRE or GMAT required?",
  "url": "https://...",
  "page_title": "FAQs",
  "path": [
    "FAQs",
    "Application Process",
    "Is the GRE or GMAT required?"
  ]
}

Chunk node
{
  "id": "chunk:8d242380ff356d48",
  "type": "Chunk",
  "label": "The In-Person MS in Applied Data Science...",
  "text": "...",
  "url": "https://...",
  "page_title": "In-Person Program",
  "path": [
    "In-Person Program",
    "Tailor Your Data Science Journey"
  ],
  "source_type": "p"
}

Edge
{
  "source": "program:msads",
  "relation": "HAS_PAGE",
  "target": "page:fe9d95e97a7ee76f"
}
HAS_PAGE
HAS_SECTION
HAS_TAB_GROUP
HAS_TAB
HAS_QUARTER
HAS_COURSE_GROUP
HAS_COURSE
HAS_ACCORDION
HAS_ACCORDION_ITEM
HAS_ROW
HAS_CONTENT
```

Graph RAG 检索仍需要一份扁平 chunks 列表 存入ChromaDB ：

```
id:
    chunk:<hash>

document used for embedding:
    path joined with " | "
    +
    chunk text

embedding:
    BGE-small 384-dimensional vector

metadata:
    chunk/page/path/source_type information

chunks.json
{
    "id": "chunk:8d242380ff356d48",
    "owner_node_id": "section:f9ec3a741e1e38ce", # owner_node_id 记录这个 chunk 直接挂在图里的哪个结构节点下，供按节点分组取回内容时使用。
    "text": "...",
    "url": "https://...",
    "page_id": "page:fe9d95e97a7ee76f",
    "page_title": "In-Person Program",
    "path": [
      "In-Person Program",
      "Tailor Your Data Science Journey"
    ],
    "source_type": "p"
  }


索引型 chunks
{
  "id": "chunk:<hash>",
  "text": "This accordion contains: Question A; Question B; Question C",
  "source_type": "accordion_index",
  "path": ["FAQs", "Application Process"]
}
```

BM25 index：`bm25.pkl`

`page_summaries.json`

### 向量索引

**FlatRAG**：

> 纯向量相似度`top_k=7` + `program_type`（in-person/online/general）粗过滤

* 单一向量库——ChromaDB + `BAAI/bge-small-en-v1.5`
* 支持 metadata `where` 过滤（`$eq`/`$in`/`$and`）

**GRAG v2：**

> 四路融合分数加权求和（`vector_weight=0.50, keyword_weight=0.30, graph_weight=0.20`）：
>
> * 向量相似度（Chroma）
> * BM25 关键词分数
> * **图分数** （`graph_scores`：从 chunk 节点沿父边网络逐层向上传播权重 0.72 衰减，看祖先节点 label/path 是否命中 query 词；chunk 自身 path 命中 query 词额外加成）
> * **意图 boost** （`intent_boosts`：针对 deadline/tuition/core-courses/visa-opt-stem 等关键词的手写规则加分，例如命中日期格式的 deadline 文本额外 +0.12）

- 主循环里 agent 不直接调用这套融合检索——它只在图导航熔断后的兜底阶段被调用一次，此时额外支持一个 `page_ids` 过滤参数，把候选集限定在决策 agent 选出的 ≤5 个 page 范围内。
- 优点：多信号融合弥补
- 缺点：多种信号发生错误时更难定位，运维和 index rebuild 成本高

### Agent / LLM 设计与 tool call loop

> 共有的设计：
>
> * 手写 agent，不使用 LangChain agent。
> * 先做 query rewrite。
> * 支持把 compound question 拆成多个 retrieval query。
> * 最多 9 步的 tool loop。
> * 工具结果不会自动全部进入最终上下文。由独立 LLM judge 决定保留哪些证据。
> * 最终再进行一次 answer generation。要求答案基于证据并提供引用。

#### **FlatRAG agent：**

```
判断复合问题拆子问题
→ rewrite query
→ agent loop （最多 9 步，3 个工具：list_page_sections/fetch_section_chunks/semantic_search）
	→ agent 选择一个或多个工具
	→ 得到 candidate chunks
	→ judge 判断 complete/partial/insufficient + 返回 useful_indices
	→ 将有用 chunk 加入累积 useful_chunks 
→ judge 判断 complete 或达到 partial 证据数阈值时停止，否则最多 9 步
→ useful_chunks 合并去重后一次性生成答案
```

**Agent-loop Prompt:**

* system_prompt：
  ```
  You are a retrieval agent for the UChicago MSADS program knowledge base.

  Your task:
  Gather the most relevant text chunks to answer the user's question.

  Available page groups:
  - "how_to_apply": {description}
  - "faqs": {description}
  - "course_progressions": {description}
  - ...

  Strategy:
  1. Call list_page_sections on the 1–2 most likely pages.
  2. Call fetch_section_chunks on the most relevant section.
  3. After each fetch/search, a judger evaluates candidates.
  4. If insufficient, call semantic_search with include_urls.
  5. As a last resort, search broadly with exclusions.
  6. If the judger says DONE, exit.

  Rules:
  - Maximum 9 tool-call rounds.
  - Do not repeat the same section or search.
  - Use exclude_chunk_ids to avoid duplicates.
  ```
* user_prompt：
  * Compound-question detection prompt
  * Query rewrite prompt
  * evidence-judge prompt
  * final-generation system prompt
* message history

**3个工具(代码支持一轮多个 tool calls)：**

* `list_page_sections(url)`
  * shows all section_breadcrumbs + chunk counts for a page
  * No retrieval; used to understand page structure before targeting a section
* `fetch_section_chunks(url, section_breadcrumb)`
  * 是exact ChromaDB metadata lookup：根据 URL 和 breadcrumb 获取 section 下的全部 chunk。
  * 支持大小写和 fuzzy match (if LLM provides a slightly wrong name)。
* `semantic_search(query, include_urls, exclude_urls, exclude_chunk_ids, top_k)`
  * Chroma vector search。
  * 可 include/exclude URL 和 chunk ID。

**页面总览 Page_summaries.json 使用：**

* 构建 FlatRAG agent 的 system prompt，要求 agent 先对最可能的 1–2 个页面调用 list_page_sections，再 fetch_section_chunks

**答案生成：区分"完全无关"和"有关但没覆盖"：**

最终生成答案时有两条兜底规则：

* 问题完全跟项目无关（通用知识、其它学校、做饭天气这类）——固定回复 "I can only answer questions about the UChicago MS in Applied Data Science program."
* 问题跟项目有关，但检索到的内容不足以回答——换成另一句话："I couldn't find relevant information about this in the available program materials. I can only answer questions about the UChicago MS in Applied Data Science program."

#### **GRAG v2 agent：**

```
判断复合问题拆子问题
→ rewrite query
→ page summaries 一次性分配 P ref，常驻 system prompt 作为长期记忆（不是工具调用）
→ 决策循环（最多 9 步，2 个工具：inspect_page / fetch_graph_content，没有 finish）
	→ agent 选择一个动作
	→ inspect_page：渲染页面结构树，懒分配 N ref，不去重、允许重复调用
	→ fetch_graph_content：先做范围校验 + 按真实节点去重，再取回内容
		→ Judge 逐 node 判断哪些内容有用
	→ Summarizer（代码固定每轮跑一次）：更新累计摘要，判断 sufficient
		→ sufficient=true：代码直接跳出循环，不绕回决策 agent
	→ 命中三重熔断条件之一：跳出循环，进兜底
→ 兜底（仅熔断触发）：从长期记忆选 ≤5 个候选 page，做一次 scoped 混合检索 + 一次轻量 Judge
→ answer_generator 生成最终回答
```

**引用系统：P / N / E**

- 全系统只有一套短引用 ↔ 真实 ID 的映射（`ReferenceRegistry`），LLM 自始至终只看得到 `P1`/`N7`/`E12` 这类短 ref，真实的 `page:<hash>`/`section:<hash>`/`chunk:<hash>` 只存在于工具内部真正调用图导航/检索器的代码里，以及落盘的 log 文件中。

  * **P ref**：request 一开始对全部 page 一次性分配完，整个 request 期间不变。
  * **N ref**：`inspect_page` 渲染某个结构节点时才分配——懒分配，没被展示过的节点没有 N ref。
  * **E ref**：`fetch_graph_content`（或兜底阶段的混合检索）真正取回某个 chunk 时才分配，按 `chunk_id` 内容寻址——同一个 chunk 无论被发现几次都拿到同一个 E ref。
- 分配是幂等的（同一个真实 ID 永远映射回同一个 ref）。`ReferenceRegistry` 挂在 `AgentContext` 上，同一个 request 内所有工具调用、Judge、Summarizer 调用共享同一份，request 结束即丢弃，不跨 request 复用。

**Agent-loop Prompt:**

* system_prompt（`build_agent_decision_system`，page summaries 段落随 request 动态拼入）：
  ```
  ROLE
  You are the graph-navigation planner for a UChicago MS in Applied Data Science RAG system.
  This system answers questions primarily by navigating a structured knowledge graph, not by
  generic full-text search.

  TASK
  Choose exactly one next action every turn.

  LONG-TERM MEMORY: ALL PROGRAM PAGES
  {page_summaries_block}

  TOOLS
  - inspect_page(page_ref): show a page's structural outline as N refs. Large branches are
    collapsed with a content preview. You may inspect any page, including ones you've already
    inspected before, at any point — re-inspecting is cheap and useful after learning new
    information about what's NOT relevant elsewhere.
  - fetch_graph_content(node_refs): fetch content from 1-5 available N refs at once.

  STRATEGY
  1. Pick the 1-3 most relevant pages directly from the long-term memory list; inspect the most
     likely one first.
  2. Use path names and preview hints in the outline to pick specific nodes; do not fetch a large
     branch when a smaller labeled child is already visible.
  3. Batch multiple sibling nodes (up to 5) in one fetch_graph_content call when plausibly relevant.
  4. Read the investigation summary before deciding — it tells you what has already been found
     useful/not useful by path, and what the decision history has already tried.
  5. If the summary says a page/branch was unproductive, re-inspect a different page from the
     long-term memory list, or re-inspect the same page if you now have new context that changes
     which branch looks promising.

  RULES
  - Use only P/N/E refs actually shown in this context. Never invent or transform a ref.
  - Tool arguments must contain the bare ref token only, e.g. P4 or N7.
  - Always state your reasoning in "reason" — every decision is recorded for audit.

  OUTPUT FORMAT
  {"action":"inspect_page|fetch_graph_content","args":{...},"reason":"..."}
  ```
* 每一轮 `agent_decision_user` prompt：
  ```
  Original question: {original_query}

  Rewritten retrieval queries:
  - {rewritten query 1}

  Investigation summary:
  Coverage so far:
  {Summarizer 上一轮的 summary_covering，按 page 分组}

  Decision history so far:
  {Summarizer 上一轮的 decision_trace_narrative}

  Available node refs (from pages already inspected):
  - [N9] Application Fee (5 content blocks; page P3)
  - [N21] Sample Elective Courses (27 content blocks; page P6) [ALREADY REJECTED: too broad — try N22 instead]

  Accepted evidence so far:
  - [E12] How to Apply > Application Requirements: ...

  Agent turns remaining after this decision: {n}

  Choose one action and return the required JSON object.
  ```

**2 个主循环工具（无 finish，无 hybrid_retrieve）：**

* `inspect_page(page_ref)`

  * 渲染页面结构树，逐节点分配 N ref。

  ```
  [P3] How to Apply
  ├── [N4] Application Requirements — Section (5 content blocks)
      preview: Requires a completed application, transcripts, two letters of recommendation...
  ├── [N5] Letters of Recommendation — Section (2 content blocks)
  ```

  * **折叠规则**：深度不为 0 的节点，只要自身内容量不超过 12 个 content block，就收起来只留预览，不再往下展开分配 N ref；内容量在 3~12 之间时额外展示一行内容预览（取节点自己的索引摘要）或子节点标签列表。这个上限和 `fetch_graph_content` 的范围校验共用同一个数字，保证"outline 里显示折叠"和"可以整体 fetch"永远是同一件事。
  * **宽而浅分支的特殊折叠**：结构子节点数超过 3 个、且每个子节点自身内容量都不超过 5 个的节点，即便自身总内容量超过 12 也照样折叠——典型场景是一个 Accordion 挂了十几个短小的子项，逐个列出没有信息增量，节点自己的索引摘要（"this accordion contains: ..."）已经把内容说清楚了。
* `fetch_graph_content(node_refs)`

  * 一次取 1-5 个已分配的 N ref。返回内容按节点归属分组分层展示：顶层请求的 N ref 展示一次完整 path，子级只显示自己的 label 和当次展示用的子编号（如 `N9.1`，用完即弃，不参与跨轮引用），叶子内容标 `[E#]`。

    ```
    [N9] Core — CourseGroup — path: Course Progressions > Sample Full-Time Schedule > Quarter 1 > Core
      [N9.1] Data Engineering Platforms
       [E12] Covers distributed data systems, cloud infrastructure, and platform architecture.
      [N9.2] Machine Learning
       [E13] Covers supervised and unsupervised methods, model evaluation, and deployment.
    ```
  * **范围校验**：满足以下任一条件才允许直接 fetch——内容量本身不多；或者已经是图结构里最细粒度的节点，没有更小的选项可以引导；或者虽然内容量偏多，但属于上面说的"宽而浅"分支。都不满足就拒绝，返回文本里附带子节点建议，例如 `N21 has 27 content blocks — too broad. Consider its children instead: N22 (27).`
  * 边界控制：一批里的拒绝节点不会拖累其他节点 + 按真实节点去重 + 持久标记被拒节点

**Judge（逐 node 判断）与 Summarizer（每轮固定跑，判 sufficient）：**

* Judge：只在 `fetch_graph_content` 成功后触发，对这一批请求里的每个顶层 N ref 分别判断保留哪些 E ref，不判断整体调查是否够——这是刻意的职责切分。
* Summarizer：**代码固定每轮调用一次**，不是 LLM 自己决定要不要总结。输入是按 page 分组的 node 评估记录、这一轮的原始工具状态、完整的已接受证据目录；输出两段独立文本——按 page 组织的调查覆盖情况，和一段决策过程审计叙事——以及是否 sufficient 的判断。sufficient 为真时代码直接跳出循环，不再绕回决策 agent 自己选择 finish。

**熔断条件与兜底：**

* 用两个贯穿整个 request 的累计计数器判断要不要放弃图导航、转向兜底检索：一次 fetch 只要涉及至少一个此前没有成功取回过的节点，就算一次有效尝试；否则（纯重复请求、参数无效、被范围校验拒绝）算一次无效尝试。三个条件满足任意一个就熔断进兜底：
  * Summarizer 判断仍然不够，且累计有效尝试超过 5 次——一直在找新证据但还是不够。
  * 累计有效尝试不超过 5 次，但累计无效尝试超过 3 次——一直在失败或重复请求。
  * 到第 5 轮为止，有效和无效尝试都还是 0 次——从没真正进入过 fetch，一直在看页面结构打转。

- 熔断触发后：决策 agent 从长期记忆里选出最多 5 个最可能相关的 page，把混合检索限定在这个范围内做一次，再跑一次轻量 Judge，结果合并进已接受证据，直接进入答案生成，不再回到图导航循环。

**答案生成：区分"完全无关"和"有关但没覆盖"（效果和 FlatRAG 一致）：**

* 问题完全跟项目无关（通用知识、其它学校、做饭天气这类）——固定回复 "I can only answer questions about the UChicago MS in Applied Data Science program."，即使证据里恰好有几条字面上沾边的内容也不允许强答。
* 完全没有证据支撑时，先判断问题本身在不在项目范围内：不在范围内用上面这句话；在范围内但语料没覆盖，换成另一句——"I couldn't find relevant information about this in the available program materials. ..."。这一步刻意不提前用代码短路掉，因为区分"完全无关"和"相关但没覆盖"本身需要语义判断，没有更省成本的信号能可靠区分这两种情况。

### 各自优缺点

**FlatRAG**

* 优点：
  1. 架构简单，管线短，可预测性强，实现和维护成本低。
  2. 3个工具语义清晰，调试成本低
  3. chunk-size/embedding 做过正式网格评测,检索质量有数据支撑
* 缺点：
  1. chunk-size/embedding 做过正式网格评测,检索质量有数据支撑
  2. 遇到强结构化页面（多级 accordion、tab）依赖清理阶段手写的硬编码，维护成本高。
  3. 纯向量检索对"这一页有哪些小节/课程"这类目录型问题命中率天然较弱（虽然 `accordion_overview` chunk 一定程度上补了这个洞）。

**GRAG v2**

* 优点：
  1. 结构信息保留在图里是"活的"——可以运行时导航（inspect_page → fetch_graph_content），更擅长结构复杂页面
  2. 结构化处理网页 -> 泛化性更好，复用性更好
  3. 四路混合检索（作为熔断后的兜底手段）
  4. 主循环收窄成 2 个工具，范围校验/去重/停止判断都是确定性代码逻辑，可预测性和可调试性比自由度更高的 agent 设计明显更强
* 缺点：
  1. 架构复杂度仍然高：图构建、四路分数融合、引用系统、Judge + Summarizer 两级判定、三重熔断，活动部件比 FlatRAG 多一个数量级
  2. 每轮固定跑 Judge 和 Summarizer 两次额外 LLM 调用，简单问题也不能省——延迟大概率显著高于 FlatRAG
  3. 真正自由的决策点收窄到只剩"选哪个 page/node"，遇到 workflow 没预料到的场景时，恢复手段比自由度更高的 agent 少

---

# GRAG → GRAG v2：问题诊断与方向性改动

原本的 `pipeline_grag/` 设计在几轮调试下来反复出现**无效工具调用**和**边界性 bug（重复调用、卡死、无法正确停止）**。从日志和代码逐节点拆解来看，这些问题是**多个环节共同导致的**，所以 v2 采取的是整体改动翻新，而不是逐个打补丁式的实验性修改。

### 根因一：prompt 设计不够 LLM-friendly

1. **system prompt 没有起到好的指引和规范作用**：原设计只是罗列 task + tools，再用 rules 规定 workflow，缺少策略层。
   **解决方向**：v2 把 system prompt 拆成五段：角色定位（ROLE）+ 任务（TASK）+ 工具的详细解释（TOOLS，说明每个工具具体能做什么、什么时候该用）+ 指引工具调用策略（STRATEGY）+ 行为规范（RULES）——用 STRATEGY 专门指引"先看长期记忆里最相关的 1-3 个 page、用 outline 里的 path 和 preview 挑具体节点、参考调查摘要判断该不该换页面"这类调用策略，而不是只靠 RULES 罗列禁止项。
2. **工具结果返回给 LLM 时非必要 metadata 太多**：原设计里 `inspect_page`/`fetch_node_chunks` 直接把 `node_id: section:9f2ac1e0...` 这类哈希 ID、结构化符号原样喂给 LLM——LLM 对这类 ID 类信息的提取和复制容易发生偏移，而且 context 里 ID 类 token 占比太高也会稀释 LLM 对真正语义内容的注意力。
   **解决方向**：v2 引入 P/N/E 短引用系统：LLM 全程只看到 `N7`/`E12` 这类短 ref，真实的哈希 ID 只存在于工具内部真正调用图导航/检索器的代码里、以及落盘的 log 文件——喂给 LLM 的应该以语义为主，ID 类的精确匹配交给 LLM 之外的索引编码（`ReferenceRegistry` 的 assign/resolve）去做。

### 根因二：太多本该由代码保证正确性的环节，被交给了 LLM 的自由裁量

边界性 bug（重复调用、卡死、无法正确停止）的根源，都是原来的设计把去重、范围校验、是否停止、是否兜底这些本该由代码保证正确性的环节，也交给了 LLM 的自由裁量。

**解决方向**：设计一个以一个有界的 agentic 决策点为核心的 workflow。把能由代码保证正确性的环节全部收回成确定性代码逻辑，只把"这块内容有没有用""接下来该看哪"这种真正需要语言理解能力的判断留给 LLM——这正是让系统变得可预测、封闭、可验证的关键。

| 环节                         | 谁决定"要不要做/什么时候做"                                                 | 性质                            |
| ---------------------------- | --------------------------------------------------------------------------- | ------------------------------- |
| 是否调用 Judge               | 由代码决定；`fetch_graph_content` 执行成功后固定触发                      | Workflow                        |
| 是否调用 Summarizer          | 由代码决定；每一轮结束后固定触发                                            | Workflow                        |
| 是否 Finish                  | 由代码读取 Summarizer 输出的`sufficient` 字段直接判断，不再返回查询 Agent | Workflow                        |
| 是否进入兜底流程             | 由代码按边界条件判断（见下）                                                | Workflow                        |
| 选择哪个 Page                | 由 LLM 决策 Agent 自主选择                                                  | Agentic                         |
| 选择哪个 Node 进行 Fetch     | 由 LLM 决策 Agent 自主选择                                                  | Agentic                         |
| Judge 判断某个 Node 是否有用 | 内容判断由 LLM 完成；调用时机、输入内容和输出 schema 均由代码固定           | 判断是 LLM，触发方式是 Workflow |

具体落到 v2 的每一处改动：

* **是否停止交给代码**：Judge 只判断单批内容有没有用，Summarizer 每轮固定跑一次、专门判断整体 sufficient；`sufficient=true` 时代码直接跳出循环，不再绕回决策 agent 自己选 finish。
* **去重交给代码**：`fetch_graph_content` 按真实节点 ID 去重，重复请求直接返回历史结果，不重新执行、不重新进 Judge。
* **范围校验交给代码**：内容量、是否图叶子、是否宽而浅分支三个条件判断能不能直接 fetch，批次内不合适的节点单独拒绝、不连累同批其它节点，还持久标注在后续轮次的可用节点列表里。
* **是否进兜底交给代码**：累计有效尝试、累计无效尝试、从未真正开始 fetch 三重熔断条件覆盖所有卡住的形态，命中即代码直接切换到兜底检索，不依赖 LLM 自己判断"要不要换策略"。

### 根因三：图导航与混合检索两条路径没有明确的主从关系

`hybrid_retrieve` 被代码写死成第一步，不管问题是不是结构型问题都要先跑一次向量检索；后续要不要走图导航、图导航过程中要不要再补一次混合检索，全靠 LLM 临场判断，没有清晰的角色分工，也弱化GRAG本身优势。

**本质问题**：向量语义检索和图结构导航解决的是不同性质的问题——前者适合"哪里提到了这个概念"，后者适合"这个页面下有哪些小节/课程、它们之间是什么关系"——但原设计没有明确谁是主循环、谁是兜底：简单问题也要多付出一次不必要的检索延迟，复杂的结构型问题又可能被过早出现的向量检索结果误导，让 LLM 提前判定"证据已经够了"。

**解决思路**：v2明确图导航为主循环，混合检索只在图导航触发熔断条件之后，作为一次性兜底手段执行一次，检索范围还进一步收窄到决策 agent 选出的候选 page 内。

### 根因四：很多分支情况和处理链路没有做闭环考量

原设计能覆盖"正常路径"该怎么走，但边界分支往往没有被显式处理完——一个节点内容量超限但确实没有更小的子节点可选时怎么办、一批请求里部分节点合适部分不合适时怎么办、完全没有证据时到底该给哪种话术、卡住了要不要有个兜底出口——这些分支要么没被想到，要么想到了但没有一个明确的出口，容易在实际运行时走进死胡同。

**本质问题**：设计时习惯先覆盖"预期会发生的情况"，没有系统性检查"所有可能的情况加起来是不是等于全集"，导致总有一些分支没有明确出口，只能靠 LLM 现场瞎猜，或者干脆卡死跑满轮次。

**解决思路**：v2尽量对关键分支做完备性检查，确保每种情况都有明确出口，不留悬空状态。具体例子：

* 范围校验里"没有更小子节点可选"这条分支：即便内容量超限，只要图结构上已经没有更细粒度的节点可以引导，也直接放行，避免一个节点因为超限又无处可去而彻底不可达。
* 一批请求里部分节点合适、部分不合适：拆开处理，不合适的单独拒绝，合适的照常执行，不因为一个节点连累整批全军覆没。
* 三重熔断条件刻意设计成互不重叠、并集覆盖"卡在 fetch 循环里"和"从未真正开始 fetch"两大类停滞形态，不会有一种卡住的方式漏在覆盖范围之外。
* 空证据时明确拆成"完全无关"和"有关但没覆盖"两条分支，各自给固定话术，不再是一句模糊的"没找到"。
