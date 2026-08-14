# Retrieval 优化方案：Metadata Filter + Contextual Chunking

## Context

Stage 2 evaluation（retrieval_eval.ipynb Section 9）发现：含 "in-person"/"online" 限定词的 query 会召回错误 scope 的 chunk。根因：无 program_type 标签；chunk embedding 缺层级上下文；无整体 overview 导致"所有课程"类查询需要多个碎片 chunk 才能回答。

修复三轨：**元数据过滤**、**breadcrumb 上下文增强**、**accordion_overview JSON chunk**。

---

## 现有关键文件

| 文件 | 当前状态 |
|------|---------|
| `src/retrieval/cleaner.py` | `sections: [{section, text, content_type}]`，无 program_type / breadcrumb / overview |
| `src/retrieval/chunker.py` | 10 字段 chunk metadata；均走 RecursiveCharacterTextSplitter |
| `src/retrieval/embedding_DB.py` | embedded text = `f"{section}\n{body}"`；5 个集合 |
| `src/retrieval/retriever.py` | `retrieve(query, top_k)` 无 where filter |
| `data/chunks/size_{256,512,900,1536,1900}/` | 5 套 chunk JSON |

---

## 已确认的 DOM 结构（in-person / online 页面）

两页 accordion 结构完全相同，两级嵌套：

```
div.list-accordion
  ul.accordion  (第1层 — program tracks / topics)
    li.accordion-item  ×3  (e.g. "Noncredit Courses", "1-Year Program", "2-Year Thesis Track")
      a.accordion-title  → tab 标题（触发元素）
      div.accordion-content
        ul.accordion  (第2层 — individual courses)
          li.accordion__item  ×27-30
            a.accordion-title  → 课程名
            div.accordion__content
              div.textblock  → 课程描述
```

其中 `instructors_staff` 和 `course_progressions` 使用不同 DOM 模式（各自已有特殊 cleaner 处理）。

---

## program_type 分类策略

**URL 级别分类（3 类）：**
- `"in_person"` — URL 含 `in-person-program`
- `"online"` — URL 含 `online-program`
- `"general"` — 其余所有

**过滤逻辑用 `$ne`**（排除错误项目，general 永远可见）：
- query → `in_person` → `{"program_type": {"$ne": "online"}}`
- query → `online` → `{"program_type": {"$ne": "in_person"}}`
- query → `general` → None（无过滤）

---

## Step 1 — `cleaner.py`：5 处改动

### 1a. 新增 `_derive_program_type(url: str) -> str`

```python
def _derive_program_type(url: str) -> str:
    if "in-person-program" in url:
        return "in_person"
    if "online-program" in url:
        return "online"
    return "general"
```

在 `clean_page()` 调用：`cleaned["program_type"] = _derive_program_type(url)`

---

### 1b. 新增共享子函数 `_accordion_to_sections()`

职责：接收已解析好的 (hierarchy_parts, text, content_type) 元组列表，统一构造 section dict + breadcrumb，可选生成 JSON overview chunk。  
被调用方：`_extract_list_accordion()`（1c）、`_clean_instructors_staff()`、`_clean_course_progressions()`（各自负责 HTML 解析，只是最后 emit 走这里）。

```python
import json

def _accordion_to_sections(
    items: list[tuple[list[str], str, str]],
    # (hierarchy_parts, text, content_type)
    overview_data: dict | None = None,
    overview_section: str = "Overview",
    program_type: str = "general",
) -> list[dict]:
    sections = []
    
    if overview_data:
        sections.append({
            "section":            overview_section,
            "section_breadcrumb": overview_section,
            "text":               json.dumps(overview_data, ensure_ascii=False, indent=2),
            "content_type":       "accordion_overview",
            "program_type":       program_type,
        })
    
    for hierarchy_parts, text, content_type in items:
        section_name = hierarchy_parts[-1]
        breadcrumb   = " > ".join(p for p in hierarchy_parts if p)
        sections.append({
            "section":            section_name,
            "section_breadcrumb": breadcrumb,
            "text":               text,
            "content_type":       content_type,
            "program_type":       program_type,
        })
    
    return sections
```

---

### 1c. 新增 `_extract_list_accordion()` — 通用两级 accordion 处理器

`extract_sections()` 遇到 `div.list-accordion` 时，跳过正常递归，转而调用此函数：

