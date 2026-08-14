# QA Pipeline 重设计：LLM Agent with Tool Calling

## Context

当前 pipeline 的核心问题：agent 自主性太弱。
- Page selector 只能在轮次间 exclude_labels，无法在同一轮内调整策略
- Judge 只能打标（full/partial/none），无法主动说"我要去另一个 section 看看"
- 无法针对具体 section 精确取块，只能全页面语义检索

**目标**：把 retrieval loop 改成真正的 LLM Agent，使用 OpenAI 兼容的 function calling（DeepSeek-chat 支持），agent 自主决定每一步：先看哪个页面的结构、去哪个 section 取块、还是回退到语义检索。

---

## 新架构总览

```
用户 query
  ↓
① Query Rewriting（1次 LLM 调用，不变）
  ↓
② Agent Loop（tool calling，最多9步）
   初始 system prompt = 任务说明 + 14个页面组描述
   初始 user message = rewritten_query
   每步：LLM 返回 tool_calls → 执行工具 → 结果进入 candidate_chunks → Judger 评估 → 有用部分进入 useful_chunks
   状态：useful_chunks: list[dict]（去重，跨步骤累积）
   终止：judger 决定 DONE 或达到最大步数
  ↓
③ Context assembly + LLM generation（不变）
  ↓
④ 每次 run() 写 data/qa_log/<timestamp>_<slug>.json
```

**正常路径 LLM 调用数**：1（改写）+ 2（agent steps）+ 1（judger）+ 1（生成）= 5 次
**最差路径**：1 + 9（agent steps，含多次 judger）+ 1 = 最多13次

---

## Agent 可用的 3 个工具（无 finish）

### 1. `list_page_sections(url)`
**用途**：查看某页面的全部 section_breadcrumb 和 chunk 数量，用于决定去哪个 section 取块。

```json
{
  "name": "list_page_sections",
  "description": "List all section breadcrumbs and chunk counts for a page URL. Use this to understand a page's structure before deciding which section to target.",
  "parameters": {
    "type": "object",
    "properties": {
      "url": {"type": "string", "description": "Page group label (e.g. 'career_outcomes') OR canonical page URL — labels are resolved automatically."}
    },
    "required": ["url"]
  }
}
```

**返回格式**（tool message content）：
```
Sections in https://datascience.uchicago.edu/.../in-person-program/:
- "Curriculum > Core Courses" (3 chunks)
- "Curriculum > Electives" (2 chunks)
- "Program Track > 1-Year" (4 chunks)
...
```

### 2. `fetch_section_chunks(url, section_breadcrumb)`
**用途**：直接取某 section 的所有 chunks，bypasses 语义检索，精确度最高。

```json
{
  "name": "fetch_section_chunks",
  "description": "Retrieve all text chunks from a specific section of a page. Use when you know exactly which section contains the answer. Fuzzy-matches the breadcrumb if not exact.",
  "parameters": {
    "type": "object",
    "properties": {
      "url": {"type": "string", "description": "Page group label (e.g. 'career_outcomes') OR canonical page URL — labels are resolved automatically."},
      "section_breadcrumb": {"type": "string", "description": "Exact breadcrumb as returned by list_page_sections"}
    },
    "required": ["url", "section_breadcrumb"]
  }
}
```

**返回格式**：
```
[0] In-Person Program | Curriculum > Core Courses
Data Engineering Platforms: Students learn to build...

[1] In-Person Program | Curriculum > Core Courses
Machine Learning Pipeline: The course covers...
```

返回的 chunks 进入本轮 `candidate_chunks`；Judger 评估后，`useful_indices` 对应的 chunks 才进入 `useful_chunks`（按 chunk_id 去重）。

### 3. `semantic_search(query, include_urls=None, exclude_urls=None, exclude_chunk_ids=None, top_k=5)`
**用途**：向量语义检索，作为 fallback 或在 section 结构不清时使用。

