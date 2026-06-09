"""Retrieval-QUALITY eval: does mememo find the right code for a plain-English question?

Holds the corpus constant (the same indexed code chunks) and compares ranking methods:
  - mememo-vector   : pure semantic vector search
  - mememo-hybrid   : mememo's production hybrid (BM25 + vector via RRF)
  - tfidf           : classic lexical retrieval (sklearn TF-IDF cosine)
  - overlap         : naive shared-token count (a grep-like lexical floor)

Ground truth is hand-authored intent questions mapped to a known target function,
phrased the way a developer would *ask* (not copied from the code) so the lexical
baselines can't trivially string-match. Metric: recall@1, recall@5, MRR over the
rank of the target function in each method's top-10.

NOT a pytest test (needs an indexed store + model). Run manually:
    set MEMEMO_STORAGE_DIR=<store where this repo is indexed>
    python tests/eval_code_search.py

server-memory is intentionally excluded: it has no code index (entities/relations/
observations only), so it cannot answer code-location questions — comparing it here
would be a strawman, not a fair quality test.
"""

from __future__ import annotations

import asyncio
import json
import statistics
from pathlib import Path

# (question, target function name). Phrased as a dev question, not as the code reads.
GROUND_TRUTH = [
    ("group skills that mean almost the same thing so they can be merged", "cluster_duplicates"),
    ("convert a name into the lowercase hyphenated form the portable format needs", "skillmd_name"),
    ("read an exported skill markdown file back into its fields", "parse_skillmd"),
    ("choose which skill prompts to inject within a token budget", "get_skills_for_intent"),
    ("increase the counter each time a skill is used", "record_use"),
    ("list skills that were never used and are old enough to remove", "stale_unused_skills"),
    ("warn that a newly created skill looks like one that already exists", "_dedup_nudge"),
    ("delete skills whose prompt text is identical, keeping the best one", "_prune_exact_dupes"),
    ("kick off skill cleanup in the background once a day", "_maybe_background_curate"),
    ("make model downloads work behind a corporate TLS-intercepting proxy", "_ensure_system_ca"),
    ("start a child process that keeps running after the hook exits", "_spawn_detached"),
    ("estimate how many tokens a piece of text uses", "count_tokens"),
    ("build a high-level picture grouping files into subsystems", "overview_diagram"),
    ("turn a search query into a vector with the right instruction prefix", "embed_query"),
    ("decide whether a session was complex enough to save a reusable skill", "should_distill"),
    ("count how many tool calls happened in a session transcript", "count_tool_uses"),
    ("write the instruction that asks the model to merge duplicate skills", "_build_merge_prompt"),
    ("strip a skill name down to safe filename characters", "sanitize_name"),
    ("write each skill out as a portable markdown file with front matter", "skill_to_skillmd"),
    ("find the single most similar existing vector to a target", "nearest"),
    ("mirror a skill into the memory store so recall can find it", "_mirror_skill_memory"),
    ("background-index the current repo if one hasn't run recently", "_maybe_background_index"),
]

TOP_K = 10


def _load_corpus(conn, base_dir, repo_id, branch):
    rows = conn.execute(
        "SELECT id, function_name, content_ref FROM memories "
        "WHERE repo_id=? AND branch_name=? AND stale=0 AND content_type='code_snippet' "
        "AND function_name IS NOT NULL",
        (repo_id, branch),
    ).fetchall()
    ids, fns, texts = [], [], []
    for r in rows:
        try:
            blob = json.loads((base_dir / r["content_ref"]).read_text(encoding="utf-8"))
        except Exception:
            continue
        ids.append(r["id"])
        fns.append(r["function_name"])
        texts.append(blob.get("text", ""))
    return ids, fns, texts


def _rank_of(target, ranked_fns):
    """1-based rank of the first chunk whose function_name == target, else None."""
    for i, fn in enumerate(ranked_fns, 1):
        if fn == target:
            return i
    return None


