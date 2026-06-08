# Changelog

## [0.30.0] - 2026-06-08

### Added
- **`curate_skills` — consolidate the distilled-skill library (self-learning loop,
  Phase C).** Autonomous distillation (Phase A) keeps adding skills, so near-duplicates
  accumulate. The new tool clusters near-duplicate skills by embedding similarity
  (cosine over the shared embedder, union-find so chains group together) and returns a
  `passthrough_prompt` for the host model to merge each cluster into one skill, then
  apply via `manage_skill` — no API key needed. With `apply=True` it also deletes
  EXACT-duplicate skills (identical prompt), keeping the highest-priority one and reaping
  its memory mirror. Dry by default; near-duplicates are never auto-deleted. Mirrors
  Hermes v0.12+'s periodic consolidation; run it on a schedule to keep the library lean.
- **Create-time dedup nudge.** `manage_skill action=create` now appends a one-line hint
  when the new skill is ≥86% similar to an existing one (`"… similar to existing skill
  'X' — consider consolidating with curate_skills"`), so duplicates are flagged at the
  source. Non-destructive and best-effort; skipped above 500 skills to keep create cheap.

## [0.29.1] - 2026-06-07

### Fixed
- **Skill-mirror orphaned on delete for names needing sanitization.** The YAML
  store stores a skill under a sanitized name (`git ops` → `gitops`) and the mirror
  is tagged with it, but the delete path queried the raw name and never matched —
  leaving the `skill` memory behind. Delete now sanitizes via the new public
  `SkillStore.sanitize_name`.
- **`count_tool_uses` no longer reads the whole transcript into memory.** It streams
  the tail via `deque(maxlen=...)`, so a large session can't spike latency/memory on
  the synchronous distill hook.

### Changed
- Cleanup from a code-review pass over the Phase A/B self-learning code: dropped the
  redundant `enabled` arg from `should_distill` (the caller already gates), isolated
  the mirror-prune error from the create error (precise logging), added
  `GitContext.for_lane()` to document the lane-sentinel context (used by
  `delete_memory`), and a schema-drift test pinning the distillation prompt to
  `ManageSkillParams`.

## [0.29.0] - 2026-06-07

### Added
- **Skills are now a first-class, recallable memory type (self-learning loop,
  Phase B).** A distilled skill previously surfaced only via its coarse intent
  bucket (`SkillStore` injection). Now `manage_skill` also mirrors each skill into
  the memory store as a `skill`-typed memory in the GLOBAL lane, so it surfaces by
  **semantic/hybrid relevance** in the inject hook, `recall_context`, and
  session-start recall — not just when the intent classifier happens to match. New
  `skill` value across `MemoryContentType`, `PERSISTENT_MEMORY_TYPES`,
  `recall_context` `RECALL_TYPES`, `recall_workspace`, and the markdown import map;
  `skill` leads every `INTENT_TYPE_PRIORITIES` list (a relevant skill is the most
  actionable hit). The mirror is best-effort and upsert-on-create (one memory per
  skill name), kept in sync on delete via a tag lookup.

### Changed
- `MemoryManager.delete_memory` accepts an explicit `repo_id`/`branch` lane
  override (needed to delete a GLOBAL-lane memory; storage delete is lane-scoped).
- New `StorageManager.get_memory_ids_by_tag(tag)` (indexed tag lookup) backing the
  skill-mirror upsert/delete sync.

## [0.28.0] - 2026-06-07

### Added
- **Autonomous skill distillation (self-learning loop, Phase A)** — opt-in
  (`MEMEMO_HOOK_SKILL_DISTILL=true`). When a session ends after doing real work
  (`>= MEMEMO_HOOK_SKILL_DISTILL_MIN_TOOLS` tool calls, default 5), a new **sync**
  Stop hook (`mememo distill --hook`) blocks the stop and asks the same-session
  model to reflect and, if the session demonstrated a reusable technique, save ONE
  skill via the existing `manage_skill` tool. The skill lands in `SkillStore` and
  is injected into future sessions by intent — closing the capture→distill→recall
  loop (Hermes-style). Passthrough-native (the host model distills; no LLM/API
  call). It is a **separate synchronous** hook (not the async `capture` hook,
  whose stdout Claude Code discards, so `decision: block` would never fire there);
  the gate is cheap (config + transcript scan, no model load), so running it
  synchronously adds negligible latency. Guarded by `stop_hook_active` so it never
  loops; off by default because blocking the stop adds a turn. New pure core in
  `mememo/context/skill_distiller.py` (`count_tool_uses` / `should_distill` /
  `build_distillation_reason`); enable by adding `hooks/distill.sh` as a sync Stop
  hook (see `hooks/hooks.json`).

## [0.27.0] - 2026-06-07

### Added
- **`overview` diagram type** (deterministic, Phase 1): subsystem/architecture flowchart
  for non-developers. Groups files by the first N path segments into subsystems, aggregates
  cross-subsystem IMPORTS edges, emits a `flowchart TD` with edge-count labels. Available
  in chat (`generate_diagram`), web UI `/diagram` panel, and `mememo diagram overview` CLI.
- **`flow` diagram type** (LLM/passthrough, Phase 2): plain-English end-to-end flowchart
  aimed at product/business readers. Grounds the prompt with the `overview` skeleton plus
  public entry points, prepends up to 2000 chars of README for plain-language context.

## [0.26.0] - 2026-06-07

### Added
- **Qwen3-Embedding-0.6B as an opt-in higher-quality embedding model**
  (`MEMEMO_EMBEDDING_MODEL=qwen3`). 1024-dim, 32k context, Apache-2.0 (no gated
  HuggingFace download, unlike `gemma`), and top of the small-model MTEB rankings —
  the recall lever for semantic / non-exact jargon that BM25 can't catch. Default
  stays `minilm`, so nothing is forced to re-index.
- **Asymmetric query encoding.** Instruction-tuned models embed queries with a
  prompt prefix but documents bare; `Embedder.embed_query` now applies the model's
  `query` prompt (registry `query_prompt_name`) while `embed`/`embed_batch` keep
  encoding stored documents bare. A registry hint the loaded model doesn't define
  degrades to a bare encode instead of raising. No-op for symmetric `minilm`.

### Changed
- `MemoryManager.create_memory` now embeds stored content with `embed()` (document
  side) instead of `embed_query()`, matching the batch path and keeping the
  query/document sides aligned for asymmetric models. Identical output for `minilm`.

### Fixed
- Switching embedding models without re-indexing now raises an actionable
  "dimension mismatch — re-index this repo/branch" error instead of an opaque FAISS
  assertion from deep inside add()/search().

## [0.25.0] - 2026-06-06

### Added
- **`recall_context` now surfaces cross-project (GLOBAL-lane) memories too.** The
  explicit recall tool had the same blind spot the inject hook just lost — it
  searched only the ambient repo's lane, so an agent asking for relevant context
  never saw global decisions/notes. Both now go through a shared
  `MemoryManager.recall_relevant` (ambient lane + GLOBAL lane, query embedded
  once, merged by similarity), which also removes the duplicated two-lane merge
  the inject hook had inlined.

## [0.24.0] - 2026-06-06

### Added
- **Per-prompt recall now surfaces cross-project (GLOBAL-lane) memories.** The
  UserPromptSubmit inject hook searched only the ambient repo's lane, so the
  accumulated global knowledge base (decisions, project notes) never appeared
  unless you were inside the exact repo that owned it. The hook now searches the
  ambient lane **and** the GLOBAL lane and merges the results (dedup by id,
  ranked by similarity). This is the main reason per-prompt recall felt empty.
- `SearchParams.repo_id` / `branch` are now honored by `search_similar` as an
  explicit lane override (they were previously dead fields) — the mechanism the
  hook uses to reach the GLOBAL lane without per-prompt workspace discovery.
- The query is embedded once and reused across both lanes (new
  `search_similar(query_embedding=...)`), and the GLOBAL search is skipped when
  the ambient lane already is global — so the two-lane recall adds no extra
  embedding cost. Opt out with `MEMEMO_HOOK_INJECT_GLOBAL_LANE=false`.

## [0.23.0] - 2026-06-05

