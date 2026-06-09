# Benchmarks

## Retrieval quality — does it find the right code? (`tests/eval_code_search.py`)

The cost benchmark below shows mememo is *heavier*; this one asks whether that buys
anything. It holds the corpus constant (the same indexed code chunks) and compares
ranking methods on plain-English questions about the codebase:

```
set MEMEMO_EMBEDDING_MODEL=minilm
set MEMEMO_STORAGE_DIR=<store where this repo is indexed>
python tests/eval_code_search.py
```

22 hand-authored intent questions, each mapped to a known target function and phrased the
way a developer would *ask* (not copied from the code, so lexical baselines can't trivially
string-match). Metric: rank of the target function in each method's top-10.

### Results (2026-06-09, this repo indexed with minilm, N=22)

| method | recall@1 | recall@5 | MRR |
|---|---|---|---|
| **mememo-hybrid** (BM25 + vector, the production mode) | **0.59** | **1.00** | **0.758** |
| mememo-vector (semantic only) | 0.32 | 0.82 | 0.535 |
| tfidf (classic lexical / keyword) | 0.36 | 0.50 | 0.436 |
| overlap (shared-token count, a grep-like floor) | 0.36 | 0.45 | 0.394 |

### Reading the numbers

- **Hybrid retrieval roughly doubles keyword search for code understanding.** It put the
  right function in the top-5 for **every** query (recall@5 = 1.00) vs 0.50 for TF-IDF;
  MRR is ~74% higher. That is the concrete payoff for the embedding machinery the cost
  benchmark charges for.
- **Pure vector is not enough — the *hybrid* is the win.** Vector-only actually trails
  keyword at rank-1 (0.32 vs 0.36): the embedding finds the right *neighborhood*
  (recall@5 = 0.82) but doesn't reliably rank it first. Fusing the BM25 lexical signal
  (RRF) sharpens rank-1 to 0.59. This validates mememo defaulting to hybrid, not pure
  vector.
- **Where keyword loses:** the paraphrased questions. "group skills that mean almost the
  same thing" → `cluster_duplicates` and "increase the counter each time a skill is used"
  → `record_use` are misses for both lexical baselines and hits for hybrid. Keyword only
  keeps up when the question happens to share tokens with the code (`count_tokens`).

### Honest limitations

- **N=22, one codebase (mememo itself), queries authored by the same person who knows the
  code** — directional, not definitive. Mitigated by a transparent per-query table and
  mechanically-scored lexical baselines, but a larger, third-party-labelled set would be
  stronger.
- Indexed with **minilm**, not qwen3 — qwen3 would likely score higher semantically but
  is impractically slow to index a whole repo on CPU (see note below).
- Gold = one designated target; a different-but-also-valid function ranking higher counts
  as a miss, which penalizes every method equally.
- This measures **retrieval** (did it surface the right code), a proxy for usefulness. It
  does **not** measure end-to-end answer quality — that would need an LLM-judge eval.
- **`server-memory` is excluded on purpose:** it has no code index (entities/relations/
  observations + keyword search), so it cannot answer "where is the code that does X" —
  scoring it here would be a strawman, not a fair test.

> Indexing note: this eval first hung for hours because the environment had
> `MEMEMO_EMBEDDING_MODEL=qwen3` — Qwen3-Embedding-0.6B embeds a whole repo far too slowly
> on CPU (large model, runs the working set into multiple GB with no flushes). Use
> **minilm** for indexing on CPU; reserve qwen3 for query-time recall or a GPU.

## mememo vs `@modelcontextprotocol/server-memory`

Reproducible cost/latency comparison against Anthropic's reference MCP memory server.
Both are driven over real stdio JSON-RPC. Run it yourself:

```
python tests/bench_vs_server_memory.py
```

### Results (2026-06-09, Windows 11, warm npx cache, tiktoken cl100k)

| Metric | mememo v0.35.0 | server-memory 2026.1.26 |
|---|---|---|
| MCP tools | 27 | 9 |
| **Tool-def tokens / turn** | **7,639** | **998** |
| Startup (spawn→tools/list) | 2.4 s | 3.9 s¹ |
| Store latency (warm, median) | 4.3 ms | 1.4 ms |
| Recall latency (warm, median) | 3.9 ms | 0.7 ms |

¹ server-memory's startup includes `npx` resolution overhead (~1–2 s even when cached);
invoked directly as `node <path>` it starts sub-second. mememo's 2.4 s is real Python
import. So in the standard `npx -y` invocation mememo actually *starts faster*, but a
node-native launch of server-memory would be quicker.

### Reading the numbers

- **Token footprint is the real cost difference.** mememo's 27 tools cost **~7.7× more
  tokens per turn** than server-memory's 9 (7.6K vs ~1K). That ~6.6K delta is paid on
  every turn while the server is connected. It's the dominant, honest cost of mememo's
  larger surface — and the lever for reducing it is trimming/gating unused tools
  (see [the token-footprint note](#footprint-context)), not micro-optimizing.
- **Per-op latency is negligible for both.** Warm store/recall are single-digit
  milliseconds on each — trivial next to network + LLM time. mememo is ~3–6× slower per
  op because it *embeds* and writes a vector index; server-memory just appends JSON. The
  one real latency cost mememo carries is the **one-time embedder load (~6 s cold)**,
  which the hook daemon amortizes to sub-100 ms across a session.

### What this does NOT measure (the honest gap)

This compares **cost and speed, not retrieval quality**. The two are not feature-equivalent:

| | mememo | server-memory |
|---|---|---|
| Retrieval | semantic (vector / hybrid BM25+embeddings) | keyword substring over a knowledge graph |
| Code awareness | AST chunking, 14 languages, CALLS/IMPORTS/EXTENDS graph | none |
| Extras | git-branch isolation, diagrams, self-learning skills | entities / relations / observations |
| Storage | SQLite + FAISS + JSON blobs | a single JSON file |

mememo spends the extra ~6.6K tokens/turn and the embedder load to *buy* semantic +
code-aware retrieval that server-memory structurally cannot do. Whether that retrieval is
actually *better* for a given task — precision/recall on a labelled query set, or
end-to-end answer quality — is **unmeasured**. That outcome eval is the next benchmark
worth building if the cost is to be justified, not just quantified.

<a name="footprint-context"></a>
### Footprint context

Of mememo's 7,639 tool-def tokens, the heaviest are `batch_store` (~680), `store_memory`
(~607), `generate_diagram` (~450). ~10 of the 27 tools are niche (graph_impact/neighbors/
path, cypher_query, recall_at_commit, detect_changes, merge_branch, sync_commits,
batch_store); dropping or config-gating them would roughly halve the footprint toward
server-memory's range while keeping the core store/recall/index/capture path.
