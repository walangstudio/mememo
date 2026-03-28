"""
Performance and effectiveness benchmarks for smart context selection.

Measures:
1. Intent classification latency and accuracy
2. Adaptive builder vs legacy _build_context_block token savings
3. Skill store load time
4. Response compressor compression ratio
5. End-to-end inject path comparison
"""

import statistics
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_memory(mem_type, text, similarity, created_days_ago=0, file_path=None):
    r = MagicMock()
    r.similarity = similarity
    r.memory.content.type = mem_type
    r.memory.content.text = text
    r.memory.content.file_path = file_path
    r.memory.content.line_range = None
    r.memory.summary.one_line = text[:80]
    r.memory.summary.detailed = text[:200] if len(text) > 200 else None
    r.memory.metadata.created_at = datetime.now() - timedelta(days=created_days_ago)
    return r


SAMPLE_RESULTS = [
    _make_mock_memory("decision", "Chose SQLite over PostgreSQL for metadata storage because it's embedded, zero-config, and sufficient for the expected data volume. PostgreSQL would add deployment complexity.", 0.82, 5),
    _make_mock_memory("analysis", "Memory leak in vector index caused by shards not being evicted from LRU cache when branch switches. Fixed by clearing cache on branch change detection.", 0.75, 2),
    _make_mock_memory("code_snippet", "def search_similar(self, params):\n    query_embedding = self.embedder.embed_query(params.query)\n    vi = self._get_vector_index(context.repo.id, context.branch.name)\n    distances, memory_ids = vi.search(query_embedding=query_embedding.tolist(), top_k=top_k)", 0.68, 1, "mememo/core/memory_manager.py"),
    _make_mock_memory("context", "mememo uses FAISS with automatic sharding at 50k vectors per shard. Each shard is lazy-loaded and evicted after 5 minutes of inactivity.", 0.65, 10),
    _make_mock_memory("code_snippet", "class VectorIndex:\n    SHARD_SIZE = 50000\n    def __init__(self, base_path, repo_id, branch, dimension):\n        self.base_path = base_path\n        self.repo_id = repo_id", 0.60, 3, "mememo/core/vector_index.py"),
    _make_mock_memory("conversation", "Session: worked on passive hooks integration. Added stop hook for auto-capture and user-prompt-submit hook for context injection. Both working in Claude Code.", 0.55, 7),
    _make_mock_memory("context", "The embedder supports two models: minilm (384-dim, 90MB, default) and gemma (768-dim, 1200MB, experimental). Device auto-detection: CUDA > MPS > CPU.", 0.50, 15),
    _make_mock_memory("relationship", "memory_manager depends on storage_manager, vector_index, embedder, and git_manager", 0.45, 20),
    _make_mock_memory("code_snippet", "def _build_context_block(results, budget, min_similarity):\n    code_types = {'code_snippet', 'relationship'}\n    persistent = []\n    code = []", 0.40, 1, "mememo/cli.py"),
    _make_mock_memory("summary", "mememo v0.3.0 added passive hooks, capture tool, store_decision, recall_context, recent_context, multi-provider LLM adapter, and type-differentiated TTL.", 0.35, 30),
    _make_mock_memory("context", "Incremental indexing uses a Merkle DAG with SHA-256 file hashes stored in ~/.mememo/merkle/file_hashes.json", 0.32, 25),
    _make_mock_memory("decision", "Decided to use sentence-transformers/all-MiniLM-L6-v2 as default embedding model. Good balance of quality, speed, and size (90MB).", 0.30, 40),
]

SAMPLE_PROMPTS = {
    "coding": "implement a new MCP tool for batch memory import",
    "debugging": "the vector index search is returning stale results after branch switch, can you fix it",
    "architecture": "should we switch from FAISS to a different vector database for better scalability",
    "testing": "write integration tests for the capture hook pipeline",
    "review": "review the changes in the cleanup_memory tool for any edge cases",
    "general": "explain how the hook injection system works in mememo",
}

