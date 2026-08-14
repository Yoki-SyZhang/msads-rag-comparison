# GRAG Pipeline: Graph Hybrid RAG for UChicago MSADS

This pipeline is the graph-based counterpart used in the repository-level comparison UI.
It keeps the original GRAG design:

- Playwright-rendered HTML scraping.
- DOM-aware knowledge graph construction.
- Hybrid retrieval with vector similarity, BM25, graph path score, and intent boosts.
- Multi-step agent tools for page inspection and node-level evidence gathering.

For the comparison path, all LLM calls use the DeepSeek API. The historical Ollama client has been removed from the runtime code; old notebooks or design notes may still mention it as historical context.

## Main Files

| File | Role |
|---|---|
| `scrape.py` | Render and scrape MSADS pages into `raw/`. |
| `build_index.py` | Build KG, chunks, Chroma vector store, BM25, and metadata under `index/`. |
| `retrieve.py` | Standalone hybrid retrieval CLI. |
| `grag/kg_builder.py` | Parse rendered HTML into a DOM-aware KG. |
| `grag/retriever.py` | Fuse vector, BM25, graph, and intent scores. |
| `grag/graph_tools.py` | Navigate pages, nodes, and chunks in the KG. |
| `agent/deepseek_client.py` | DeepSeek API client for JSON and text generation. |
| `agent/agent_loop.py` | Query rewrite, agent decision, tool execution, evidence judge loop. |
| `agent/answer_generator.py` | Final answer synthesis and citation extraction. |
| `agent/tools.py` | Five agent tools: hybrid retrieve, page summaries, inspect page, fetch node chunks, fetch chunk. |
| `app.py` | FastAPI endpoint using DeepSeek. The repository-level comparison UI does not require starting this server. |

## Data And Indexes

```text
raw/             # rendered HTML JSON
processed/       # debug artifacts and graph/chunk inspection files
index/           # knowledge_graph.json, chunks.json, Chroma, BM25, metadata
log/             # GRAG agent run logs
docs/            # URL config, page summaries, historical eval materials
```

The repository-level comparison does not unify this URL config with FlatRAG. Questions should be chosen from the intersection of the two pipelines' indexed URL coverage.

## Run

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY="your_deepseek_api_key"
python build_index.py
python retrieve.py "What are the core courses?"
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

On Windows PowerShell:

```powershell
pip install -r requirements.txt
$env:DEEPSEEK_API_KEY="your_deepseek_api_key"
python build_index.py
python retrieve.py "What are the core courses?"
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

## Comparison UI

The preferred way to compare this pipeline with FlatRAG is from the repo root:

```bash
streamlit run ui/app.py
```