### Added
- **Hybrid lexical+vector recall.** The `memories_fts` FTS5 table was populated
  but unused; `search_similar` can now fuse a BM25 lexical pass with the vector
  candidate pool via Reciprocal Rank Fusion (`mememo/core/hybrid.py`), so exact
  identifiers and terse jargon the embedder blurs (project names, function
  names) rank correctly. Verified on the real store: "business idea validation"
  now ranks the right memory first instead of an unrelated note. Falls back to
  pure vector when there's no lexical match.
- Opt-in via `SearchParams.hybrid` (default **off**). Enabled on the recall
  surfaces — the per-prompt inject hook, `recall_context`, and `search_similar`
  — and deliberately **off** for callers that use `min_similarity` as a hard
  gate (capture dedup, pre-tool hook, `recall_at_commit`), since a strong
  lexical hit may bypass the similarity floor.

### Fixed
- `search_similar` now applies stale/type/tag filtering **before** truncating to
  `top_k`, so a filtered-out hit no longer steals a slot from a valid one
  further down the ranking.

### Notes
- SessionStart `recall_workspace` still uses its own vector-only path; extending
  hybrid fusion there is a follow-up.

## [0.22.0] - 2026-06-05

### Added
- **Tree-sitter walkers now capture field types**, so class diagrams render
  typed fields (`+<type> <name>`) for all 10 typed OO languages — Go, Rust,
  Java, C#, C/C++, TypeScript, Kotlin, Swift, Scala, PHP — matching Python.
  Each field stores `name: type` (was names only); JavaScript and Ruby fields
  stay untyped (the grammars expose no type). The type is reached via the
  declaration's `type` field, a `type_annotation` child (Swift), or a wrapping
  `variable_declaration` (C#/Kotlin); annotation colons are stripped while a
  type's own punctuation (e.g. C++ `::std::string`) is kept. Types over 60
  chars or with unsafe characters fall back to name-only at render time, so an
  exotic annotation can never break the Mermaid parse. Requires a re-index.

## [0.21.2] - 2026-06-05

### Added
- **Class diagrams now render field types**, not just names. A stored
  `name: type` attribute renders as `+<type> <name>` (UML order); generics are
  rewritten to Mermaid's `~ … ~` syntax (`List[str]` → `+List~str~ items`).
  Types that don't map to a safe token (unions like `Foo | None`, nested
  generics, callables) fall back to name-only so an exotic annotation can never
  break the Mermaid parse. Types are currently available for Python fields; the
  tree-sitter languages store names only (rendered as `+<name>`).

## [0.21.1] - 2026-06-05

### Fixed
- **Go intra-struct method calls now resolve.** A call on a method's own
  receiver (`a.validate()`) was emitted with the receiver var verbatim
  (`a.validate`), which matched no symbol, so every call between methods of the
  same struct was dropped from the graph (Go's receiver is a named var, not
  `self`, so the resolver's self-receiver rebind couldn't catch it). The Go
  walker now rewrites a receiver call to the struct's fully-qualified
  `module.Struct.method`, which the resolver binds exactly. Calls on other
  variables and bare functions are unaffected. Improves Go call graphs and
  `graph_neighbors`/`graph_impact`. Requires a re-index.

## [0.21.0] - 2026-06-05

### Added
- **Class fields and diagrams now cover Go and Ruby — completing every chunked
  language.** Go `struct` types are now emitted as class chunks (with their
  named fields; anonymous embedded fields are skipped), and Go methods carry
  `class_name = receiver struct` so the class diagram attaches them and the
  method's `module.Struct.method` qualname matches its edge source (intra-method
  calls now resolve, mirroring the other OO walkers). Ruby classes/modules
  extract their `@instance_variables` (assigned in `initialize` or any method)
  as fields, stripping the `@` and excluding nested class/module bodies. This
  closes the field-extraction arc started in v0.20.0. Requires a re-index.

## [0.20.2] - 2026-06-04

### Added
- **Class fields now also cover Kotlin, Swift, Scala, and PHP** — completing the
  declared-field tree-sitter languages (Java/C#/C++/TS/JS/Rust shipped in
  v0.20.1). Handles Kotlin `val`/`var` properties, Swift `let`/`var` (and
  computed/observed-property accessors), Scala `val`/`var` definitions, and PHP
  `$`-prefixed properties (multiple per declaration). The field walk skips
  property-accessor and method bodies so locals (Scala/Swift/Kotlin reuse the
  field node for locals) and accessor locals aren't mistaken for fields. Go
  (structs aren't chunked as classes) and Ruby (`@x` in `initialize`, no
  declarations) remain a follow-up. Requires a re-index.

## [0.20.1] - 2026-06-04

### Added
- **Class fields in class diagrams now cover the tree-sitter OO languages too.**
  v0.20.0 extracted class fields for Python; the tree-sitter walkers now extract
  them for **Java, C#, C/C++, TypeScript, JavaScript, and Rust** (struct fields)
  into `Chunk.attributes`. Handles multi-declarator fields (`int a, b;` → both),
  C/C++ pointer/array/reference members (`int* p` → `p`, not `* p`), and skips
  nested-type and Rust enum-variant fields. Go (structs aren't chunked as
  classes) and Kotlin/Swift/Scala/PHP/Ruby are unchanged — a clean follow-up.
  Requires a re-index for fields to appear.

## [0.20.0] - 2026-06-04

### Added
- **Class diagrams show fields, not just methods.** The Python chunker now
  extracts a class's attributes — typed class vars / dataclass / Pydantic fields
  and `self.x` instance attributes set in `__init__` — into the class chunk
  (stored in the content blob; untyped class-level constants and nested-closure
  `self` assignments are excluded as noise). `class_diagram` renders them as
  `+field` rows above the methods when given the storage `base_dir`, and the
  same skeleton now feeds the ERD generator so it builds from real fields
  instead of guessing them from source text. Requires a re-index of Python repos
  for the fields to appear; existing indexes render methods-only as before.

## [0.19.0] - 2026-06-04

### Fixed
- **Default indexing now covers every supported language.** `index_repository`
  and `sync_commits` defaulted to `["**/*.py", "**/*.ts", "**/*.js", "**/*.go",
  "**/*.rs"]`, so a default index of a Java, C#, C/C++, Kotlin, Ruby, PHP, Swift,
  or Scala repo matched **zero files** — all the per-language edge-walker and
  call-resolution work was unreachable without manually passing `file_patterns`.
  The default now derives from `language_detector.get_index_globs()` (Python +
  every edge-walker language; markdown and chunker-less `.svelte`/`.vue` excluded)
  so it can't drift behind a newly-added language. A regression test ties the
  default globs to the walker registry.

### Changed
- **Indexing parses each file once.** The main chunk loop and the edge pass each
  walked every file (the loop built chunks and discarded edges; the edge pass
  re-walked to recover them). `ChunkerFactory.chunk_file_with_edges` now returns
  chunks and the typed edges from a single AST/tree-sitter walk, and the edge
  pass only resolves + persists — halving the parse/edge-walk work per file on
  the indexing hot path.

## [0.18.2] - 2026-06-04

### Docs
- Code-review fix of v0.18.1: the Memory Graph feature bullet said typed edges
  span "all 13 supported languages", which undercounts and drops Python — the
  edge graph covers Python (AST) plus all 13 tree-sitter languages (14),
  matching the count used elsewhere in the README.

## [0.18.1] - 2026-06-04

### Docs
- README now documents the five languages added in v0.10.0 (Kotlin, Ruby, PHP,
  Swift, Scala) in the Supported Languages table, and the `generate_diagram`
  tool + `/diagram` web panel (class/call/module deterministic; sequence/usecase/
  state/erd LLM-synthesized) — both were shipped but undocumented. Corrected the
  stale "25 MCP tools" / "15+ languages" counts to the actual 26 tools + 6
  resources and 14 languages.

## [0.18.0] - 2026-06-04

