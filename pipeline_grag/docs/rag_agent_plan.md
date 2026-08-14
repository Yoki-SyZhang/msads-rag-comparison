# MSADS RAG Agent 完整设计 Plan

## Summary
在现有 hybrid retriever 之上增加一个 `qwen3:8b` 驱动的 RAG agent。agent 先 rewrite/decompose 当前用户问题，再默认调用 hybrid retrieval；如果证据不足，就用根目录 `page_summaries.json` 的 description 选择 page，再用 Knowledge Graph 的整页结构图定位 node，按 node fetch chunks，循环 judge，最后只用保留下来的 evidence 生成答案。最终 JSON response 由代码生成，不让 LLM 直接生成。所有 LLM 中间输出（rewrite、decision、judge）均为 JSON 格式，由代码用 `json.loads()` 解析；只有 answer generation 输出自由文本。

## Core Flow
- `POST /chat` request：
  ```json
  {
    "query": "string",
    "history": [
      { "role": "user", "content": "..." },
      { "role": "assistant", "content": "..." }
    ]
  }
  ```
- 后端流程：
  ```text
  query + history
    -> rewrite current query only
    -> agent loop (max 9 tool calls):
         -> agent decision: choose next tool  [JSON output]
         -> execute tool
         -> judge（list_page_summaries 除外）: update evidence, check if sufficient  [JSON output]
              if sufficient     -> break loop
              if not sufficient -> back to agent decision
         -> if max calls reached: break loop, proceed with current evidence
    -> answer generation using evidence_container
    -> code builds answer_parts and citations
    -> write one log file
    -> return frontend JSON
  ```
- `history` 只用于解决当前 query 里的指代，比如 “it / this program / that deadline”；不 rewrite 历史问题，也不重新回答历史问题。
- 默认第一步是 `hybrid_retrieve`。
- 如果 judge 认为 evidence 已足够，提前停止；否则最多 9 次 tool call。

## Agent Tools
- `hybrid_retrieve(query, top_k)`
  - 复用当前 retrieval core：Chroma vector + BM25 + graph score + intent boost。
  - 返回候选 chunks：`chunk_id`、`owner_node_id`、`page_title`、`path`、`source_url`、`source_type`、score、text/snippet。
  - 返回后必须 judge。

- `list_page_summaries(query)`
  - 只帮助 agent 选择 page。
  - 直接使用根目录 `page_summaries.json`（不生成新文件）；agent 启动时一次性加载，把 `urls` 映射到 KG 的 `page_id / page_title / source_url`，保存在内存。
  - 不返回 chunk 正文，也不返回完整 page structure。
  - 调用后不执行 judge（仅用于 page 选择，不产生 evidence）。
  - 输出给 LLM（JSON 数组，每项包含）：
    ```text
    label
    description
    page_id
    page_title
    source_url
    ```

- `inspect_page(page_id_or_label_or_title)`
  - 展示该 page 的完整 KG 结构图，不限制结构 depth。
  - 不展示 `HAS_CONTENT` edge，不展示 chunk 正文。
  - 给 LLM 的格式使用 Markdown tree，不用深层 JSON。
  - 每个结构 node 展示：
    ```text
    node.label as node_name [node_id: real node.id] [type: node.type]
    ```
  - `node_name` 来源优先级：
    ```text
    node.label -> node.heading -> node.title -> node.page_title -> node.id
    ```
  - 示例：
    ```text
    # Page Structure: How to Apply
    URL: https://...

    - Page: How to Apply [node_id: page:fe9d95e97a7ee76f] [type: Page]
      - HAS_SECTION -> Page Header Content [node_id: section:3a9d3e4e4b217c96] [type: Section]
      - HAS_SECTION -> Master's in Applied Data Science Application Requirements [node_id: section:f9ec3a741e1e38ce] [type: Section]
      - HAS_SECTION -> Letters of Recommendation [node_id: section:abc123...] [type: Section]
    ```
  - LLM 读 `node_name` 和 `type` 判断语义，复制真实 `node_id` 给后续 tool。

