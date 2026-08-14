# Stage 4 前的 RAG Agent 优化点 + Stage 4 架构设计

## Context

Stage 3 QA pipeline 已完成，当前是单轮单问题 RAG agent。Stage 4 是 Streamlit UI。目标：在进入 Stage 4 前识别 3 个优化点，明确 Stage 4 的架构范围。

---

## 3 个优化点

### #1 多问题扇出

**问题**：`_rewrite_query()` 把输入压成单个陈述句，agent loop 只走一条路。用户一次问多个独立问题时，某个问题会被漏掉。

**方案**：`run()` 入口加 `_detect_subquestions(query)`（一次 LLM 调用），若检测到 N>1 个独立子问题，循环走 N 遍 rewrite → agent loop，合并 useful_chunks → 统一 `_generate()` 生成综合回答。

**改动**：仅 `src/qa/qa_pipeline.py`。

---

### #2 多轮对话 + History 压缩

**两个子问题**：

- **Rewrite 侧（指代消解）**：用户问"那在线版本呢？"，必须有历史才能 rewrite 成有意义的陈述句。传 summary + 最近 3 轮完整 history 给 `_rewrite_query()`（与 generation 用同一份压缩后的 history 对象，不单独截断）。
- **Generation 侧**：`_generate()` 需要完整对话历史（同一份 history 对象）保持上下文连贯。

**History 管理策略（渐进压缩）**：
- 用户每次打开 app → `pipeline_history = []` 从空开始；整个对话期间持续累积
- 当 history 超过 3 轮完整 exchange（即 6 条消息）时，对最早的 3 轮做一次 LLM summarize，压缩成 1 条 `{"role": "system", "content": "Earlier conversation summary: ..."}` 插在 history 头部
- 之后每满 3 轮再压缩一次，summary 会滚动合并进去
- 结果：history 始终是 `[summary(1)] + [最近3轮(6条)]` = 7 条，不膨胀也不丢失信息

**压缩位置**：`src/ui/app.py` 里在每次提交新消息前调用 `_compress_history()` helper（一次 LLM 调用）。Pipeline 接收已压缩好的 history，自己不管理压缩。

**改动范围**：
| 文件 | 改动 |
|------|------|
| `src/qa/qa_pipeline.py` | `run(query, history=None, progress_cb=None)`；`_rewrite_query(query, history)` 和 `_generate()` 都传同一份 history |
| `src/qa/prompt_templates.py` | `format_rewrite_prompt()` + generation messages 加 history |
| `src/ui/app.py` | session state + `_compress_history()` helper |

---

### #3 流式输出 + 内联引用角标 + 进度状态

**流式输出**：`_generate()` 加 `stream=False` 默认参数。Stage 4 传 `stream=True`，Streamlit 用 `st.write_stream()` 实时"打字"输出。Sources 区域在 stream 完成后展示。

**实时进度状态（新增）**：由于 LLM 在 rewrite / tool 调用 / judge 阶段用户也要等，stream 之前就应该有反馈。通过给 `run()` 传一个 `progress_cb: callable | None = None` 回调函数，pipeline 在关键步骤调用 `progress_cb("消息")` 更新 UI。

pipeline 在以下节点调用 progress_cb：
- rewrite 开始前：`"Rewriting your query..."`
- 每次 agent tool call 前：`"Fetching information from [page group]..."` 或 `"Running semantic search..."`
- judge 调用后：`"Evaluating results... (step N/9)"`
- generate 开始前：`"Generating answer..."`

Streamlit 用 `st.status()` 展示（Streamlit 1.28+ 内置，会显示一个可展开的 live 状态盒子）：
```python
with st.status("Searching for information...", expanded=True) as status_box:
    result = pipeline.run(query, history=..., stream=True,
                          progress_cb=lambda msg: status_box.update(label=msg))
# stream 完成后 status_box 自动收起
```

**内联引用角标**：改 `SYSTEM_PROMPT` Rule 5，要求 LLM 答案中用 `[1][2]` 角标标注引用来源，底部来源列表用 `1. [Title](URL)` 格式。Streamlit 渲染为可点击超链接。

**Low-confidence 提示（人性化）**：当 `low_confidence=True` 时，不显示技术性的 "Partial match"，而是在 bot 回答末尾追加一句：