### Fixed
- **Tree-sitter intra-method calls now resolve (all 13 non-Python languages).**
  Method chunks set `parent_class` but not `class_name`, so the symbol built for
  a method was `module.method` while the call edge's source was
  `module.Class.method` — they never matched, so *every* call made inside a
  method (Java/C#/C++/TS/JS/Kotlin/Ruby/PHP/Swift/Scala/Rust) was dropped, not
  just `this.`-qualified ones. Method chunks now carry `class_name = <owning
  class>` (the same fix v0.16.0 applied to Python), so the qualname aligns and
  these calls resolve. Class diagrams now attach methods for these languages
  too. Go is intentionally unchanged (its scope omits the receiver type, so
  `class_name=None` is what keeps Go calls resolving).

### Added
- **`self`/`this`/`cls`/`$this`/`Self` intra-class calls resolve to the sibling
  method, in every language.** The resolver now rebinds a single-hop
  receiver-qualified call (`this.helper`, `self.helper`, `$this->helper`,
  `Self::new`) to the source's own class when the raw label doesn't otherwise
  resolve — generalizing the Python-only rewrite from v0.16.0 to the
  tree-sitter walkers. Resolved edges never change (fallback only fires on an
  unresolved label).

**Requires a re-index** of already-indexed non-Python repos
(`mememo index <path> --full`) for the new call edges and class-diagram method
membership to appear.

## [0.17.1] - 2026-06-04

### Fixed (code review of v0.17.0)
- **CA-injection one-shot flag set before the injection ran.** Under a
  concurrent first-run model load, a second thread could see the flag set, skip
  injection, and download with the stock CA bundle before `inject_into_ssl` had
  taken effect. The flag is now set *after* the attempt (injection is
  idempotent, so a racing load re-injects harmlessly).
- **`requirements.txt` had drifted from `pyproject.toml`** — it was missing
  `truststore`, `requests`, `pyyaml`, and the Kotlin/Ruby/PHP/Swift/Scala
  tree-sitter grammars, so `pip install -r requirements.txt` produced a degraded
  install (no corporate-TLS fix, text-only chunking for 5 languages). Re-synced.

### Docs
- Documented `MEMEMO_USE_SYSTEM_CA` and the corporate-TLS first-run behavior, and
  added an upgrade note that v0.16.0's method→class linkage needs a `--full`
  re-index of existing indexes.

## [0.17.0] - 2026-06-04

### Added
- **Works behind a TLS-intercepting corporate proxy out of the box.** The
  first-run HuggingFace model download used the stock certifi CA bundle, which
  doesn't contain an enterprise proxy's root CA, so the embedder failed with
  `CERTIFICATE_VERIFY_FAILED` and no index could be built. The embedder now
  routes SSL through the OS trust store via `truststore` (which holds that CA —
  it's why browsers work) right before the download, the Python analog of
  `--use-system-ca`. Best-effort and idempotent; opt out with
  `MEMEMO_USE_SYSTEM_CA=0` to keep the stock bundle. Verified end-to-end: a
  fresh `all-MiniLM-L6-v2` download + encode that previously failed now loads.

## [0.16.0] - 2026-06-03

### Fixed
- **Python method chunks now carry their owning class.** The production chunk
  path (`factory.chunk_file` → `PythonASTChunker.chunk`) used a flat
  `ast.walk` that hardcoded `parent_class=None` and never set `class_name` on
  methods, so every indexed method memory had zero class linkage. `chunk()` now
  delegates to the scope-aware `chunk_with_edges` walk and methods record
  `class_name = <owning class>`. Effects: class diagrams attach methods to their
  class (the bodies were empty before), call-graph labels read `Class.fn`
  instead of bare `fn`, and the index qualname becomes `module.Class.method`.
  **Requires a re-index** for already-indexed repos (`mememo index <path> --full`).
  A method's owning class is the *direct* enclosing scope, so a helper closure
  nested inside a method stays a plain function and never pollutes the class
  diagram's member list. (Removed the dead `_emit_edges` second-pass walker,
  superseded by the unified `chunk_with_edges` traversal.)

### Added
- **`self.x()` / `cls.x()` calls resolve to real `CALLS` edges.** Intra-class
  method calls used to emit a dangling `self.x` target the resolver could never
  match; they now bind to the enclosing class member (`module.Class.x`), so the
  resolver links them to the method's memory. Richer call graphs and
  `graph_neighbors` / `graph_impact` over object-oriented Python.

## [0.15.1] - 2026-06-03

### Fixed (code review of v0.15.0)
- **Auto-index lock not released on a crashed child.** The detached
  `mememo index` child can die silently (e.g. the embedder fails to load); its
  lock then suppressed auto-indexing for the whole TTL (15 min). The child now
  owns its lock (`--autoindex-lock`) and releases it on any non-zero exit, so the
  next session retries instead of waiting it out.
- **Stale-lock reclaim race.** Two sessions opening at once could both unlink an
  expired lock and both spawn. Acquisition is now a bounded `O_EXCL`-create loop:
  only one create wins; the loser re-checks, sees the fresh lock, and skips.
- **`mememo index --watch` swallowed failed rounds.** A round that returns
  `success=False` (errors are caught inside `index_repository`) now logs a
  warning instead of looking identical to success.
- **CLI/server construction drift.** `mememo index` and `initialize_mememo` now
  share one `build_memory_manager(config)` factory (`mememo/core/bootstrap.py`),
  so the vector-index path, secret-scan/auto-sanitize flags, and repo-id fallback
  (now honoring `MEMEMO_REPO_ID` in both) can't diverge.

## [0.15.0] - 2026-06-02

### Added
- **`mememo index <path>` CLI** — the explicit first-index without going through
  a chat tool (there was no CLI verb before). `--full` forces a non-incremental
  re-index, `--watch [--interval N]` keeps a repo fresh on a poll loop, `--quiet`
  for hooks. Builds the memory manager directly (no LLM adapter / background
  migration thread) and writes to the same store/vector-index the server reads.
- **Opt-in auto-index on session start.** `MEMEMO_AUTO_INDEX_ON_SESSION_START=true`
  (or `hook.auto_index_on_session_start`) makes the SessionStart hook spawn a
  detached `mememo index <repo>` for the current repo — full first index, then
  incremental — so a project stays indexed with no explicit trigger. Non-blocking
  (never delays session open) and guarded by a per-repo lock with a TTL
  (`auto_index_min_interval_minutes`, default 15) so concurrent sessions don't
  pile up indexes.



### Fixed
- **Empty diagram crashed the renderer with a Mermaid parse error**
  (`Expecting ..., got 'EOF'`). A diagram with no data emits a header plus a
  `%% no data` comment, which Mermaid can't parse. New `is_empty_diagram()` is
  now checked everywhere a diagram is rendered: the web `/diagram` route returns
  an `empty` flag and the panel shows a "no data for this scope" message; the MCP
  `generate_diagram` tool returns `success=False` with a clear message for empty
  deterministic diagrams; and the HTML opener renders a "no data" note instead of
  an unparseable `<pre class="mermaid">`.

## [0.14.2] - 2026-06-02

### Added
- **Flow diagrams in the web UI.** The Diagrams panel's `sequence`/`usecase`/
  `state`/`erd` options are no longer disabled. The `/diagram` route now
  delegates the LLM types to the shared `generate_diagram` (grounded in the
  deterministic subgraph + source): if an LLM provider is configured it renders
  the Mermaid server-side; otherwise it returns the grounded prompt and the UI
  shows it with a **copy-prompt** button to paste into a chat model. All diagram
  routes now return a uniform shape (`success`/`passthrough`/`passthrough_prompt`).

## [0.14.1] - 2026-06-02

### Added
- **Open diagrams without installing anything.** A `.mmd` file isn't viewable by
  a non-developer; now diagrams render as a self-contained, double-clickable
  `.html` (mermaid.js from the same pinned CDN+SRI as the web UI; offline message
  with the source if the CDN is blocked).
  - `mememo/diagram_html.py` — `render_html(diagrams, title)` / `write_html(...)`.
    Accepts one diagram or several (tabbed gallery). Source is HTML-escaped so a
    label can't break the page.
  - `python -m mememo diagram <class|call|module> [--scope] [--repo] [--out]` —
    generate a deterministic diagram from the index and open it in the browser.
  - `python -m mememo render <file.mmd|-> [--out] [--no-open]` — convert any
    Mermaid file (or stdin) into an openable `.html`.

## [0.14.0] - 2026-06-01

### Added
- **Diagram Phase 2 — LLM/passthrough-synthesized Mermaid** in `generate_diagram`:
  `sequence`, `usecase`, `state`, `erd`. Each assembles grounding from the
  deterministic subgraph (Phase 1 class/call/module output) plus the scope's
  indexed source, then either returns `passthrough=True` + `passthrough_prompt`
  for the host model to synthesize the Mermaid in chat (default, no API key), or
  completes directly when an LLM provider is configured (falling back to
  passthrough if the call fails). Mirrors the `capture` passthrough contract.
  - `sequence` (scope = a function/method) also pulls the entry point's sibling
    methods so `self.method()` dispatch that isn't a resolved CALLS edge is still
    traceable.
  - Web UI stays deterministic-only (a passive page can't call the host model);
    the LLM types live in the chat MCP tool.

### Notes
- Phase-1 logic refactored into `_phase1` (behavior unchanged). LLM types with no
  indexed data return a clear "index the repo first" error instead of prompting
  the model with an empty graph.

## [0.13.7] - 2026-06-01

### Fixed
- **sync_commits had the same secret-scan crash v0.13.6 fixed in index_repository.**
  The v0.13.6 fix was special-cased to one indexing path; the commit-sync
  re-index (`sync_commits`) still called `create_memory` without
  `skip_secret_scan`. A changed file containing a secret-like fixture raised,
  was swallowed as a warning, and — because its old memories were already staled
  and `set_last_indexed_commit` then advanced HEAD — was dropped and never
  retried. `sync_commits` now passes `skip_secret_scan=True` too.
- **Merkle read-error sentinel could permanently skip a file.**
  `compute_file_hash` returns `""` on a read error; `get_changed_files` stored
  and committed that empty string as the file's hash, so once the file became
  readable again it matched the sentinel and was treated as unchanged forever.
  An empty hash is now reported as changed but never recorded.

### Removed
- Dead double-`clear()` in `index_repository._flush` left over from the
  v0.13.6 snapshot-and-clear refactor (the buffers are already empty there).

## [0.13.6] - 2026-06-01

### Fixed
- **Secret scanner crashed the whole repository index.** `index_repository`
  batches chunks and `create_memories_batch` ran secret detection on each; one
  secret-like chunk (a Rust/JS test fixture, an example credential) raised
  `ValueError` and aborted the entire index — per-file flushes were swallowed but
  the final flush propagated, so `index_repository` returned "Error indexing
  repository: Secrets detected" even after rows had committed. Source code is the
  same trust level as the store it's indexed into and legitimately contains
  secret-like patterns, so code indexing now passes `skip_secret_scan=True`
  (threaded through `create_memories_batch`); a fixture can never crash the index.
- **Interrupted index left stale Merkle state → later incremental runs skipped
  everything.** `MerkleDAG.get_changed_files` persisted file hashes at
  detection time, before any memory was written. A crash/interrupt (or the secret
  crash above) then left files marked indexed with zero memories committed, so
  every later `incremental=True` run reported "0 changed" and indexed nothing.
  Detection now stages hashes (`persist=False`) and `index_repository` calls
  `merkle.commit()` only after the index fully succeeds — an interrupted run
  leaves change-detection state untouched and re-detects on the next run.

## [0.13.5] - 2026-06-01

### Fixed
- **SQLite write contention across concurrent mememo servers.** stdio MCP spawns
  one mememo server per Claude session, and with hooks several processes write to
  the one `~/.mememo` DB. The connection enabled WAL but set no busy timeout, so
  a contended writer (e.g. an indexing batch) failed at Python's 5s default —
  symptom seen as `index_repository` "taking forever" with nothing committed and
  a bloated WAL. Now: `connect(timeout=30)` + `PRAGMA busy_timeout=30000` (wait
  out the lock), `PRAGMA synchronous=NORMAL` (safe WAL setting, shorter write-lock
  hold), `PRAGMA wal_autocheckpoint=1000` (bound WAL growth). Pairs with the
  v0.13.4 fix that stopped the background migration thread from sharing the
  connection. (All sessions must reload — `uv pip install -e .` + restart — to
  pick up the new pragmas.)

## [0.13.4] - 2026-06-01

### Fixed
- **Identity-migration safety (code review).** (1) `_backfill_reindex_identity`
  selected `DISTINCT repo_id, repo_path, remote_url`, which emits two rows for a
  repo that has both pre-migration (NULL `remote_url`) and post-save rows — so
  `reassign_repo_id` ran twice for the same id and the second pass could re-key
  rows already moved. Now `GROUP BY repo_id, repo_path` (one row per repo,
  `MAX(remote_url)`). (2) The background migration thread shared the live
  server's sqlite connection with the MCP handler threads; it now opens its own
  `StorageManager`/connection (WAL serialises the writes), so concurrent cursor
  use can't corrupt state. (3) `recall_workspace` token-budget trimming used
  `continue`, admitting smaller lower-ranked memories after skipping a larger
  higher-ranked one; now `break` to preserve similarity ranking.

## [0.13.3] - 2026-06-01

### Fixed
- **Edge walkers + IMPORTS resolution (code review).** Five AST-verified graph
  bugs where emitted edges were silently dropped at resolve time:
  - **Kotlin** — `_flatten_navigation` used named fields that don't exist on
    `navigation_expression` (the grammar is positional), so *every* `obj.method()`
    CALLS edge was lost. Now walks named children positionally.
  - **Ruby** — mixin `IMPLEMENTS` source qualname was doubled (`module.Dog.Dog`)
    so it never matched the class chunk → dropped. Now uses the scope qualname.
  - **PHP** — grouped `use App\Http\{A, B}` lost the shared namespace prefix.
    Now prepends the `namespace_name` prefix to each clause.
  - **Scala** — expression-body `def f() = expr` visited the call's sub-nodes
    instead of the call itself → CALLS dropped. Now dispatches on body type.
  - **IMPORTS edges were dropped for every language** — the source qualname is
    the bare module, which was never registered as a symbol, so the resolver
    discarded all of them (IMPORTS count was 0). `index_repository._flush` now
    registers a module-level `SymbolEntry` per file, so IMPORTS resolve and
    `module_dependency` diagrams render (verified: 0 → 140 on `mememo/core`).
  +5 regression tests.

## [0.13.2] - 2026-06-01

### Fixed
- **Diagram bugs from a code review.** (1) Call-graph scope detection treated any
  string containing `-` as a UUID, so a kebab-case function name (`get-user`) was
  never looked up → "not found"; now only a real UUID is treated as an id, with a
  literal-id fallback. (2) `call_graph` now renders unresolved/external calls
  (target_memory_id NULL) as leaf nodes labeled by `target_symbol` instead of
  dropping them — and the "%% no data" sentinel only fires when there are truly
  no CALLS. (3) `max_nodes` now halts the BFS instead of only the current batch
  (it could overshoot by a full frontier). (4) Mermaid labels (file/class/function
  names) are escaped so a `"` in a name can't break or inject the diagram; class
  method names are sanitized for the `{ }` block.

## [0.13.1] - 2026-05-31

### Fixed
- **`generate_diagram` returned empty diagrams.** The MCP tool called a
  nonexistent `git_manager.get_context()` and read `ctx.repo_id`/`ctx.branch`;
  the `AttributeError` was swallowed, leaving `repo_id=""` so every diagram came
  back `%% no data`. Now uses `detect_context()` → `ctx.repo.id`/`ctx.branch.name`
  (and logs on failure instead of silently blanking). The web `/diagram` route
  likewise returned "no data" when `repo_id` was omitted; it now defaults to the
  store's busiest `(repo, branch)` so the single-repo web UI works without
  passing one. Regression tests cover the detection + default-repo paths (the
  earlier tests passed `repo_id` explicitly, hiding the bug). NOTE: `module`
  diagrams are still empty for Python repos because IMPORTS edges are dropped at
  resolve time (the chunker emits them but the module-level source qualname isn't
  a registered symbol) — a separate pre-existing indexing bug, tracked for a
  follow-up.

## [0.13.0] - 2026-05-31

### Added
- **Codebase diagrams (Mermaid) — Phase 1, deterministic.** Generate diagrams
  straight from the indexed code graph (no LLM): **class diagram** (classes +
  methods + EXTENDS/IMPLEMENTS), **call graph** (CALLS subgraph from a function),
  **module dependency** (cross-file IMPORTS). New `mememo/diagrams.py`
  (`class_diagram`/`call_graph`/`module_dependency`), a `generate_diagram` MCP
  tool, a web `GET /diagram` (+ `/scopes`) route, and a Diagrams panel in the web
  UI that renders Mermaid (mermaid.js via CDN). Verified on real indexed code
  (`BaseChunker <|-- MarkdownChunker` etc). LLM-synthesized diagrams (ERD,
  sequence, state, use-case) are Phase 2 — they'll run through the same
  `generate_diagram` tool in chat (passthrough), shown disabled in the web UI.

## [0.12.0] - 2026-05-31

### Changed
- **`import-md` now creates one memory per markdown heading-section** instead of
  one big memory per file. Whole-file embeddings averaged a multi-topic note into
  a blurry vector that no specific query matched well (recall scored only
  0.15–0.32); per-section memories let a query match the exact section. Reuses
  `MarkdownChunker` (every heading is its own section; subsections excluded).
  `file_path` stays clean (heading goes in `function_name`, `line_range` carries
  the section span); idempotency is per-section via the existing
  `(file_path, checksum)` skip; URL/wikilink REFERENCES edges attach to the
  section they appear in, not the file. Heading-only sections (no body) are
  dropped as recall noise. New `--whole-file` flag keeps the legacy
  one-memory-per-file behaviour. Existing stores should re-import to get
  section-granular memories.

## [0.11.1] - 2026-05-30

### Fixed
- **Semantic search now actually persists embeddings.** `VectorIndex.add` added
  vectors to the in-memory faiss shard and wrote `mappings.db`, but only flushed
  the shard to disk on shard-full (50k) or 5-minute idle eviction. Any
  short-lived process — `import-md`, hooks, a quickly restarted server — exited
  with the vectors in memory only: `mappings.db` kept the rows but the
  `shard_*.faiss` file was never written, so the next process loaded an empty
  index and `search_similar` / `recall_context` returned nothing. `add` now
  writes each modified shard to disk immediately, making embeddings durable
  regardless of process lifetime. Found by an end-to-end recall-effectiveness
  probe (unit tests passed because they add+search within one process). Existing
  stores must re-index/re-import to materialise the missing shards.

## [0.11.0] - 2026-05-30

### Added
- **Links as first-class memory (multimodal docs/links P2).** URLs are now
  extracted beyond markdown: from Python docstrings and from captured
  conversation, materialised as a new `reference` memory type (searchable,
  joins `PERSISTENT_MEMORY_TYPES`) plus `REFERENCES` edges, deduped by
  normalised URL.
  - New `chunking/url_extract.py`: `scan_urls(text)` + `normalize_url(url)`.
  - `python_ast_chunker` scans class/function docstrings → `REFERENCES` edges
    (source = the symbol that owns the docstring, target = the URL).
  - `capture` extracts URLs from session text → `reference` memories (deduped).
  - `reference` added to `MemoryContentType` + `PERSISTENT_MEMORY_TYPES` and to
    the persistent-recall content-type sets (`recall_context`, workspace recall).
  - `import-md` now maps frontmatter `type: reference` to the `reference` type
    (was `relationship`).
  - Markdown URL REFERENCES (v0.8.0) unchanged. Images remain deferred.

## [0.10.0] - 2026-05-30

### Added
- **Edge walkers for Kotlin, Ruby, PHP, Swift, Scala.** These languages were
  text-fallback-only and emitted zero graph edges; they now have tree-sitter
  edge walkers (`walk_kotlin`/`walk_ruby`/`walk_php`/`walk_swift`/`walk_scala`
  in `chunking/ts_edges.py`) on par with rust/java/c/cpp/csharp. Each emits the
  edges its AST supports: IMPORTS (import/require/use), EXTENDS + IMPLEMENTS
  (class/superclass/interface/protocol/trait), CALLS (function/method/scoped),
  and USES where the language has typed member access (swift/scala). Wired into
  `_GRAMMARS`, `LANGUAGE_QUERIES`, and `LANGUAGE_CATEGORIES`; added the five
  `tree-sitter-*` grammar deps. 44 new tests. Closes the per-language edge gap —
  every chunked language now participates in the graph.

## [0.9.1] - 2026-05-30

### Fixed
- **`import-md` now actually stamps the global lane** when `--repo` is omitted.
  It passed `cwd=None` to `create_memory`, so running the import from inside a
  git repo stamped that ambient repo's id instead of `GLOBAL_REPO_ID` — the
  memories then only recalled inside that repo, not workspace-wide. `create_memory`
  gained `force_global`; the importer sets it when no `--repo` is given, stamping
  `(GLOBAL_REPO_ID, "main")` to match what `recall_workspace` queries. (The
  importer's tests had stubbed git detection to return GLOBAL, which masked this.)

## [0.9.0] - 2026-05-30

### Added
- **Portable project identity** — `repo_id` is now derived from the
  normalized `owner/repo` path rather than the raw remote URL or local path.
  `git@github.com:owner/repo` and `https://github.com/owner/repo` resolve to
  the same id. SSH host aliases (e.g. `git@gh-kitty:`) are resolved to their
  canonical hostname before normalization. Override precedence: `MEMEMO_REPO_ID`
  env > `.mememo/project.yaml project_id` > normalized remote hash > path hash
  fallback. Monorepo and cross-host-collision escape hatch: set `project_id` in
  `.mememo/project.yaml`.
- **Global lane** — memories stored outside a git repo go to `repo_id =
  "__global__"`. SessionStart hook and workspace recall always include the
  global lane so process/project notes surface regardless of active repo.
- **Workspace recall** — `discover_workspace` scans immediate-child repos from
  a parent directory (capped at `MEMEMO_WORKSPACE_MAX_REPOS`, default 8).
  Cross-location deps declared in `.mememo/workspace.yaml` under `projects:`.
  `recall_workspace` embeds the query once and merges results across all
  discovered repos plus the global lane, ranked by similarity.
- **SessionStart hook** — `python -m mememo session-start --hook` fires
  asynchronously at Claude Code session open, recalls memories from the
  workspace, and injects them via `additionalContext`. Register via
  `install-git-hooks --with-session-start` or manually in
  `~/.claude/settings.json`. `register_claude_session_start_hook()` in
  `mememo.hooks.installer` handles programmatic registration (idempotent).
  CLAUDE.md injection stays harness-controlled — mememo augments, does not
  replace it.
- **`import-md` importer** — `python -m mememo import-md <dir> [--repo <path>]
  [--dry-run]` ingests `.md` files with YAML frontmatter (e.g.
  `~/.claude/projects/.../memory/*.md`). Type mapping: `decision` →
  `decision`; `project`/`user`/`feedback` → `context`; `reference` →
  `relationship`. Idempotent: skips files whose `(file_path, checksum)` pair
  already exists. `[[wikilink]]` references stored as `REFERENCES` edges.
- **`reindex-identity` CLI** — `python -m mememo reindex-identity [--dry-run]`
  recomputes every stored `repo_id` via the live resolver and moves FAISS dirs
  to match. Supports `--dry-run` to inspect changes without writing.
- **Auto-migration** — on first startup after upgrade, a daemon thread
  re-derives every stored `repo_id` via the new resolver and moves FAISS dirs
  accordingly. Guarded by `schema_meta.identity_migrated`; runs exactly once.
  Non-blocking: server is usable immediately. FAISS conflicts (two old ids
  converge) clear embedding pointers for re-embedding.
- **`import-md --allow-secrets`** — opt-in bypass of secret detection for
  trusted local markdown. Memory notes often carry placeholder credentials
  (e.g. `postgres://user:pass@host`) that the scanner otherwise rejects.

### Fixed
- **Migration transaction safety** — `reassign_repo_id` caught
  `OperationalError` but not `IntegrityError`. When two repos normalise to the
  same id (incl. one repo aliased twice, each with a `branch_state` 'main' row)
  the partial `memories` UPDATE was left dirty for the next request to commit,
  producing an inconsistent store. Now wrapped in an explicit transaction
  (rollback + re-raise); the backfill records the colliding repo as skipped and
  keeps going.
- **`normalize_remote` ssh-with-port** — `ssh://user@host:port/path` was misread
  as SCP, making the port the "owner" segment and producing a garbage id. Added
  a negative lookahead so scheme URLs skip the SCP branch.
- **Vector-dir scaffolding** — `VectorIndex.__init__` pre-creates
  `{new_id}/{branch}/` before migration runs, so `move_vector_index` always saw
  a non-empty target → forced re-embed + orphaned `{old_id}/` every upgrade. An
  empty pre-created target is now replaced; only real FAISS files are a conflict.
- **CLI log-rotation contention** — running a mememo CLI command while the server
  holds `~/.mememo/logs/server.log` spammed `WinError 32` on rollover. The file
  handler now tolerates a locked file (skips that rotation, keeps appending).

## [0.8.1] - 2026-05-28

### Fixed
- **Hooks now actually reuse the running daemon** (PR #38, merged into v0.8.0 but missed in that entry). `cmd_capture` / `cmd_inject` / `cmd_pre_tool` were calling `initialize_mememo()` directly, which re-ran full boot every fire — defeating the v0.8.0 hooks-sidecar. They now call `ensure_initialized()` (the idempotent gate). Live log evidence pre-fix showed ~10 fresh "Initializing mememo" blocks per minute with the sidecar up; post-fix it's once per server process lifetime.
- **Hookd cold-init race serialized across handler threads** (PR #40). Each hookd request spins a fresh asyncio loop in its own thread, so the existing is-None check in `ensure_initialized` let multiple threads through and ran full init in parallel. Wrapped the init with a module-level `threading.Lock` using a double-checked-locking pattern; fast path stays a cheap pointer compare, only cold-init contention pays the lock.

### Tests
- Real `cmd_capture` integration test driven through hookd's default factories (PR #40), closing the wiring gap that hid the #38 bug behind a trivial `_echo` factory in earlier tests.

## [0.8.0] - 2026-05-28

### Added
- **FTS5 content search + `content_type` filter** (PR #36). New `memories_fts` virtual table populated by `save_memory` and kept in sync via an `AFTER DELETE ON memories` trigger, with a one-shot startup backfill for existing stores. `/memories?q=` now ORs the existing file/fn/class LIKE with an FTS prefix-match subquery, so `decision`/`context`/`summary` memories (NULL file/fn/class, body lives in a blob) are finally findable. New `/memories?content_type=` filter + frontend dropdown.
- **Hook sidecar** (PR #35). The MCP server boots a stdlib loopback HTTP listener (127.0.0.1, bearer-token, discovery file at `~/.mememo/.daemon.json`). `mememo capture|inject|pre-tool --hook` first POSTs into the running daemon, falling back transparently to the in-process slow path when the daemon isn't reachable (e.g. git hooks fired outside Claude Code). New modules: `mememo/hookd.py`, `mememo/hookclient.py`. Opt-outs: `MEMEMO_NO_HOOK_DAEMON=1` (server), `MEMEMO_NO_HOOK_CLIENT=1` (CLI).
- **`REFERENCES` URL edge type** (PR #37). Markdown doc sections emit a typed edge per outbound `https?://…` (trailing sentence punctuation stripped, dedup per section, fenced URLs ignored). The resolver naturally leaves the URL as `target_symbol` with `target_memory_id` NULL. Added to both `EdgeType` (chunking) and `RelationType` (types/memory) Literals; web graph renders REFERENCES with a distinct color.

### Fixed
- **MCP cold-start regression** (PR #34). `Embedder.dimension` was triggering a SentenceTransformer load (~6s cold on Windows) just to read a number `MODEL_REGISTRY` already publishes. `VectorIndex(..., dimension=embedder.dimension)` at `initialize_mememo` forced the load even though the model isn't used until the first `embed()` call. The property now reads from the registry first; cold init drops from ~9s → ~2.5s. Verified by `~/.mememo/logs/server.log` — "Loading embedding model" no longer appears at server boot.

### Notes
- **Editable installs need a refresh**: `pip install -e .` (or `uv pip install -e .`) so `importlib.metadata.version("mememo")` agrees with this bump. The daemon discovery file embeds the reported version, so the refresh is also how the sidecar advertises the new build.

## [0.7.1] - 2026-05-27

### Fixed
- **Indexing is ~90× faster — the multi-hour hang is gone.** `count_tokens` called `tiktoken.get_encoding("cl100k_base")` on every invocation; with no cached BPE file it attempted an HTTPS download that failed after ~0.8s, and the failure was never memoized. Run per chunk (and again inside `truncate_to_tokens`' binary search for summaries), this dominated indexing time. The tokenizer load is now attempted once and the failure cached, falling back to a fast offline heuristic. Indexing mememo on itself dropped from ~2030s to ~23s for the same 138 files / 1145 chunks.
- **Repository indexing now batches chunk creation.** `index_repository` created memories one chunk at a time, so every chunk spawned a `git` subprocess (context detection), ran a single-item embed, and committed on its own. It now accumulates chunks and calls `create_memories_batch` (one git-detect + one batched embed + one vector add per flush of 256).
- **File discovery no longer descends ignored trees.** The `glob("**/*")` walk enumerated `target/`, `.git/`, `node_modules/` etc. and filtered afterward (minutes on a large Rust `target/`); it now uses `os.walk` with in-place directory pruning, plus a max-file-size guard and a binary-extension skip list. Progress is logged every 100 files.

### Changed
- **MCP server starts fast and logs to a file.** `sentence-transformers`/`torch` and `faiss` are now imported lazily instead of at module load, so `python -m mememo` reaches the stdio handshake in ~2s (was a 5–15s cold import that could exceed Claude Code's connect window and show the server as failed). Logs are also written to `~/.mememo/logs/server.log` (rotating) since stderr is invisible once spawned.

## [0.7.0] - 2026-05-24

### Added
- **Rust edge extraction** `walk_rust` emits the full edge taxonomy for Rust: IMPORTS (`use` paths), CALLS (bare/scoped/field callees), IMPLEMENTS (`impl Trait for Type`), and USES (method-to-`impl` type binding), plus function/method/struct/enum/trait chunks. Previously Rust produced chunks but zero edges.
- **Java edge extraction** `walk_java` emits IMPORTS (dotted paths, wildcard preserved), EXTENDS (`superclass`), IMPLEMENTS (`super_interfaces`), CALLS (bare/`this`-qualified/field-access callees), and USES (`this` field reads), plus class/interface/method/constructor chunks. Verified against tree-sitter-java 0.23.5. Previously Java produced chunks but zero edges.
- **C / C++ edge extraction** `walk_c_family` (shared) emits IMPORTS (`#include`, brackets/quotes stripped), CALLS (bare/`this->m`/`Ns::f` callees), and for C++ EXTENDS (`base_class_clause`, multiple bases) + USES (method-to-class binding and `this->field` reads), plus struct/class and function/method chunks. Verified against tree-sitter-c 0.24.2 / tree-sitter-cpp 0.23.4. Previously C/C++ produced chunks but zero edges.
- **C# edge extraction** `walk_csharp` emits IMPORTS (`using`, dotted; alias resolves to its target), EXTENDS (`base_list` — C# does not separate base class from interfaces in-grammar, so every base type is EXTENDS), CALLS (bare/`this.M`/member-access callees), and USES (method-to-class binding and `this.<member>` reads), plus class/interface/struct/enum/record and method/constructor chunks, namespace-aware qualnames. Verified against tree-sitter-c-sharp 0.23.5. This closes the edge-graph gap — every language mememo chunks now emits edges.
- **Markdown docs in the graph** `MarkdownChunker` (pure-Python, heading-scoped) chunks `.md` files into one chunk per heading section (parent/child hierarchy), and emits `DOCUMENTS` edges from a doc section to the code symbols it names — backtick-quoted identifiers and bare `path/to/file.ext` mentions, resolved against the indexed symbol table at INFERRED confidence. Doc qualnames are namespace-aware (`file.heading-slug`). New `heading` chunk type; new `DOCUMENTS` edge type.

### Changed
- **Edge-type values are no longer constrained at the DB layer.** The `relations.type` `CHECK` was dropped; edge types are validated by the `RelationType`/`EdgeType` literals (Pydantic) before insert. Existing databases are migrated in place (table rebuild, rows preserved). Adding a future edge type now needs no schema migration.

### Fixed
- **Edge pass now runs for every walker language.** `index_repository` hardcoded the edge-pass language list to the original five (python/typescript/tsx/javascript/go), so the Rust/Java/C/C++/C# walkers added in v0.7 never ran during real indexing. The list is now driven by `ts_edges.EDGE_WALKERS`, so a new walker is picked up automatically.
- **Markdown `DOCUMENTS` edges now actually persist.** The chunker emitted edges with a slug-path `source_qualname` while the indexer registered the heading chunk under `module.<heading-text>`, so the resolver (which drops edges with an unknown source) discarded every doc→code edge. `Chunk` gained an explicit `qualname` the indexer registers verbatim, keeping both sides in sync; an end-to-end resolve test guards it. Also fixed heading-chunk `end_line` off-by-one and stopped URLs being emitted as bare-path edge targets. The `relations.type` migration is now re-runnable (`DROP TABLE IF EXISTS relations_new`).

## [0.6.2] - 2026-05-20

### Changed
- **tree-sitter dependency migrated** `tree-sitter-languages` (unmaintained, no wheels past Python 3.12) replaced with the per-language `tree-sitter-typescript/-javascript/-go/-rust/-java/-c/-cpp/-c-sharp` wheels. Each is self-contained and fully offline (no runtime grammar download), so mememo now installs and parses natively on Python 3.10–3.14 with plain pip. Core `tree-sitter` pin moved `>=0.20,<0.22` → `>=0.25.2,<0.26`
- **tree-sitter chunker query API** updated for tree-sitter 0.25 (`Query(lang, src)` + `QueryCursor.captures()` returning `dict`). A version-compat shim keeps the legacy ≤0.21 path working too
- **Installers are uv-aware** `install.sh` / `install.bat` detect `uv` and create the venv with it (falling back to pinned CPython 3.12 when the system Python is unsupported), then `uv pip install`. Plain `python -m venv` + `pip` remain the fallback when uv is absent

## [0.6.0] - 2026-05-13

### Added
- **Worktree-canonical repo_id** (FR-024): `GitManager.canonical_repo_root` uses `git rev-parse --git-common-dir` so linked worktrees (`git worktree add ...`) collapse to the same SHA-256 `repo_id` as the primary checkout instead of fragmenting per worktree
- **`mememo migrate-worktrees`** (FR-025): one-shot CLI that re-keys legacy per-worktree `repo_id`s onto the canonical one across `memories`, `relations`, `branch_state`, and the existing index_state tables. Idempotent, supports `--dry-run`
- **Four read-only MCP resources** (FR-026/FR-027): `mememo://repo/{id}/stats`, `mememo://repo/{id}/stale`, `mememo://repo/{id}/branch/{name}/summary`, `mememo://repo/{id}/community/{cid}` — each payload bounded to ≤4 KB with a `truncated` marker
- **PreToolUse hook** (FR-028/FR-029): `python -m mememo pre-tool --hook` reads the Claude Code hook payload for Grep/Glob/Bash calls and emits up to 3 related memories totalling at most 300 tokens. Never blocks the tool call; failures log to stderr and return an empty continue response
- **FastAPI web UI** (FR-030/FR-031): `mememo serve` (optional extras) launches a localhost-only app on 127.0.0.1:5757 with read-only routes (`/repos`, `/memories?as_of_sha=<sha>`, `/relations`, `/communities`, `/snapshots/{sha}`) plus a single-page D3-force graph + paginated memories table + time-travel slider. Binding refused for any host other than localhost
- **`cypher_query` MCP tool** (FR-032): hand-rolled parser for a documented Cypher subset (`MATCH (a)-[r:TYPE]->(b)`, `WHERE` with `=` / `<>` / `=~` / `AND` / `OR`, `RETURN ident.prop [AS alias]`, `LIMIT`). Any other construct raises `UnsupportedCypherError` which the tool layer turns into `error_kind="unsupported"`
- **`mememo install-git-hooks --with-pretool`**: installer extension that registers the PreToolUse hook into `<repo>/.claude/settings.json`. Idempotent; refuses to clobber a user-customised block unless `--force` is passed
- **`mememo merge-branch` / `mememo sync-commits` CLI shims**: dispatch arms that the opt-in post-merge / post-commit hooks shell out to. Previously the hook scripts called these subcommands but the dispatch was missing — fixed in the simplify pass
- **Optional install extras**: `pip install 'mememo[web]'` for the FastAPI UI, `pip install 'mememo[graph]'` for `cluster_relations` (networkx) + `dedup_entities` (rapidfuzz). The MCP server, hooks, and every other CLI subcommand work without either. Installer flags `--with-web` and `--with-graph` available

### Changed
- Console script entry-point repointed from `mememo.server:main` (non-existent) to `mememo.__main__:main`
- `__main__.py` rewritten as a `{cmd: handler}` dispatch table
- `translate_to_sql` returns `(sql, params, projection_keys)` so callers no longer reach into private parser internals
- Snapshot filter pushed server-side: `/memories?as_of_sha=<sha>` filters before paginating, replacing a buggy client-side filter that corrupted `total` and pagination math

### Performance
- `graph_neighbors` and `graph_impact` BFS rewritten as batched per-level `IN (...)` queries — one SQL per level instead of one per visited node (N+1 elimination)
- `merge_branch` switched to `executemany` + a single transaction commit; per-row `append_event` + per-row commit replaced. `content_ref` secrets-scan results cached within one merge call
- `detect_changes` chunks its `WHERE file_path IN (...)` to 500 ids per batch — defends against `SQLITE_MAX_VARIABLE_NUMBER` failures on large diffs
- `index_repository._run_edge_pass` consumes `(rel_path, content, lang)` triples + a pre-built `SymbolEntry` list threaded from the main indexing loop instead of re-walking the tree with 6 separate `rglob` patterns + re-reading every file + re-querying SQLite for the symbol table. Also no longer descends into `.venv` / `node_modules`

### Reuse
- New `coerce_sha(value) -> str` helper in `mememo.types` replaces 9 hand-rolled `commit_hash or NULL_SHA` / length-check sites across `memory_manager`, `storage_manager`, `index_repository`, `merge_branch`
- `SHA_PATTERN` and `SHA_PREFIX_PATTERN` exported from `mememo.types`; duplicate regexes dropped from `recall_at_commit.py` and `web/app.py`

### Security
- `mememo.web.app.run()` refuses to bind to anything other than 127.0.0.1 / localhost — defence in depth on top of the FastAPI route guards
- `/snapshots/{sha}` and `/memories?as_of_sha=` reject anything that isn't 4-40 hex chars, matching the v0.4 option-injection guards on `recall_at_commit`

## [0.5.0] - 2026-05-13

### Added
- **Typed-edge memory graph** (FR-013, FR-014, FR-017): every indexed Python / TypeScript / JavaScript / Go file emits `IMPORTS`, `CALLS`, `EXTENDS`, `IMPLEMENTS`, `USES`, `DECORATED_BY` edges into a new `relations` table. Edge confidence (`EXTRACTED` / `INFERRED` / `AMBIGUOUS`) recorded per row. CHECK constraints reject unknown types + non-40-char SHAs
- **`mememo/chunking/python_ast_chunker.py`** gains `chunk_with_edges()` — a single scope-aware walk that emits chunks with proper `parent_class` AND raw edges in one pass. Legacy `chunk()` unchanged
- **`mememo/chunking/ts_edges.py`** (FR-014): tree-sitter walkers for TypeScript / JavaScript / Go that share the same scope-stack pattern as the Python walker. Handles JS-vs-TS class_heritage grammar differences and Go method receivers
- **`mememo/core/symbol_resolver.py`** (FR-015, FR-016): turns `(rel_path, raw_edges)` into resolved `Relation` rows. Exact qualname match → `EXTRACTED`; suffix match via a precomputed tail index → `EXTRACTED`; single Jaro-Winkler match ≥ 0.95 → `INFERRED`; otherwise `AMBIGUOUS` with the raw `target_symbol` preserved. Fuzzy match auto-disables when the symbol set exceeds `fuzzy_max_symbols=2000` so the resolver stays O(E) on large corpora
- **`mememo/core/graph_analysis.py`** (FR-018, FR-019): `cluster_relations()` runs deterministic-with-seed Louvain (networkx) over the relations graph and stamps `community` per edge. `dedup_entities()` chains Jaro-Winkler (rapidfuzz) and a path-compressed union-find; writes `(canonical_memory_id, alias_label, similarity)` to a new `entity_aliases` table. Both raise a clear `ImportError` when their optional dep is missing
- **`graph_neighbors`** MCP tool (FR-020): depth-limited BFS over typed edges with direction (`out` / `in` / `both`) and edge-type filter
- **`graph_path`** MCP tool (FR-021): shortest directed edge path between two memories or `null` if unreachable within `max_depth`
- **`graph_impact`** MCP tool (FR-022): blast-radius BFS with `min_confidence` floor; each reached memory decorated with its current `risk_grade` (from v0.4 sync_commits) + file/class/function metadata. `direction='upstream'` inverts the walk to find callers / dependents
- **`search_similar` `cluster_id` filter** (FR-023): restrict semantic-search results to memories whose relations live in the named community
- **Resolver perf gate** (FR-016): `benchmarks/resolver_perf.py` + a pytest budget gate that asserts ≤1.0s per 10k chunks
- **Index-time perf gate** (FR-035): `benchmarks/index_corpus_perf.py` asserts that edge extraction adds at most 30% over chunk-only baseline

### Changed
- `index_repository` now runs a best-effort edge post-pass after chunking that emits + resolves + persists relations. Fails open: chunk-only indexing still produces a usable store when tree-sitter or other optional deps are missing
- `Chunk.parent_class` is now populated correctly for Python methods via the unified scope-aware walk (legacy walk left `parent_class=None` per a pre-v0.5 TODO)

### Performance
- Resolver tail-index precomputation: suffix lookup is O(1) per edge instead of O(N). Resolved 5k symbols in <0.5s where the original implementation took >6s

## [0.4.0] - 2026-05-13

### Added
- **Commit-aware memory layer**: every memory now carries the git SHA it was created at (`created_at_sha`) and last updated at (`updated_at_sha`); risk-graded staleness (`WILL_BREAK` / `LIKELY_AFFECTED` / `MAY_NEED_TESTING`) lives on `memories.risk_grade`
- **`memory_events` table**: append-only event log (CREATED / UPDATED / STALED / DELETED / RESTORED) with a `CHECK(length(commit_sha)=40)` guard and a UNIQUE index against duplicate CREATED events under concurrent startup
- **`branch_state` table**: per-(repo, branch) last_indexed_sha + parent_sha (merge-base with default branch); upserted by `index_repository` on every run
- **`detect_changes` MCP tool**: read-only diff → affected memories with risk grades; backs the post-commit hook
- **`recall_at_commit` MCP tool**: time-travel semantic search; resolves a target SHA to its commit timestamp, replays events, filters FAISS search to the alive set
- **`merge_branch` MCP tool**: unions alive source-branch memories into target, dedup by `content_sha`, emits RESTORED events tagged at the merge SHA
- **GitManager extensions**: `merge_base`, `is_merge_commit`, `diff_between`; whitelist extended with `merge-base` and `cat-file`
- **Risk grader** (`mememo/core/risk_grader.py`): pure function turning a `--name-status` diff + memory line range into a risk grade; FR-009 SHOULD-clause hunk-overlap downgrade implemented behind an optional `hunk_ranges` arg
- **Opt-in git hooks**: `mememo install-git-hooks --repo-path <repo>` copies `post-merge` (triggers `mememo merge-branch`) and `post-commit` (triggers `mememo sync-commits`) into `.git/hooks/`; refuses to clobber existing hooks unless `--force` is passed (FR-033)
- **`NULL_SHA` / `BACKFILL_SHA` sentinels**: 40-char hex sentinels for "no git context" and "legacy pre-v0.4 row"; replace the empty-string SHA flaw flagged by the v0.4 security audit

### Changed
- `mark_memories_stale_for_file(file, repo, branch, reason, commit_sha=None)` now emits a STALED event per affected memory; the new `commit_sha` parameter is the SHA that caused the staling (defaults to `NULL_SHA`)
- `index_repository` upserts `BranchState` alongside the legacy `repo_index_metadata.last_indexed_commit` so commit-aware tools can read the canonical per-branch SHA
- `server.py` tolerates a missing pip-install (`PackageNotFoundError`) and falls back to `"0.0.0+local"` so the module loads from a source checkout during dev/CI

### Migration
- v0.3 → v0.4 backfill runs idempotently on every startup: rows with a valid 40-char `commit_hash` get their `created_at_sha` / `updated_at_sha` backfilled and receive one synthetic CREATED event; legacy rows without a valid SHA get `BACKFILL_SHA` so the replay path can distinguish them from live non-repo writes
- A down-migration helper `StorageManager.downgrade_v04_to_v03()` is available for emergency rollback — it drops the v0.4 tables and columns; SQLite `ALTER TABLE DROP COLUMN` requires SQLite 3.35+

### Security
- `memory_events.commit_sha` has a DB-level CHECK constraint requiring length 40; combined with the Pydantic field validator on `MemoryEvent.commit_sha`, empty-string SHA writes are rejected at both layers
- Backfill uses `INSERT OR IGNORE` + a UNIQUE index on `(memory_id, commit_sha) WHERE op='CREATED'` so concurrent startups cannot duplicate synthetic events

## [0.3.0] - 2026-03-21

### Added
- **Passive hooks**: `UserPromptSubmit` hook injects relevant memories as a system message before each prompt; `Stop` hook asynchronously captures memorable facts from the conversation transcript after each response — both fully automatic, no user action required
- **`capture` tool**: LLM-based extraction of decisions, analysis, and context from raw text; falls back to a self-extract prompt in passthrough mode (no LLM configured)
- **`store_decision` tool**: Store architectural decisions with rationale in a single call
- **`recall_context` tool**: Search persistent memories only (decisions, analysis, context) — excludes code snippets
- **`recent_context` tool**: Fetch the most recently stored memories by creation time
- **`end_session` tool**: Flush and persist indexes at session end
- **`llm_adapter`**: Multi-provider LLM abstraction (Anthropic, OpenAI, Ollama) used by the capture tool for autonomous extraction
- **`cli.py`**: `python -m mememo capture --hook` and `python -m mememo inject --hook` entry points consumed by the hook scripts
- **Benchmark suite** (`benchmarks/hooks_perf.py`): Reproducible token-savings benchmark comparing inject hook vs naive file-read baseline; reports per-prompt breakdown, mean savings, and full JSON output

### Fixed
- Tree-sitter incompatibility with `tree-sitter >= 0.22`: pinned to `<0.22` so `tree_sitter_languages` (which uses the old `Language(path, name)` API) works correctly; Go, Rust, Java, C, C#, TypeScript parsers now load without errors
- Tree-sitter parser failures now log a single `WARNING` per language on first failure and cache it — previously logged `ERROR` on every file processed for that language
- `capture` deduplication: before storing an LLM-extracted memory, a similarity search at threshold 0.97 skips near-identical content from previous sessions; dedup check fails open so a search error never silently drops a memory
- Persistent memory accumulation: type-differentiated TTL expires `conversation` memories after 30 days and `context` memories after 90 days; `decision`, `analysis`, and `summary` are durable and never auto-expired. Cleanup runs lazily at inject time. Configurable via `MEMEMO_TTL_CONVERSATION_DAYS` and `MEMEMO_TTL_CONTEXT_DAYS` (set to 0 to disable)

### Changed
- Hook inject uses a two-stage similarity filter: broad `inject_search_floor` (default 0.2) fetches candidates; `inject_min_similarity` (default 0.25) filters the final injected block — reduces noise without sacrificing recall

## [0.2.0] - 2026-02-24

### Fixed
- Pydantic V2 deprecations: replaced `class Config` with `ConfigDict`, `.dict()` with `.model_dump()`
- MCP cold-start timeout: added `warmup.py` to pre-compile bytecode and cache embedding model at install time
- `claude mcp add` failing on re-install: install scripts now remove existing entry before re-adding
- Batch script `! was unexpected` error caused by `enabledelayedexpansion` and exclamation marks in echo strings

### Changed
- Minimum Python version bumped to 3.10 (required by fastmcp)
- Version is now a single source of truth in `pyproject.toml`; all code reads it via `importlib.metadata`
- Install scripts read version dynamically from the installed package
- Type annotations modernised: `Optional[X]` → `X | None`, `List[X]` → `list[X]`, `Tuple` → `tuple`
- GitHub Actions CI simplified: single Ubuntu job, Python 3.10 + 3.12 only, removed CodeQL and security jobs

## [0.1.0] - Unreleased

Initial development version.

### Features
- FastMCP server with 9 tools + 2 resources
- Code-aware chunking (Python AST, tree-sitter for 8+ languages)
- Semantic search with FAISS vector indexing
- Git-aware branch isolation
- Incremental indexing with Merkle DAG
- Secrets detection and sanitization

### Requirements
- Python 3.10+
- Dependencies: FastMCP, Pydantic, sentence-transformers, FAISS, tree-sitter