SAMPLE_TRANSCRIPT = """user: Can you help me fix the memory leak in the vector index?

assistant: I'll investigate the memory leak. Let me start by reading the vector index code.

<tool_use>
{"name": "Read", "input": {"file_path": "/opt/apps/tools/claude/mememo/mememo/core/vector_index.py"}}
</tool_use>

<tool_result>
class VectorIndex:
    SHARD_SIZE = 50000
    LRU_EVICTION_SECONDS = 300

    def __init__(self, base_path, repo_id, branch, dimension):
        self.base_path = base_path
        self.repo_id = repo_id
        self.branch = branch
        self.dimension = dimension
        self._shards = {}
        self._loaded_shards = {}
        self._last_access = {}

    def add(self, embeddings, memory_ids, checksums):
        active_shard = self._get_active_shard()
        for emb, mid, cs in zip(embeddings, memory_ids, checksums):
            if active_shard.ntotal >= self.SHARD_SIZE:
                self._persist_shard(active_shard)
                active_shard = self._create_new_shard()
            active_shard.add(np.array([emb], dtype=np.float32))

    def search(self, query_embedding, top_k):
        all_distances = []
        all_ids = []
        for shard_id in self._get_all_shard_ids():
            shard = self._load_shard(shard_id)
            distances, indices = shard.search(np.array([query_embedding], dtype=np.float32), top_k)
            # ... mapping logic
        return all_distances, all_ids
</tool_result>

Loading ███████░░░░░ 70%

assistant: I found the issue. The `_loaded_shards` dict keeps growing when you switch branches because there's no cache invalidation on branch change. The LRU eviction only checks time-based staleness, not branch relevance.

Here's the fix:

```python
def _get_vector_index(self, repo_id: str, branch: str) -> VectorIndex:
    if repo_id == self.vector_index.repo_id and branch == self.vector_index.branch:
        return self.vector_index
    # Clear loaded shards from previous branch to prevent memory leak
    self.vector_index._loaded_shards.clear()
    self.vector_index._last_access.clear()
    key = (repo_id, branch)
    if key not in self._vector_index_cache:
        self._vector_index_cache[key] = VectorIndex(
            base_path=self.vector_index.base_path,
            repo_id=repo_id,
            branch=branch,
            dimension=self.vector_index.dimension,
        )
    return self._vector_index_cache[key]
```

Relevant memories from previous sessions:
- [decision] Chose SQLite over PostgreSQL
- [context] mememo uses FAISS with automatic sharding

The key change is adding `self.vector_index._loaded_shards.clear()` when the branch changes. This ensures shards from the previous branch are released from memory.

user: That looks right. Can you also add a test for this?
"""


# ---------------------------------------------------------------------------
# 1. Intent Classification
# ---------------------------------------------------------------------------

def bench_intent_classification():
    print("=" * 70)
    print("1. INTENT CLASSIFICATION BENCHMARK")
    print("=" * 70)

    from mememo.embeddings.embedder import Embedder
    from mememo.context.intent_classifier import IntentClassifier

    embedder = Embedder(model_name="minilm", device="cpu")
    cache_dir = Path(tempfile.mkdtemp())
    classifier = IntentClassifier(embedder=embedder, cache_dir=cache_dir)

    # Warm up (computes centroids on first call)
    warm_emb = embedder.embed_query("warm up")
    t0 = time.perf_counter()
    classifier.classify(warm_emb)
    centroid_time = (time.perf_counter() - t0) * 1000
    print(f"\n  Centroid computation (first call): {centroid_time:.1f}ms")

    # Cached classification
    latencies = []
    results = {}
    for intent_name, prompt in SAMPLE_PROMPTS.items():
        emb = embedder.embed_query(prompt)
        times = []
        for _ in range(50):
            t0 = time.perf_counter()
            result = classifier.classify(emb, confidence_threshold=0.3)
            times.append((time.perf_counter() - t0) * 1000)
        latencies.extend(times)
        results[intent_name] = result

    print(f"  Classification latency (cached): {statistics.mean(latencies):.3f}ms avg, "
          f"{statistics.stdev(latencies):.3f}ms stdev, {max(latencies):.3f}ms max")

    # Accuracy
    print("\n  Classification accuracy:")
    correct = 0
    for expected, result in results.items():
        match = "OK" if result.intent == expected else f"MISS (got {result.intent})"
        if result.intent == expected:
            correct += 1
        print(f"    {expected:15s} -> {result.intent:15s} conf={result.confidence:.3f}  {match}")
    print(f"  Accuracy: {correct}/{len(results)} ({correct/len(results)*100:.0f}%)")

    # Reload from cache
    classifier2 = IntentClassifier(embedder=embedder, cache_dir=cache_dir)
    emb = embedder.embed_query("test cache load")
    t0 = time.perf_counter()
    classifier2.classify(emb)
    cache_time = (time.perf_counter() - t0) * 1000
    print(f"\n  Cache reload + classify: {cache_time:.3f}ms")