- `fetch_node_chunks(node_id, fetch_depth)`
  - 从某个结构 node 出发，获取该范围内的正文 chunks。
  - `fetch_depth` 限制结构扩散深度，不限制 `HAS_CONTENT` chunk 数量。
  - `fetch_depth=0`：只取当前 node 直接拥有的 chunks。
  - `fetch_depth=1`：取当前 node + 子一级结构 node 的 chunks。
  - `fetch_depth=2`：继续取孙一级结构 node 的 chunks。
  - LLM 可以根据 node type 决定 depth：
    ```text
    Section / Tab / AccordionItem: 通常从 fetch_depth=0 或 1 开始
    Page / TabGroup / broad container: 通常不要直接 fetch，先选更具体的子 node
    Person / Diagram / Table: 通常 fetch_depth=0 就够
    CourseGroup / Quarter: 通常 fetch_depth=1 更可能拿到具体课程内容
    ```
  - 返回给 LLM 的 chunk 信息要精简：
    ```text
    [chunk:8843c4af561c149b]
    Page: How to Apply
    Path: How to Apply > Application Requirements
    Text:
    ...
    ```
  - 完整 metadata 留在 memory/log 里。
  - 返回后必须 judge。

- `fetch_chunk(chunk_id)`
  - 按具体 `chunk_id` 获取单个 chunk 全文（不截断）。
  - 用于 hybrid retrieval 只返回 snippet，但某个 chunk 看起来很可能有用时。
  - 返回给 LLM 的格式与 `fetch_node_chunks` 一致：
    ```text
    [chunk:8843c4af561c149b]
    Page: How to Apply
    Path: How to Apply > Application Requirements
    Text:
    ...full text (no truncation)...
    ```
  - 返回后必须 judge。

## Agent Memory And Evidence
- 每次 `/chat` 维护 request-level memory，不做长期持久化：
  ```text
  run_id
  original_query
  history
  rewritten_queries
  tool_calls
  candidate_pages
  inspected_pages
  evidence_container
  judge_history
  stop_reason
  ```
- `evidence_container` 是一个列表，保存 judge 认为可能用于最终回答的 evidence，每项有一个 `evidence_id`（代码按 `ev_001`、`ev_002` 顺序分配，run 内唯一）和 `source_type` 字段：

  **所有 evidence 项共有字段：**
  ```text
  evidence_id      string   代码分配，ev_001 / ev_002 ...
  source_type      string   "chunk" | "node_structure"
  page_id          string
  page_title       string
  source_url       string   用于 citation
  text             string   实际传给 answer generation 的内容
  why_kept         string   judge 的保留理由
  ```

  **source_type = "chunk" 追加字段（来自 hybrid_retrieve / fetch_node_chunks / fetch_chunk）：**
  ```text
  chunk_id         string
  owner_node_id    string
  owner_node_name  string
  path             string   e.g. "How to Apply > Application Requirements"
  score            float    retrieval score
  retrieval_query  string   哪条 rewritten query 找到的
  ```

  **source_type = "node_structure" 追加字段（来自 inspect_page）：**
  ```text
  （无额外字段；text 字段存储 inspect_page 返回的完整 Markdown tree 原文）
  ```

- 所有 tool call 都记录进 memory/log，但不是所有 tool result 都进入 `evidence_container`。
- 最终 answer generation 只使用 `evidence_container`；citation 代码侧按 `source_url / page_title / text` 构造，与 `source_type` 无关。

## LLM Prompts