```python
def _extract_list_accordion(
    acc_div,                              # BS4: div.list-accordion
    heading_stack: list[tuple[int, str]], # 当前 heading 栈（外层 extract_sections 传入）
    page_title: str,
    program_type: str,
) -> list[dict]:
    """
    处理 div.list-accordion > ul.accordion > li.accordion-item > ... > li.accordion__item 结构。
    输出：一个 accordion_overview chunk + N 个 per-course detail chunk。
    """
    overview_data = {}
    items = []
    
    heading_bc = " > ".join(t for _, t in heading_stack)
    
    for tab_li in acc_div.select("li.accordion-item"):
        tab_name_el = tab_li.select_one("a.accordion-title")
        if not tab_name_el:
            continue
        tab_name = tab_name_el.get_text(strip=True)
        course_names = []
        
        for item_li in tab_li.select("li.accordion__item"):
            title_el = item_li.select_one("a.accordion-title")
            body_el  = item_li.select_one(".accordion__content .textblock")
            if not title_el:
                continue
            
            item_title = title_el.get_text(strip=True)
            item_body  = body_el.get_text(" ", strip=True) if body_el else ""
            course_names.append(item_title)
            
            # 构造 hierarchy：外层 heading 路径 + tab 名 + 课程名
            hierarchy = ([heading_bc] if heading_bc else []) + [tab_name, item_title]
            items.append((hierarchy, item_body, "text"))
        
        overview_data[tab_name] = course_names
    
    # overview section 名以 heading 路径为 root
    overview_sec = (f"{heading_bc} > " if heading_bc else "") + "Courses Overview"
    
    return _accordion_to_sections(
        items,
        overview_data=overview_data,
        overview_section=overview_sec,
        program_type=program_type,
    )
```

**输入/输出示例（in-person program page）：**
```
div.list-accordion 包含 3 个 tab × ~10 课程

overview chunk:
  content_type: "accordion_overview"
  section:      "Courses Overview"
  text (JSON):  {
    "Noncredit Courses": ["Career Seminar (Seminar, required)", "Introduction to Statistical Concepts (Foundational, optional)"],
    "1-Year 12 Course Program": ["Machine Learning I (Core)", "Machine Learning II (Core)", ...30 courses],
    "2-Year Thesis Track":      [...]
  }

detail chunk (×30):
  section:            "Machine Learning I (Core)"
  section_breadcrumb: "1-Year 12 Course Program > Machine Learning I (Core)"
  text:               "The Pass/Fail Career Seminar supports..."
  content_type:       "text"
  program_type:       "in_person"
```

**⚠️ 注意事项：**
- `extract_sections()` 的遍历逻辑需要在遇到 `div.list-accordion` 时 `continue`（跳过子树递归），否则会重复处理
- 如果 accordion 里套有更多文本（非 accordion__item）也要考虑，但当前 DOM 结构确认只有两级，不会出现额外文本
- 课程名（`a.accordion-title`）已包含类型信息（如 "Core"、"Elective"、"Foundational"），保留原文，不截断

---

### 1d. 修改 `extract_sections()`：heading 栈 + 遇到 div.list-accordion 转接

改动1：维护 `heading_stack: list[tuple[int, str]]`：
- 遇到 hN 弹出所有 level ≥ N 的条目，push `(N, heading_text)`
- 每个 section dict 新增 `section_breadcrumb` = `" > ".join(t for _, t in heading_stack)`

改动2：遍历时检测 `div.list-accordion`：
```python
if child.name == "div" and "list-accordion" in child.get("class", []):
    new_sections = _extract_list_accordion(
        child, heading_stack, page_title, program_type
    )
    sections.extend(new_sections)
    continue   # 不递归进子树
```

改动3：所有 section dict 新增 `program_type = program_type`（从函数参数传入）

---

### 1e. 更新 `_clean_instructors_staff()` 和 `_clean_course_progressions()`

两个函数不走 `extract_sections()`，自行处理 HTML。改动：
- **保留**各自的 DOM 解析逻辑（结构差异大）
- **删除** `_clean_instructors_staff()` 里现有的非结构化 overview/intro 文本 chunk（旧实现会把分组名单以 plain text 形式输出）—— 防止与新的 JSON accordion_overview 重复
- **改变**最终 emit：从直接构造 section dict，改为调用 `_accordion_to_sections(overview_data, items, ...)`
- 构造各自的 `overview_data`：
  - instructors_staff: `{"Faculty": ["Alice Chen", ...], "Staff": [...], ...}`（JSON 替换原来的 plain text 分组列表）
  - course_progressions: `{"Full-Time": {"Fall Quarter": ["ML I (3cr)", ...]}, "Online": {...}, ...}`（overview 值只放课程名+学分，不放全文）