```json
{
  "name": "semantic_search",
  "description": "Semantic similarity search. Use include_urls to restrict to specific pages, exclude_urls/exclude_chunk_ids to avoid already-tried content.",
  "parameters": {
    "type": "object",
    "properties": {
      "query": {"type": "string"},
      "include_urls": {"type": "array", "items": {"type": "string"}, "description": "If set, only search within these pages. Each entry can be a page group label or a canonical URL — labels are expanded to all their URLs automatically."},
      "exclude_urls": {"type": "array", "items": {"type": "string"}, "description": "If set, exclude chunks from these URLs (full URL only)"},
      "exclude_chunk_ids": {"type": "array", "items": {"type": "string"}, "description": "Exclude specific chunk IDs already accumulated"},
      "top_k": {"type": "integer", "default": 5, "description": "Number of results"}
    },
    "required": ["query"]
  }
}
```

**返回格式**：同 fetch_section_chunks，并标注 distance。返回的 chunks 同样先进入 `candidate_chunks`，经 Judger 筛选后才加入 `useful_chunks`（去重）。

---

## Agent System Prompt（放在 prompt_templates.py）

**不硬编码页面描述**，改为 `build_agent_system_prompt(summaries: list[dict]) -> str` 函数，在 `QAPipeline.__init__` 时调用，动态从 `page_summaries.json` 读取的 `self._selector._summaries` 生成。
这样只需维护 `page_summaries.json` 一份文件。

```python
def build_agent_system_prompt(summaries: list[dict]) -> str:
    groups = "\n".join(f'- "{g["label"]}": {g["description"]}' for g in summaries)
    return f"""You are a retrieval agent for the UChicago MSADS program knowledge base.
Your task: gather the most relevant text chunks to answer the user's question.

Available page groups:
{groups}

Strategy:
1. Call list_page_sections on the 1-2 most likely pages to see their structure.
2. Call fetch_section_chunks on the specific section most likely to contain the answer.
3. A judger will evaluate your retrieved candidates after each fetch/search. If the judger says CONTINUE, try another approach.
4. If fetch results are insufficient, call semantic_search with include_urls to search within likely pages.
5. As a last resort, call semantic_search with exclude_urls and exclude_chunk_ids to search broadly.
6. If judger says DONE, the loop exits automatically — do not keep searching.

Rules:
- Maximum 9 tool calls total.
- Do not repeat the same section or search.
- Pass exclude_chunk_ids=[...] to semantic_search to avoid duplicate chunks.
"""
```

---

## candidate_chunks → Judger → useful_chunks 流程

**`fetch_section_chunks` 和 `semantic_search` 的返回结果不直接进入 `useful_chunks`**，而是进入本轮的 `candidate_chunks`。每次有实质检索（而非 `list_page_sections`）后，运行 **Judger**：

```
retrieval tool call → candidate_chunks
         ↓
     Judger LLM call
     输入：original_query + candidate_chunks + accumulated useful_chunks
     输出：{completeness: full/partial/none, useful_indices: [0,2,...], decision: done/continue}
         ↓
  useful_indices → 对应 candidate_chunks → 加入 useful_chunks（去重）
         ↓
  decision=done → 退出 agent loop
  decision=continue → 向 agent 追加消息告知不足，agent 继续下一轮工具调用
```

Judger 的 prompt 沿用现有的 `format_judgment_prompt`，加上 `decision` 字段：
```python
# 返回 JSON：
# {"completeness": "full"|"partial"|"none", "useful_indices": [...], "decision": "done"|"continue"}
# decision=done 当 completeness==full 或 completeness==partial 且 len(useful_chunks)>=2
```

Agent 收到 judger 反馈后（通过 **system role** 消息注入，不是 user role — judger 是 pipeline 系统级控制器），可以根据"CONTINUE"决策选择不同的 section 或改用语义检索。

---

## useful_chunks 容器

- 类型：`list[dict]`，字段同现有 chunk schema
- 去重：按 `chunk_id`，新加入前检查
- 生命周期：整个 agent loop 共享，loop 结束传入 `_generate()`

---

## 实现文件

### 1. `src/retrieval/retriever.py` — 新增两个方法