> **Ollama 调用规范**
> - 结构化输出（rewrite、decision、judge）：请求里加 `"format": "json"`，确保 qwen3:8b 可靠输出 JSON。
> - Answer generation 输出自由文本，不加 `"format": "json"`。
> - qwen3:8b 默认开启 thinking 模式。Ollama 0.6+ 通常把 thinking 放在独立字段，不影响 JSON 解析；实现 `ollama_client.py` 时需一次实测确认——若 thinking 混入 response 正文则用正则剥离 `<think>...</think>` 块，或在 system prompt 开头加 `/no_think`。

- Query Rewrite Prompt：
  - 只 rewrite 当前用户问题。
  - 使用 history 只解决指代，不 rewrite 历史问题。
  - 把当前问题改写成一个或多个 declarative retrieval queries。
  - 多问题要拆分。
  - JSON 输出：
    ```json
    {
      "rewritten_queries": [
        {"id": "q1", "query": "...", "target": "..."},
        {"id": "q2", "query": "...", "target": "..."}
      ]
    }
    ```

- Agent Decision Prompt：
  - 只选择下一步 tool，不回答问题。
  - 可用 tools：
    ```text
    hybrid_retrieve
    list_page_summaries
    inspect_page
    fetch_node_chunks
    fetch_chunk
    ```
  - 规则：
    ```text
    first action normally hybrid_retrieve
    if retrieval is insufficient, do not repeat similar retrieval
    use page summaries description to choose page
    use inspect_page to read full page structure
    identify a promising node before fetch_node_chunks
    read node_name, node type, and hierarchy
    copy exact node_id from [node_id: ...]
    choose fetch_depth based on node type and specificity
    fetch_depth means how many structural levels below the selected node to include
    stop only when evidence directly answers the question
    ```
  - JSON 输出：
    ```json
    {
      "action": "fetch_node_chunks",
      "args": {
        "node_id": "section:f9ec3a741e1e38ce",
        "fetch_depth": 0
      },
      "reason": "This specific Section node appears to directly contain application requirements."
    }
    ```

- Evidence Judge Prompt：
  - 判断当前 evidence 是否足够直接回答用户问题。
  - 相关但不直接回答的 chunk 不算 sufficient。
  - 多问题必须逐项判断。
  - 不决定 next action；next action 由 Agent Decision Prompt 负责。
  - 对于 chunk 类 tool 的结果（hybrid_retrieve / fetch_node_chunks / fetch_chunk）：用 `chunk_id` 指定 keep/discard。
  - 对于 inspect_page 的结果：如果结构本身直接回答了问题，judge 在 `keep_structural` 里只需指定 `page_id` 和 `why_kept`；代码负责把该 page 的完整 Markdown tree 原文存入 evidence `text` 字段，并分配 `evidence_id` 和补全 `page_title / source_url`。
  - JSON 输出：
    ```json
    {
      "sufficient": false,
      "confidence": 0.35,
      "keep_chunks": ["chunk:abc", "chunk:def"],
      "discard_chunks": ["chunk:zzz"],
      "keep_structural": [
        {
          "source_type": "node_structure",
          "page_id": "page:fe9d95e97a7ee76f",
          "why_kept": "structure directly lists the required courses"
        }
      ],
      "missing": "required application materials list",
      "reason": "..."
    }
    ```
  - `keep_structural` 为空列表时可省略或保留 `[]`。
  - `source_type` 在 `keep_structural` 项中只允许 `"node_structure"`。

- Answer Generation Prompt：
  - LLM 只生成 answer text，不生成 JSON。
  - 只允许使用 evidence list。
  - 使用 `[1]`、`[2]` citation markers。
  - 如果 evidence 不完整（`stop_reason=max_tool_calls` 或 evidence 为空），要明确说明 available evidence 中没有找到什么。