# ---------------------------------------------------------------------------
# 2. Adaptive Builder vs Legacy
# ---------------------------------------------------------------------------

def bench_adaptive_vs_legacy():
    print("\n" + "=" * 70)
    print("2. ADAPTIVE BUILDER vs LEGACY _build_context_block")
    print("=" * 70)

    from mememo.cli import _build_context_block
    from mememo.context.adaptive_builder import AdaptiveContextBuilder, BuilderConfig
    from mememo.utils.token_counter import count_tokens

    intents = ["debugging", "architecture", "coding", "general"]

    for intent in intents:
        # Legacy
        t0 = time.perf_counter()
        legacy_block = _build_context_block(SAMPLE_RESULTS, budget=800, min_similarity=0.25)
        legacy_time = (time.perf_counter() - t0) * 1000
        legacy_tokens = count_tokens(legacy_block) if legacy_block else 0

        # Adaptive
        cfg = BuilderConfig(base_budget=800, min_budget=200, max_budget=1200, min_similarity=0.25)
        builder = AdaptiveContextBuilder(intent=intent, intent_confidence=0.7, config=cfg)
        t0 = time.perf_counter()
        result = builder.build(SAMPLE_RESULTS)
        adaptive_time = (time.perf_counter() - t0) * 1000
        adaptive_tokens = result.tokens_used

        savings = ((legacy_tokens - adaptive_tokens) / legacy_tokens * 100) if legacy_tokens > 0 else 0
        print(f"\n  Intent: {intent}")
        print(f"    Legacy:   {legacy_tokens:4d} tokens, {legacy_time:.2f}ms")
        print(f"    Adaptive: {adaptive_tokens:4d} tokens, {adaptive_time:.2f}ms, "
              f"budget={result.effective_budget}, entries={result.entries_included}")
        print(f"    Token delta: {legacy_tokens - adaptive_tokens:+d} ({savings:+.1f}%)")


# ---------------------------------------------------------------------------
# 3. Skill Store
# ---------------------------------------------------------------------------

def bench_skill_store():
    print("\n" + "=" * 70)
    print("3. SKILL STORE PERFORMANCE")
    print("=" * 70)

    from mememo.context.skill_store import SkillStore

    tmpdir = Path(tempfile.mkdtemp())
    store = SkillStore(base_dir=tmpdir)

    # Create skills
    for i in range(20):
        intent = ["coding", "debugging", "architecture", "testing", "review"][i % 5]
        store.create_skill(f"skill-{i}", intent, f"Skill prompt content number {i} " * 10, priority=i)

    # Cold load
    store2 = SkillStore(base_dir=tmpdir)
    t0 = time.perf_counter()
    store2.list_skills()
    cold_load = (time.perf_counter() - t0) * 1000

    # Warm load (mtime check only)
    t0 = time.perf_counter()
    store2.list_skills()
    warm_load = (time.perf_counter() - t0) * 1000

    # Intent query
    times = []
    for _ in range(100):
        t0 = time.perf_counter()
        store2.get_skills_for_intent("debugging", 200)
        times.append((time.perf_counter() - t0) * 1000)

    print(f"\n  20 skills, cold load:  {cold_load:.2f}ms")
    print(f"  20 skills, warm check: {warm_load:.3f}ms")
    print(f"  Intent query (avg):    {statistics.mean(times):.3f}ms")


# ---------------------------------------------------------------------------
# 4. Response Compressor
# ---------------------------------------------------------------------------