```python
def list_sections(self, url: str) -> list[dict]:
    """Return all unique section_breadcrumbs and chunk counts for a URL."""
    result = self._collection.get(
        where={"source_url": url},
        include=["metadatas"]
    )
    counts: dict[str, int] = {}
    for meta in result["metadatas"]:
        bc = meta.get("section_breadcrumb") or meta.get("section", "")
        counts[bc] = counts.get(bc, 0) + 1
    return [{"section_breadcrumb": k, "chunk_count": v} for k, v in sorted(counts.items())]

def get_by_section(self, url: str, section_breadcrumb: str) -> list[dict]:
    """Retrieve all chunks from a specific section of a page."""
    result = self._collection.get(
        where={"source_url": url, "section_breadcrumb": section_breadcrumb},
        include=["documents", "metadatas"]
    )
    chunks = []
    for i, doc in enumerate(result["documents"]):
        meta = result["metadatas"][i]
        chunks.append({
            "text": doc,
            "chunk_id": result["ids"][i],
            "source_url": meta.get("source_url", ""),
            "page_title": meta.get("page_title", ""),
            "section": meta.get("section", ""),
            "section_breadcrumb": meta.get("section_breadcrumb", ""),
            "content_type": meta.get("content_type", ""),
            "program_type": meta.get("program_type", "general"),
            "distance": 0.0,
        })
    return chunks
```

### 2. `src/qa/agent_tools.py` — NEW，工具执行逻辑

**职责**：接收工具名 + 参数 → 执行 → 返回 (tool_result_str, new_chunks)

Agent 传入的 `url` 参数可能是页面 group label（如 `"career_outcomes"`）而非完整 URL。`execute_tool` 在分发前先通过 `_build_label_map(summaries)` 构建 label→URLs 映射，再用以下两个辅助函数透明解析：
- `_resolve_single_url(value, label_map)` — label 返回第一个 URL，完整 URL 原样返回
- `_resolve_url_list(values, label_map)` — 列表中每个 label 展开为该 group 所有 URL

```python
def execute_tool(
    name: str,
    args: dict,
    retriever: Retriever,
    useful_ids: set[str],
    summaries: list[dict] | None = None,  # page_summaries.json 内容，用于 label 解析
) -> tuple[str, list[dict]]:
    label_map = _build_label_map(summaries or [])

    if name == "list_page_sections":
        url = _resolve_single_url(args.get("url", ""), label_map)
        return _list_page_sections(url, retriever)
    if name == "fetch_section_chunks":
        url = _resolve_single_url(args.get("url", ""), label_map)
        return _fetch_section_chunks(url, args.get("section_breadcrumb", ""), retriever)
    if name == "semantic_search":
        resolved_args = dict(args)
        if args.get("include_urls"):
            resolved_args["include_urls"] = _resolve_url_list(args["include_urls"], label_map)
        return _semantic_search(resolved_args, retriever, useful_ids)
    return f"Unknown tool: {name}", []
```

**`list_page_sections` 执行**：
- label 或 URL → `_resolve_single_url` → 调用 `retriever.list_sections(url)`
- 格式化为多行字符串返回，不产生 candidate_chunks

**`fetch_section_chunks` 执行（含兜底）**：
1. label 或 URL → `_resolve_single_url`，得到真实 URL
2. 调用 `retriever.list_sections(url)` 取出所有真实 breadcrumb
3. 先尝试精确匹配；失败则大小写不敏感匹配；再失败用 `difflib.get_close_matches(breadcrumb, all_breadcrumbs, n=1, cutoff=0.6)` 找最接近的
4. 若找到近似匹配 → 用真实 breadcrumb 执行检索，tool result 中注明 `(fuzzy-matched to: "真实 breadcrumb")`
5. 若完全无匹配 → tool result 返回 `"Section not found. Available sections: {list}"` 并返回 0 个候选
- 所有返回 chunks 进入本轮 `candidate_chunks`（不直接进 useful_chunks）

**`semantic_search` 执行**：
- `include_urls` 中每个条目先经 `_resolve_url_list` 展开（label → 该 group 所有 URLs）
- 构建 where 参数：`include_urls` → `{"source_url": {"$in": resolved_urls}}`
- `exclude_urls` / `exclude_chunk_ids` 在 Python 层后处理过滤（不依赖 $nin）
- 调用 `retriever.retrieve(query, top_k=top_k, where=where)`
- 返回新增 chunks，进入本轮 `candidate_chunks`

### 3. `src/qa/qa_pipeline.py` — 重写 retrieval loop 部分

**保留不变**：`__init__`, `_rewrite_query`, `_generate`, `_dedupe_sources`, `build_pipeline`

