# Benchmarks

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
