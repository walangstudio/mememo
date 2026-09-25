"""Query building, BM25 ranking, and the injected blocks. Stdlib only (hook path)."""

import re

from . import store

STOP = set("""
a about above after again all also am an and any are as at be because been before being below between both but by
can could did do does doing done down during each else even ever every few for from further get gets got had has
have having he her here hers him his how i if in into is it its itself just let like make me more most my no nor not
now of off on once only or other our out over own please same she should so some such than that the their them then
there these they this those through to too under until up us very via want was we were what when where which while
who whom why will with would you your yours yes ok okay thanks thank hi hey pls lets let's use using used need needs
new also again still really maybe sure right one two way thing things something anything someone help show tell give
look see try go going run check fix make add update change find work working works time now today yeah
""".split())

_NONWORD = re.compile(r"[^a-z0-9]+")
MAX_TERMS = 12
BUDGET_CHARS = 3200  # ~800 tokens
HEADER = (
    "Auto-recalled from mememo memory. Notes are dated and may be stale: verify against the code before "
    "acting. If the answer is not in memory or the code, say you don't know instead of guessing."
)


def terms(prompt):
    out = list(dict.fromkeys(w for w in re.findall(r"[a-z0-9]+", prompt.lower()) if w not in STOP and len(w) > 1))
    if len(out) > MAX_TERMS:
        # ponytail: longest terms as a rarity proxy; switch to fts5vocab doc counts if long prompts rank badly
        out = sorted(out, key=len, reverse=True)[:MAX_TERMS]
    return out


def _matched(ts, text):
    """Number of query terms present in text, matching on a stem-like prefix."""
    text = " " + _NONWORD.sub(" ", text.lower())
    return sum(1 for t in ts if " " + t[: max(4, len(t) - 2)] in text)


def _relevant(matched, total, score):
    # ponytail: gate tuned on the real corpus (negatives match <= 2 terms, <= 10.1 bm25); retune via eval if it drifts
    cov = matched / total
    return matched >= 3 or cov >= 0.6 or (cov >= 0.4 and score >= 12)


def search(con, prompt, lane=None, k=6, include_old=False):
    ts = terms(prompt)
    if not ts:
        return []
    q = " OR ".join(f'"{t}"' for t in ts)
    where = "" if include_old else "and c.current=1 and c.superseded=0"
    rows = con.execute(
        "select c.sha, c.path, c.lane, c.type, c.card_id, c.date, c.claim, c.body, c.current, c.superseded,"
        f" -bm25(fts, 4.0, 1.0) from fts join chunks c on c.id=fts.rowid where fts match ? {where}"
        " order by bm25(fts, 4.0, 1.0) limit 60",
        (q,),
    ).fetchall()
    hits, per_file = [], {}
    for r in rows:
        if not _relevant(_matched(ts, r[6] + " " + r[7]), len(ts), r[10]):
            continue
        mult = 1.5 if lane and r[2] == lane else 1.2 if r[2] == store.GLOBAL else 1.0
        hits.append(
            {
                "id": r[0],
                "path": r[1],
                "lane": r[2],
                "type": r[3],
                "card_id": r[4],
                "date": r[5],
                "claim": r[6],
                "body": r[7],
                "current": r[8],
                "superseded": r[9],
                "score": r[10] * mult,
            }
        )
    hits.sort(key=lambda h: -h["score"])
    out = []
    for h in hits:
        if out and h["score"] < 0.5 * out[0]["score"]:
            break
        if per_file.get(h["path"], 0) >= 2:
            continue
        per_file[h["path"]] = per_file.get(h["path"], 0) + 1
        out.append(h)
        if len(out) >= k:
            break
    return out


def _label(h):
    tags = [h["card_id"] or "", h["type"], h["date"] or ""]
    if h.get("superseded"):
        tags.append("SUPERSEDED")
    elif not h.get("current", 1):
        tags.append("DELETED")
    return f"[{' '.join(t for t in tags if t)} | {h['path']}]"


def recall_block(hits, budget=BUDGET_CHARS):
    """Top 3 with verbatim body, the rest as claim lines; never cut a body mid-way."""
    if not hits:
        return ""
    lines, used = ["<mememo-recall>", HEADER], 0
    for i, h in enumerate(hits):
        entry = f"{_label(h)} {h['claim']}"
        if i < 3 and h["body"] and used + len(entry) + len(h["body"]) <= budget:
            entry += "\n" + h["body"]
        if used + len(entry) > budget:
            break
        lines.append(entry)
        used += len(entry)
    lines.append("</mememo-recall>")
    return "\n".join(lines)


PROTOCOL = """Persistent memory is mememo (it replaces auto memory; MEMORY.md is not used). Current lane: {lane}.
Relevant memories are injected automatically per prompt inside <mememo-recall>. Explicit lookup: `mememo search "<query>"`; full file or card: `mememo show <card id or path>`.
Save durable knowledge (user corrections, preferences, decisions, non-obvious outcomes, project state) with Bash:
  mememo add --type feedback|user|project|reference --topic <slug> --claim "<one factual sentence>" --body "<verbatim details: exact commands, paths, versions, why>" [--lane global|here] [--supersedes <card id>] [--src <file or commit>]
The body records only what was actually said or observed (quote the user; exact values); never add steps, rules or details nobody stated.
Before adding, `mememo search` for it; if an existing card states an older version of the fact, pass --supersedes <its id> and copy every still-true fact from the old card into the new body (a superseded card is hidden from recall). Use --lane global for user-wide rules, here for this project. Never delete memory files. Never store secrets."""


def digest(con, lane, budget=8000):
    head = f"<mememo>\n{PROTOCOL.format(lane=lane)}\nMemory index (rules first, then this lane, then recent):"
    rows = con.execute(
        "select path, lane, type, description from files order by"
        " case when type in ('feedback','user') then 0 when lane=? then 1 else 2 end, mtime desc",
        (lane,),
    ).fetchall()
    lines, used = [], len(head)
    for path, _, type_, desc in rows:
        line = f"- [{type_}] {path}: {desc}"
        if used + len(line) > budget:
            lines.append(f"(+{len(rows) - len(lines)} more files; surfaced per prompt or via mememo search)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join([head, *lines, "</mememo>"])