**删除**：`_judge` 方法

**新增**：`_run_agent_loop`, `_write_log`

**`run()` 新流程**：

```python
def run(self, query: str) -> dict:
    agent_messages = []
    useful_chunks: list[dict] = []
    completeness = "none"
    
    rewritten = self._rewrite_query(query, agent_messages)  # agent_messages用于log
    
    completeness, useful_chunks = self._run_agent_loop(
        rewritten, query, agent_messages
    )
    
    if not useful_chunks:
        # 兜底：直接语义检索
        useful_chunks = self._retriever.retrieve(rewritten, top_k=7)
        completeness = "none"
    
    low_confidence = completeness != "full"
    confidence_note = ... # 同之前逻辑
    
    context_str, _ = format_context(useful_chunks)
    answer = self._generate(query, context_str, confidence_note, agent_messages)
    sources = self._dedupe_sources(useful_chunks)
    
    result = {
        "answer": answer,
        "sources": sources,
        "completeness": completeness,
        "low_confidence": low_confidence,
        "attempts": len([m for m in agent_messages if m["role"] == "tool"]),
    }
    self._write_log(query, rewritten, agent_messages, useful_chunks, result)
    return result
```

**`_run_agent_loop(rewritten, original_query, log_messages)`**：

```python
def _run_agent_loop(self, rewritten, original_query, log_messages):
    from src.qa.agent_tools import TOOL_SCHEMAS, execute_tool  # TOOL_SCHEMAS: 3 tools only (no finish)
    
    useful_chunks: list[dict] = []
    useful_ids: set[str] = set()
    completeness = "none"
    
    messages = [
        {"role": "system", "content": self._agent_system_prompt},
        {"role": "user", "content": f"Find relevant information to answer: {rewritten}"},
    ]
    
    for step in range(9):  # max 9 tool calls
        resp = self._client.chat.completions.create(
            model=_LLM_MODEL, messages=messages, tools=TOOL_SCHEMAS,
            tool_choice="auto", temperature=0, max_tokens=400,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump())
        
        if not msg.tool_calls:
            break  # agent chose to stop
        
        candidate_chunks: list[dict] = []
        retrieval_happened = False
        
        for tc in msg.tool_calls:
            name = tc.function.name
            args = json.loads(tc.function.arguments)
            # summaries 传给 execute_tool 用于 label → URL 解析
            tool_result, new_candidates = execute_tool(
                name, args, self._retriever, useful_ids, self._selector._summaries
            )
            candidate_chunks.extend(new_candidates)
            if name in ("fetch_section_chunks", "semantic_search"):
                retrieval_happened = True
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})
        
        # 只在有实质检索时运行 judger
        if retrieval_happened and candidate_chunks:
            completeness, useful_indices, decision = self._judge(
                original_query, candidate_chunks, useful_chunks
            )  # _judge 返回值加 decision: "done"|"continue"
            
            for i in useful_indices:
                if 0 <= i < len(candidate_chunks):
                    c = candidate_chunks[i]
                    if c["chunk_id"] not in useful_ids:
                        useful_chunks.append(c)
                        useful_ids.add(c["chunk_id"])
            
            # 将 judger 结论以 system role 注入（不是 user role：judger 是 pipeline 系统控制器）
            judge_feedback = (
                f"[Judger] completeness={completeness}, useful={useful_indices}. "
                f"Decision: {'DONE — exit retrieval loop.' if decision == 'done' else 'CONTINUE — try another approach.'}"
            )
            messages.append({"role": "system", "content": judge_feedback})
            
            if decision == "done":
                break
    
    log_messages.extend(messages)
    return completeness, useful_chunks
```

**`_judge` 返回值扩展**：在现有 `(completeness, useful_indices)` 基础上增加 `decision`：
- `decision = "done"` if completeness == "full"，或 completeness == "partial" and len(useful_chunks after update) >= 2
- `decision = "continue"` otherwise

### 4. `src/qa/prompt_templates.py` — 新增 AGENT_SYSTEM_PROMPT

在现有代码基础上增加：
```python
AGENT_SYSTEM_PROMPT = "..."  # 如上方 Agent System Prompt 节所述
```

---