## Context Management For Qwen
- `qwen3:8b` 有上下文长度限制；具体可用 context 取决于 Ollama 本地模型配置和 `num_ctx`。当前环境里 `ollama` 不在 PATH，无法本地确认确切值。
- `qwen3:8b` 默认开启 thinking 模式。Ollama 0.6+ 通常把 thinking 内容放在独立的 `thinking` 字段，`message.content` 只含实际输出，与 `"format": "json"` 兼容。实现 `ollama_client.py` 时需做一次实测确认当前 Ollama 版本的行为；若 thinking 混入 response 正文，则在解析前用正则剥离 `<think>...</think>` 块，或在 system prompt 开头加 `/no_think`。
- 设计上默认要做 context budgeting：
  ```text
  history: 只传最近必要轮次，并优先用于指代消解
  page summaries: 只传 label + description + page id/url
  inspect_page: 传完整结构 tree，但不传 HAS_CONTENT 和 chunk text
  fetch_node_chunks: 传精简 chunk view，不传完整 metadata
  answer generation: 只传 evidence_container，不传全部 tool results
  ```
- 默认预留 token：
  ```text
  answer generation output budget
  judge output budget
  tool decision output budget
  ```
- 如果某个 `inspect_page` 的完整 Markdown tree 仍然太大：
  - log 中保留完整 tree。
  - LLM-visible tree 仍保持 full-depth，但可以按 top-level section 分批返回；agent 再选择下一批 section。
  - 不用 depth 截断结构，因为用户希望 inspect_page 展现图能力；只做上下文分批。

## Output Interface
- Final response JSON 由代码生成：
  ```json
  {
    "answer": "Applicants need to submit ... [1]",
    "citations": [
      {
        "index": 1,
        "title": "How to Apply",
        "source_url": "https://...",
        "snippet": "Letters of Recommendation, Candidate Statement..."
      }
    ],
    "debug": {
      "run_id": "...",
      "log_path": "log/2026-05-08_153012_xxx.json",
      "rewritten_queries": ["..."],
      "tool_call_count": 5,
      "stop_reason": "sufficient_evidence"
    }
  }
  ```
- `citations` 由代码按 `evidence_id` 顺序从 `evidence_container` 取 `source_url / page_title / text` 构造，与 `source_type` 无关。
- `snippet` 由代码从 evidence `text` 字段截取 80 到 150 字符。
- `stop_reason` 取值：`"sufficient_evidence"` | `"max_tool_calls"` | `"empty_evidence"`。

## Logging
- 每次 `/chat` 写一个 log：
  ```text
  log/YYYY-MM-DD_HHMMSS_<run_id>.json
  ```
- log 包含：
  ```text
  request
  rewritten_queries
  tool_calls
  judge_history
  evidence_container
  final answer prompt
  raw answer text
  parsed citation markers
  final response
  ```
- 每个 tool call 记录：
  ```text
  step
  tool_name
  arguments
  result_summary
  returned_chunk_ids
  returned_page_ids
  returned_node_ids
  llm_visible_result
  ```

## Implementation Changes
- 新增 agent 后端模块：
  ```text
  app.py
  agent/prompts.py
  agent/schemas.py
  agent/ollama_client.py
  agent/query_rewriter.py
  agent/tools.py
  agent/agent_loop.py
  agent/answer_generator.py
  agent/logger.py
  ```
- 新增可复用 RAG core：
  ```text
  grag/retriever.py
  grag/graph_tools.py
  ```
- `retrieve.py` 保留 CLI，但内部改为调用 `grag/retriever.py`。
- `build_index.py` 不改动 `page_summaries.json`；agent 启动时直接读根目录 `page_summaries.json`，一次性把 `urls` 映射到 KG 的 `page_id / page_title / source_url`，存入内存供 `list_page_summaries` 使用。
- `agent/ollama_client.py` 规范：
  - rewrite / decision / judge 三类调用加 `"format": "json"`，代码用 `json.loads()` 解析输出。
  - answer generation 不加 `"format": "json"`，LLM 只输出带 `[n]` 标记的自由文本；citation 的 `source_url / title / snippet` 全部由代码从 `evidence_container` 按 `evidence_id` 顺序构造，与 `source_type` 无关，不由 LLM 生成。
  - 实现时确认当前 Ollama 版本对 qwen3:8b thinking 的处理方式，必要时剥离 `<think>...</think>` 块再解析 JSON。