def bench_response_compressor():
    print("\n" + "=" * 70)
    print("4. RESPONSE COMPRESSOR")
    print("=" * 70)

    from mememo.context.response_compressor import ResponseCompressor
    from mememo.utils.token_counter import count_tokens

    compressor = ResponseCompressor()

    original_tokens = count_tokens(SAMPLE_TRANSCRIPT)
    original_chars = len(SAMPLE_TRANSCRIPT)

    t0 = time.perf_counter()
    compressed = compressor.preprocess(SAMPLE_TRANSCRIPT)
    compress_time = (time.perf_counter() - t0) * 1000

    compressed_tokens = count_tokens(compressed)
    compressed_chars = len(compressed)

    char_ratio = (1 - compressed_chars / original_chars) * 100
    token_ratio = (1 - compressed_tokens / original_tokens) * 100

    print(f"\n  Original:   {original_chars:5d} chars, {original_tokens:4d} tokens")
    print(f"  Compressed: {compressed_chars:5d} chars, {compressed_tokens:4d} tokens")
    print(f"  Reduction:  {char_ratio:.1f}% chars, {token_ratio:.1f}% tokens")
    print(f"  Latency:    {compress_time:.2f}ms")

    # What was stripped
    print(f"\n  Stripped content:")
    if "<tool_use>" not in compressed:
        print(f"    [OK] Tool blocks removed")
    if "███" not in compressed:
        print(f"    [OK] Progress bars removed")
    if "Relevant memories from previous sessions:" not in compressed:
        print(f"    [OK] Mememo inject block removed")

    # Enhanced prompt
    summaries = ["[decision] Chose SQLite over PostgreSQL", "[context] FAISS sharding at 50k"]
    enhanced = ResponseCompressor.build_enhanced_prompt("Base prompt.", summaries)
    print(f"\n  Enhanced prompt adds {count_tokens(enhanced) - count_tokens('Base prompt.')} tokens for {len(summaries)} existing memories")


# ---------------------------------------------------------------------------
# 5. End-to-end token comparison
# ---------------------------------------------------------------------------

def bench_e2e_token_comparison():
    print("\n" + "=" * 70)
    print("5. END-TO-END TOKEN COMPARISON (simulated inject)")
    print("=" * 70)

    from mememo.cli import _build_context_block
    from mememo.context.adaptive_builder import AdaptiveContextBuilder, BuilderConfig
    from mememo.utils.token_counter import count_tokens

    header = "Relevant memories from previous sessions:\n"
    header_tokens = count_tokens(header)

    # Legacy path
    legacy_block = _build_context_block(SAMPLE_RESULTS, budget=800, min_similarity=0.25)
    legacy_total = header_tokens + count_tokens(legacy_block) if legacy_block else 0

    print(f"\n  Legacy (fixed 800 budget):")
    print(f"    Injected tokens: {legacy_total}")
    if legacy_block:
        print(f"    Entries: {legacy_block.count(chr(10)) + 1}")

    # Smart path per intent
    for intent in ["debugging", "architecture", "coding", "general"]:
        cfg = BuilderConfig(base_budget=800, min_budget=200, max_budget=1200, min_similarity=0.25)
        builder = AdaptiveContextBuilder(intent=intent, intent_confidence=0.7, config=cfg)
        result = builder.build(SAMPLE_RESULTS)
        smart_total = header_tokens + result.tokens_used if result.block else 0
        savings = legacy_total - smart_total
        pct = (savings / legacy_total * 100) if legacy_total > 0 else 0

        print(f"\n  Smart ({intent}):")
        print(f"    Injected tokens: {smart_total} (budget={result.effective_budget})")
        print(f"    Entries: {result.entries_included}")
        print(f"    vs legacy: {savings:+d} tokens ({pct:+.1f}%)")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("mememo Smart Context Selection - Performance Benchmark")
    print("=" * 70)
    bench_intent_classification()
    bench_adaptive_vs_legacy()
    bench_skill_store()
    bench_response_compressor()
    bench_e2e_token_comparison()
    print("\n" + "=" * 70)
    print("BENCHMARK COMPLETE")
    print("=" * 70)