## 日志格式（data/qa_log/）

**文件名**：`YYYYMMDD_HHMMSS_<slug>.json`

```json
{
  "timestamp": "2026-05-05T12:34:56.789",
  "query": "What are the core courses required in the in-person program?",
  "rewritten_query": "...",
  "agent_steps": [
    {
      "step": 1,
      "tool_calls": [{"name": "list_page_sections", "args": {"url": "..."}}],
      "tool_results": [{"name": "list_page_sections", "content": "Sections...(truncated 500 chars)"}],
      "judger": null
    },
    {
      "step": 2,
      "tool_calls": [{"name": "fetch_section_chunks", "args": {"url": "...", "section_breadcrumb": "Curriculum > Core Courses"}}],
      "tool_results": [{"name": "fetch_section_chunks", "content": "[0] ...(truncated)"}],
      "judger": {
        "completeness": "full",
        "useful_indices": [0, 1],
        "decision": "done",
        "raw_response": "{\"completeness\": \"full\", ...}"
      }
    }
  ],
  "useful_chunks": [
    {"chunk_id": "in_person_program__0", "page_title": "In-Person Program",
     "section_breadcrumb": "Curriculum > Core Courses", "text_snippet": "first 120 chars..."}
  ],
  "generate_elapsed_ms": 2341,
  "result": {
    "completeness": "full",
    "low_confidence": false,
    "attempts": 2,
    "answer_snippet": "first 200 chars of answer..."
  }
}
```

**日志设计原则**：
- `agent_steps` 按 step 组织（每次 LLM-agent 调用 = 1 step），包含该步的 tool_calls、tool_results（截断500字符）和 judger 结果
- `judger` 字段：`list_page_sections` 步骤为 `null`；有检索的步骤包含完整 judger 输出含原始 JSON 响应
- system/user 初始消息不记录（减小文件体积）；[Judger] user 注入消息也不单独记录，已在 judger 字段体现
- `useful_chunks` 只记录最终入选的 chunks（text_snippet 截断120字符），不记录 candidate_chunks

---

## 修改文件清单

| 文件 | 操作 |
|------|------|
| `src/retrieval/retriever.py` | 新增 `list_sections()` 和 `get_by_section()` |
| `src/qa/agent_tools.py` | 新建：TOOL_SCHEMAS + `execute_tool()` |
| `src/qa/qa_pipeline.py` | 重写 retrieval 部分：删 `_judge`，加 `_run_agent_loop` + `_write_log` |
| `src/qa/prompt_templates.py` | 新增 `AGENT_SYSTEM_PROMPT` |
| `data/qa_log/` | 运行时自动创建 |

**不修改**：`page_selector.py`（validate_stage_3.py 仍需导入）、`page_summaries.json`、`demo.py`

---

## validate_stage_3.py 兼容性

| 检查 | 是否受影响 |
|------|-----------|
| Check 1: import QAPipeline | ✓ 不变 |
| Check 2: PageSelector loads (page_selector.py不变) | ✓ 不变 |
| Check 3: QAPipeline instantiation | ✓ 不变 |
| Check 4-8: 行为检查（off-topic/deadline/capstone/career） | ✓ generation 部分不变，行为取决于 agent 召回质量 |
| Check 9: 返回 dict 结构 | ✓ 结构不变，attempts 改为 tool call 次数 |

---

## 验证步骤

1. `python src/qa/demo.py` — 提问 "What are the core courses for in-person students?"，观察 agent 是否调用 `list_page_sections` → `fetch_section_chunks` → `finish`
2. 检查 `data/qa_log/` 下有对应 JSON 文件，agent_steps 清晰展示推理路径
3. `conda run -n adsp-nlp-backup python tests/validate_stage_3.py` — 9/9 PASS

---

## ⚠️ 主要风险

- **DeepSeek function calling 可靠性**：偶尔可能不按预期调用工具。System prompt 足够明确，但需要手动测试几个 query。
- **Agent 传 label 而非 URL**：已在 `execute_tool` 中通过 `_resolve_single_url` / `_resolve_url_list` 透明处理，agent 无需区分 label 和 URL。
- **useful_chunks 为空的兜底**：已在 `run()` 中处理，直接用无 filter 的语义检索兜底。