- `agent/prompts.py` 放 prompt；`agent/schemas.py` 只放 request/response/evidence/memory/tool call 数据结构，不放 prompt。

## Test Cases
- Query rewrite：
  - 输出为合法 JSON，包含 `rewritten_queries` 数组。
  - 当前单问题改写为 declarative statement。
  - 当前多问题拆成多个 retrieval queries。
  - history 只用于解决指代，不生成历史问题的 rewritten queries。
- Agent Decision：
  - 输出为合法 JSON，包含 `action` / `args` / `reason` 字段。
  - `args` 中 `node_id` 与 `inspect_page` 输出的真实 `node_id` 一致（不是 node_name）。
- Evidence Judge：
  - 输出为合法 JSON，包含 `sufficient` / `confidence` / `keep_chunks` / `discard_chunks` / `keep_structural` / `missing` / `reason`。
  - 不包含 `next_action` / `next_args`（next action 由 Agent Decision 负责）。
  - `sufficient=true` 时 loop 提前终止。
  - 9 次 tool call 后仍 `sufficient=false`：loop 结束，用当前 evidence 生成答案。
  - inspect_page 后 judge 认为结构本身可回答时：`keep_structural` 非空，每项只含 `source_type / page_id / why_kept`；代码从 inspect_page 结果中取完整 Markdown tree 原文存入 `text`，补全 `evidence_id / page_title / source_url` 并写入 evidence_container。
  - evidence_container 中所有项共有 `evidence_id / source_type / page_id / page_title / source_url / text / why_kept`；chunk 类项额外含 `chunk_id / score`；node_structure 类项无额外字段。
- Tool behavior：
  - `list_page_summaries` 只返回 label/description/page mapping，不返回 node lists；数据来自根目录 `page_summaries.json` 加 KG 映射。
  - `inspect_page` 输出完整 Markdown tree，包含 `node.label`、真实 `node.id`、`node.type`。
  - `inspect_page` 不输出 `HAS_CONTENT` 和 chunk 正文。
  - `fetch_node_chunks(fetch_depth=0)` 只取当前 node chunks。
  - `fetch_node_chunks(fetch_depth=1)` 获取当前 node 和子一级结构 node 的 chunks，不限制 chunk 数量。
  - `fetch_chunk` 返回单个 chunk 全文，格式与 `fetch_node_chunks` 一致。
- Agent loop：
  - top-k 不足时，agent 会使用 page summaries 和 inspect_page，而不是重复相似 retrieval。
  - agent 能根据 node type 选择 `fetch_depth=0` 或 `fetch_depth=1`。
  - evidence 足够时提前停止，不跑满 9 轮。
- Response：
  - LLM 输出带 `[n]` citation markers 的自由文本。
  - `answer_parts` 和 `citations` 由代码从 `evidence_container` 构造，不由 LLM 生成 JSON。
  - citations 中 `source_url`、`title`、`snippet` 与 chunk_id 对应。
- Logging：
  - 一次 `/chat` 只生成一个完整 log。
  - log 可还原 rewrite、tool calls、judge、evidence selection、final answer。

## RAGAS Evaluation Notebook

- 文件：`eval/ragas_eval.ipynb`
- 目标：用 RAGAS 对完整 RAG agent pipeline 做端对端质量评估。
- 问题集：根目录 `eval_queries.json`（已有 `question` + `ground_truth_answer` + `gold_urls`）。
- Evaluator LLM：qwen3:8b via Ollama；embeddings 复用 index 的 BAAI/bge-small-en-v1.5。

### 依赖

```
ragas>=0.2
langchain-ollama
langchain-community
```

### RAGAS 接入 Ollama