- 两者均传 `program_type = cleaned["program_type"]`（都是 `"general"`）
- `_parse_dataviz_scripts()`: 直接在 section dict 加 `section_breadcrumb = section_name`、`program_type = cleaned["program_type"]`

---

## Step 2 — `chunker.py`：新增 accordion_overview 分块逻辑 + metadata 字段

### 2a. 新增 `_split_accordion_overview()` — 递归按 JSON key 拆分

```python
import json

def _split_accordion_overview(
    json_text: str,
    section: str,
    section_breadcrumb: str,
    max_size: int,
    base_meta: dict,
) -> list[dict]:
    """
    保证每个输出 chunk 都是完整的 JSON 子树。
    Level 0: 全量 JSON ≤ max_size → 直接返回
    Level 1: 按顶层 key 拆分
    Level 2: 若 level-1 chunk 仍超限，按第二层 key 拆分
    """
    def _make(data: dict, sec: str, bc: str) -> dict:
        text = json.dumps(data, ensure_ascii=False, indent=2)
        return {**base_meta, "text": text, "section": sec,
                "section_breadcrumb": bc, "content_type": "accordion_overview",
                "word_count": len(text.split())}
    
    data = json.loads(json_text)
    if len(json_text) <= max_size:
        return [_make(data, section, section_breadcrumb)]
    
    chunks = []
    for top_key, top_val in data.items():
        sub = {top_key: top_val}
        sub_text = json.dumps(sub, ensure_ascii=False, indent=2)
        sub_bc = f"{section_breadcrumb} > {top_key}"
        if len(sub_text) <= max_size:
            chunks.append(_make(sub, top_key, sub_bc))
        else:
            if isinstance(top_val, dict):
                for mid_key, mid_val in top_val.items():
                    sub2 = {top_key: {mid_key: mid_val}}
                    sub2_bc = f"{sub_bc} > {mid_key}"
                    chunks.append(_make(sub2, f"{top_key} > {mid_key}", sub2_bc))
            else:
                chunks.append(_make(sub, top_key, sub_bc))  # 超限也整块输出
    return chunks
```

**拆分示例（in-person courses overview，size_512）：**
```
全量 JSON "Noncredit + 1-Year + 2-Year" ~2000 chars > 512
  → 按顶层 key 拆：
    {"Noncredit Courses": [...]}  ~200 chars ≤ 512 → 1 chunk ✓
    {"1-Year 12 Course Program": [...]} ~900 chars > 512
      → 按第二层拆：不适用（top_val 是 list 不是 dict）→ 整块输出（允许超限）
    {"2-Year Thesis Track": [...]} ~800 chars > 512 → 整块输出
```

**⚠️ 说明：** in-person 的 overview 每个 tab 的值是 list（课程名列表），不是 dict，无法继续细分。但因为只是课程名（短字符串），实际超限风险低。course_progressions 的 overview 值是 dict（quarter → courses），可以到第二层继续拆。

### 2b. `chunk_page()` 主流程增加 accordion_overview 分支

```python
for block in cleaned["sections"]:
    if block["content_type"] == "accordion_overview":
        new_chunks = _split_accordion_overview(
            json_text=block["text"],
            section=block["section"],
            section_breadcrumb=block["section_breadcrumb"],
            max_size=chunk_size,
            base_meta={...},  # source_url, page_title, program_type, scraped_at, url_aliases
        )
        chunks.extend(new_chunks)
    else:
        # 原有 RecursiveCharacterTextSplitter 路径，新增 program_type + section_breadcrumb
        ...
```

### 2c. 所有 chunk metadata 新增 2 字段

```python
"program_type":       block.get("program_type", cleaned.get("program_type", "general")),
"section_breadcrumb": block.get("section_breadcrumb", block["section"]),
```

---

## Step 3 — `embedding_DB.py`：metadata + embedded text

### 3a. metadata 新增 2 字段
```python
"program_type":       chunk["program_type"],
"section_breadcrumb": chunk["section_breadcrumb"],
```