> *This topic may not be fully covered in the available materials. For more details, you can check the [MS in Applied Data Science program page](https://datascience.uchicago.edu/...).*

URL 优先用 result 里 sources 里最相关的那个，没有则用 MSADS 主页。这句话由 UI 层追加（不是 LLM 生成），确保措辞一致。

**改动范围**：`src/qa/qa_pipeline.py`（`_generate()` stream + `progress_cb` 调用点）、`src/qa/prompt_templates.py`（SYSTEM_PROMPT Rule 5 角标规则）、`src/ui/app.py`（st.status + st.write_stream + low_confidence UI 追加）

---

## Stage 4 架构设计

### 推荐实现顺序

**Part A：Pipeline 升级（先做，~1h，可独立测试）**
- `run(query, history=None, stream=False, progress_cb=None)` 签名（所有新参数均有默认值，向后兼容）
- `_rewrite_query()` 和 `_generate()` 传同一份 history
- `_generate()` 加 stream 分支
- pipeline 各关键步骤加 `if progress_cb: progress_cb("...")` 调用
- `SYSTEM_PROMPT` Rule 5 改为内联角标格式
- 可在 `demo.py` 里手动构造 history + dummy progress_cb 验证
- **Part A 完成后：跑 `tests/validate_stage_3.py`，确认 9/9 检查仍全过，然后单独 commit：`[Stage 4] Upgrade qa pipeline: history + stream + progress_cb`** → 这是 Part B 出问题时的 git 恢复点

**Part B：Streamlit UI（主工作，~2-3h）**

文件位置：`src/ui/app.py`（启动：`streamlit run src/ui/app.py`，访问 `localhost:8501`）

UI 布局：
```
┌──────────────────────────────────────────┐
│  🎓 UChicago MSADS Assistant             │  ← 顶部标题
├──────────────────────────────────────────┤
│                                          │
│  [User] What are the core courses?       │
│                                          │
│  ▶ Searching for information... (收起)   │  ← st.status 实时状态
│                                          │
│  [Bot] The core courses include...  [1]  │  ← stream 输出 + 角标
│        Typically taken in Q1/Q2.   [2]  │
│                                          │
│        1. Curriculum (link)              │  ← 编号超链接 sources
│        2. FAQ (link)                     │
│                                          │
│        ℹ This topic may not be fully    │  ← 仅 low_confidence 时
│          covered. Check program page.    │
│                                          │
│  [User] What about the online version?  │
│  [Bot] (streaming text...)               │
├──────────────────────────────────────────┤
│  Ask about the MSADS program...  [Send]  │  ← st.chat_input
└──────────────────────────────────────────┘
```

Session state 设计：
```python
st.session_state = {
    "messages": [                        # 显示用，保留完整对话用于渲染
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "...", "sources": [...], "low_confidence": False}
    ],
    "pipeline_history": [               # 传给 pipeline 的压缩版 history
        {"role": "system", "content": "Earlier summary: ..."},  # 可选
        {"role": "user", "content": "..."},
        {"role": "assistant", "content": "..."},
        ...                             # 最近 3 轮
    ]
}
```

### 关键文件列表

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/qa/qa_pipeline.py` | 修改 | run() 加 history / stream / progress_cb；rewrite+generate 接 history |
| `src/qa/prompt_templates.py` | 修改 | history 注入 + SYSTEM_PROMPT 角标规则 |
| `src/ui/app.py` | 新建 | Streamlit 主界面 |
| `tests/validate_stage_4.py` | 新建 | pipeline 加载 + UI 可导入验证 |
| `requirements.txt` | 确认 | 已含 streamlit（CLAUDE.md 要求 Stage 4 前 pip install） |

### Stage 4 验证清单
- `streamlit run src/ui/app.py` 启动无报错，`localhost:8501` 可访问
- 多轮指代消解：问一个问题 → 问"那在线版本呢？" → rewrite 输出正确的陈述句
- 多问题扇出：一次问两个独立问题 → 两个都被回答
- 压缩测试：连续问 4+ 轮 → history 被压缩且语境不丢失
- 角标测试：答案中有 [1][2] 且底部列表可点击跳转
- 进度状态：发送问题后立即看到 st.status 状态更新
- low_confidence：触发时显示人性化提示而不是技术指标