```python
from ragas.llms import LangchainLLMWrapper
from ragas.embeddings import LangchainEmbeddingsWrapper
from langchain_ollama import ChatOllama
from langchain_community.embeddings import HuggingFaceEmbeddings

llm = LangchainLLMWrapper(
    ChatOllama(model="qwen3:8b", base_url="http://localhost:11434")
)
embeddings = LangchainEmbeddingsWrapper(
    HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
)
```

> **注意**：qwen3:8b thinking 模式可能干扰 RAGAS 对输出的解析。建议在 ChatOllama 初始化时加 `system="/no_think"` 或通过 `model_kwargs={"options": {"stop": ["</think>"]}}` 禁用；或实现时实测默认行为是否兼容。

### 数据集构建

对 `eval_queries.json` 中每条记录运行 agent（调用 `agent_loop.run(query)`），收集：

```text
user_input        question 字段
response          agent 生成的 answer text
retrieved_contexts evidence_container 中所有 evidence 的 text 字段（列表）
reference         ground_truth_answer 字段
```

构造 RAGAS `EvaluationDataset`：

```python
from ragas import EvaluationDataset, SingleTurnSample

samples = [
    SingleTurnSample(
        user_input=row["question"],
        response=row["agent_answer"],
        retrieved_contexts=row["contexts"],   # List[str]
        reference=row["ground_truth_answer"]
    )
    for row in results
]
dataset = EvaluationDataset(samples=samples)
```

### 评估 Metrics

四个主要 metric，全部可用（因为有 `ground_truth_answer`）：

```text
Faithfulness       answer 是否由 context 支持，无幻觉（不需要 reference）
AnswerRelevancy    answer 是否切题（不需要 reference）
ContextPrecision   retrieved context 中相关内容的比例（需要 reference）
ContextRecall      retrieved context 是否覆盖 reference 中的关键信息（需要 reference）
```

```python
from ragas import evaluate
from ragas.metrics import Faithfulness, AnswerRelevancy, ContextPrecision, ContextRecall

result = evaluate(
    dataset=dataset,
    metrics=[Faithfulness(), AnswerRelevancy(), ContextPrecision(), ContextRecall()],
    llm=llm,
    embeddings=embeddings,
)
df = result.to_pandas()
df.to_csv("eval/ragas_results.csv", index=False)
```

### Notebook 结构

```text
1. 环境检查：ragas 版本、Ollama 连通性（GET /api/tags）、index/ 文件是否存在
2. 加载 eval_queries.json
3. 逐条运行 agent_loop，收集 answer + contexts（tqdm 进度条）
   - 把每条运行结果序列化缓存到 eval/agent_run_cache.json，避免重复调用
4. 构造 EvaluationDataset
5. 配置 RAGAS LLM + embeddings
6. 运行 evaluate()
7. 打印各 metric 均值（DataFrame.mean()）
8. 低分样本分析：每个 metric 最低 5 条，展示 question / response / contexts / score
9. 导出 eval/ragas_results.csv
```

### 规模估算

```text
eval_queries.json 约 30 条
每条 × 4 metrics × 约 2 次 LLM 调用 ≈ 240 次 Ollama 调用
qwen3:8b 本地推理，预计总耗时 20–40 分钟
```

## Assumptions
- LLM 全部使用 Ollama `qwen3:8b`。
- Ollama 的具体 context size 需要实现时通过本地 `ollama show qwen3:8b` 或 API 配置确认；当前 plan 按有限上下文设计。
- 当前 KG 的真实 ID 格式保持为 `type:hash`，例如 `section:f9ec3a741e1e38ce`。
- debug HTML 中看到的语义文字主要来自 KG node 的 `label`。
- 可用于最终回答的正文 evidence 都是 Chunk node，通常通过 `HAS_CONTENT` 挂在结构 node 下。
- 前端维护 conversation history；后端只接收当前 request 中传入的 history。