### 3b. embedded text 改用 breadcrumb 前缀
```python
breadcrumb = chunk.get("section_breadcrumb") or chunk.get("section", "")
page_title = chunk.get("page_title", "")
prefix = f"[Page: {page_title} | Section: {breadcrumb}]"
texts.append(f"{prefix}\n\n{body}")
```

**accordion_overview chunk 的 body 已是 JSON string，前缀照样拼接。**

**Token 安全边界：**
- MiniLM size_512：512 + ~100 ≈ 612 chars ≈ 153 tokens（限制 256，安全）
- BGE size_1900：1900 + ~100 ≈ 2000 chars ≈ 500 tokens（限制 512，安全）
- 全部 5 个集合全量重建

---

## Step 4 — 新建 `src/retrieval/query_router.py` + 更新 `retriever.py`

### 4a. `src/retrieval/query_router.py`（新文件）

```python
from openai import OpenAI

_SYSTEM_PROMPT = (
    "You classify user queries about a university MS in Applied Data Science program. "
    "Reply with ONLY one word:\n"
    "- 'in_person' — query specifically asks about in-person/campus/Chicago/full-time/part-time program\n"
    "- 'online'    — query specifically asks about online/remote program\n"
    "- 'general'   — covers both, neither, or no specific program type mentioned"
)

def route_query(query: str, client: OpenAI) -> dict | None:
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user",   "content": query},
        ],
        max_tokens=5,
        temperature=0,
    )
    label = resp.choices[0].message.content.strip().lower()
    if label == "in_person":
        return {"program_type": {"$ne": "online"}}
    if label == "online":
        return {"program_type": {"$ne": "in_person"}}
    return None   # general → 无过滤
```

### 4b. `retriever.py`：新增 where 参数

```python
def retrieve(self, query: str, top_k: int = 5, where: dict | None = None) -> list[dict]:
    kwargs = dict(query_embeddings=[vec], n_results=top_k,
                  include=["documents", "metadatas", "distances"])
    if where:
        kwargs["where"] = where
    raw = self._collection.query(**kwargs)
    ...
```

**Stage 3 调用方式（在 `src/qa/` 里）：**
```python
from src.retrieval.query_router import route_query
where  = route_query(user_query, deepseek_client)
chunks = retriever.retrieve(user_query, top_k=5, where=where)
```

---

## Step 5 — 重建 ChromaDB

```bash
conda activate adsp-nlp-backup
python src/retrieval/chunker.py
python src/retrieval/embedding_DB.py
```

集合名不变：`msads_minilm_size_{512,900}` + `msads_bgesmall_size_{900,1536,1900}`

---

## Step 6 — 更新 eval

- `eval/retrieval_eval.ipynb`：Section 9 改用 where filter 重跑，对比路由前后 top-3
- `tests/validate_stage_2_cleaning.py`：新增 program_type + section_breadcrumb 字段检查

---

## 验证方法

1. Q01（in-person core courses）→ top-3 全来自 in-person，且 accordion_overview chunk 排名靠前
2. Q06（difference between programs）→ None，两种来源都出现
3. Q10（dress code）→ 路由返回 None，行为不变
4. 重跑 Hit@5 / MRR@5 对比

---

## 实施顺序

```
Step 1a  cleaner.py — _derive_program_type()
Step 1b  cleaner.py — _accordion_to_sections() 共享子函数
Step 1c  cleaner.py — _extract_list_accordion() 通用 div.list-accordion 处理器
Step 1d  cleaner.py — extract_sections() 改动：heading 栈 + 遇到 div.list-accordion 转接
Step 1e  cleaner.py — 重构 _clean_instructors_staff() / _clean_course_progressions() emit 逻辑
         + 各自构建 overview_data dict
         + _parse_dataviz_scripts() patch
Step 2a  chunker.py — _split_accordion_overview() 递归 JSON 拆分
Step 2b  chunker.py — chunk_page() 增加 accordion_overview 分支
Step 2c  chunker.py — 所有 chunk 新增 program_type + section_breadcrumb 字段
Step 3   embedding_DB.py — metadata + embedded text 前缀
Step 4a  query_router.py — 新建 LLM 路由模块（新文件）
Step 4b  retriever.py — 新增 where 参数
Step 5   重建 ChromaDB — 全量 rebuild（5 个集合）
Step 6   eval 重跑验证
```