def _metrics(ranks):
    hit1 = sum(1 for r in ranks if r == 1) / len(ranks)
    hit5 = sum(1 for r in ranks if r and r <= 5) / len(ranks)
    mrr = statistics.mean((1.0 / r) if r else 0.0 for r in ranks)
    return hit1, hit5, mrr


async def main():
    from mememo.server import ensure_initialized

    await ensure_initialized()
    import mememo.server as srv
    from mememo.types.memory import SearchParams

    mm = srv.memory_manager
    conn = mm.storage_manager.conn
    base_dir = Path(mm.storage_manager.base_dir)

    row = conn.execute(
        "SELECT repo_id, branch_name, COUNT(*) c FROM memories "
        "WHERE content_type='code_snippet' GROUP BY repo_id, branch_name ORDER BY c DESC LIMIT 1"
    ).fetchone()
    if not row:
        print("No indexed code found. Index this repo first: `mememo index .`")
        return
    repo_id, branch = row["repo_id"], row["branch_name"]
    ids, fns, texts = _load_corpus(conn, base_dir, repo_id, branch)
    fn_set = set(fns)
    id_to_fn = dict(zip(ids, fns))
    print(f"corpus: {len(ids)} code chunks in lane {repo_id[:10]}/{branch}\n")

    gt = [(q, t) for q, t in GROUND_TRUTH if t in fn_set]
    missing = [t for _, t in GROUND_TRUTH if t not in fn_set]
    if missing:
        print(f"skipped (target not in corpus): {missing}\n")

    # Lexical baselines over the same chunk texts.
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    vec = TfidfVectorizer().fit(texts)
    chunk_tfidf = vec.transform(texts)

    def tfidf_rank(q):
        sims = cosine_similarity(vec.transform([q]), chunk_tfidf)[0]
        order = sims.argsort()[::-1][:TOP_K]
        return [fns[i] for i in order]

    def overlap_rank(q):
        qt = set(q.lower().split())
        scored = sorted(
            range(len(texts)), key=lambda i: len(qt & set(texts[i].lower().split())), reverse=True
        )
        return [fns[i] for i in scored[:TOP_K]]

    async def mememo_rank(q, hybrid):
        res = await mm.search_similar(
            SearchParams(
                query=q,
                top_k=TOP_K,
                min_similarity=0.0,
                hybrid=hybrid,
                repo_id=repo_id,
                branch=branch,
            )
        )
        out, seen = [], set()
        for r in res:
            fn = id_to_fn.get(r.memory.id)
            if fn and fn not in seen:
                seen.add(fn)
                out.append(fn)
        return out

    methods = {"mememo-vector": [], "mememo-hybrid": [], "tfidf": [], "overlap": []}
    print(f"{'target':<26}{'vec':>5}{'hyb':>5}{'tfidf':>7}{'ovlp':>6}   (rank, '-' = miss)")
    print("-" * 72)
    for q, target in gt:
        rv = _rank_of(target, await mememo_rank(q, False))
        rh = _rank_of(target, await mememo_rank(q, True))
        rt = _rank_of(target, tfidf_rank(q))
        ro = _rank_of(target, overlap_rank(q))
        methods["mememo-vector"].append(rv)
        methods["mememo-hybrid"].append(rh)
        methods["tfidf"].append(rt)
        methods["overlap"].append(ro)

        def s(r):
            return str(r) if r else "-"

        print(f"{target:<26}{s(rv):>5}{s(rh):>5}{s(rt):>7}{s(ro):>6}")

    print("\n" + "=" * 50)
    print(f"{'method':<16}{'recall@1':>10}{'recall@5':>10}{'MRR':>8}")
    print("-" * 50)
    for name, ranks in methods.items():
        h1, h5, mrr = _metrics(ranks)
        print(f"{name:<16}{h1:>10.2f}{h5:>10.2f}{mrr:>8.3f}")
    print(f"\nN = {len(gt)} queries, top_k = {TOP_K}")


if __name__ == "__main__":
    asyncio.run(main())
