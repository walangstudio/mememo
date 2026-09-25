"""Memory files: paths, lanes, frontmatter, cards, secrets. Stdlib only (hook path)."""

import hashlib
import os
import re
import secrets
import time

GLOBAL = "global"
TYPES = ("user", "feedback", "project", "reference")

_HEAD = re.compile(r"^#{1,3}\s+(.+?)\s*$")
_META = re.compile(r"^id:\s*(m-[0-9a-z]+)\s*(.*)$")
_SECRETS = [
    re.compile(p)
    for p in (
        r"AKIA[0-9A-Z]{16}",
        r"gh[pousr]_[A-Za-z0-9]{36}",
        r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[A-Za-z0-9]{24,}",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"(?i)(?:mysql|postgres(?:ql)?|mongodb)://[^\s:/]+:[^\s@]+@\S+",
        r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
        r"(?i)(?:api[_-]?key|secret|token|password)\s*[=:]\s*[\"']?[A-Za-z0-9_\-/+=]{20,}",
    )
]


def home():
    return os.environ.get("MEMEMO_HOME") or os.path.join(os.path.expanduser("~"), ".mememo")


def mem_root():
    return os.path.join(home(), "memory")


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "notes"


LANE_RE = re.compile(r"^(global|repos/[a-z0-9._-]+/[a-z0-9._-]+|dirs/[a-z0-9-]+)$")


def _dir_lane(d):
    tail = "-".join(os.path.normpath(d).replace("\\", "/").split("/")[-2:])
    return f"dirs/{slug(tail)[:40]}-{hashlib.sha256(os.path.normcase(d).encode()).hexdigest()[:8]}"


def normalize_remote(url):
    url = url.strip()
    m = re.match(r"^[^@/]+@[^:/]+:(.+)$", url)  # scp-style git@host:owner/repo
    path = m.group(1) if m else re.sub(r"^[a-z+]+://(?:[^@/]+@)?[^/]+/", "", url)
    parts = [p for p in re.sub(r"\.git/?$", "", path).split("/") if p]
    return "/".join(parts[-2:]).lower() if len(parts) >= 2 else None


def _origin_url(git):
    gitdir = git
    if os.path.isfile(git):
        with open(git, encoding="utf-8") as f:
            gitdir = f.read().split("gitdir:", 1)[-1].strip()
        gitdir = os.path.join(os.path.dirname(git), gitdir)
        common = os.path.join(gitdir, "commondir")
        if os.path.isfile(common):
            with open(common, encoding="utf-8") as f:
                gitdir = os.path.normpath(os.path.join(gitdir, f.read().strip()))
    try:
        with open(os.path.join(gitdir, "config"), encoding="utf-8") as f:
            cfg = f.read()
    except OSError:
        return None
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', cfg)
    return m.group(1) if m else None


def lane_for(cwd):
    """repos/<owner>/<repo> for a git checkout with an origin, else dirs/<slug>."""
    start = os.path.abspath(cwd or os.getcwd())
    d = start
    while True:
        git = os.path.join(d, ".git")
        if os.path.exists(git):
            name = normalize_remote(_origin_url(git) or "")
            return "repos/" + name if name and LANE_RE.match("repos/" + name) else _dir_lane(d)
        parent = os.path.dirname(d)
        if parent == d:
            return _dir_lane(start)
        d = parent


def parse_frontmatter(text):
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[3:end].splitlines():
        m = re.match(r"^\s*([A-Za-z_]+):\s*(.*?)\s*$", line)
        if m and m.group(2):
            meta.setdefault(m.group(1), m.group(2).strip("\"'"))
    return meta, text[end + 4 :].lstrip("\n")


def _pack(text, size=700):
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        if buf and len(buf) + len(para) > size:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        out.append(buf)
    return out


def chunks(path, text):
    """Yield dicts: claim, body, card_id, date, supersedes, src. Cards stay whole; prose packs to ~700 chars."""
    meta, body = parse_frontmatter(text)
    desc = meta.get("description", "")
    stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ").replace("-", " ")
    # A card is "## claim" directly followed by an "id: m-..." line; headings inside a card body stay in the body.
    sections, heading, lines, in_card = [], "", [], False
    rows = body.splitlines()
    for i, line in enumerate(rows):
        m = _HEAD.match(line)
        starts_card = bool(m) and i + 1 < len(rows) and bool(_META.match(rows[i + 1].strip()))
        if m and (starts_card or not in_card):
            sections.append((heading, "\n".join(lines)))
            heading, lines, in_card = m.group(1), [], starts_card
        else:
            lines.append(line)
    sections.append((heading, "\n".join(lines)))
    for heading, sec in sections:
        first, _, rest = sec.lstrip("\n").partition("\n")
        m = _META.match(first.strip())
        if m:
            fields = dict(re.findall(r"\|\s*([a-z_]+):\s*([^|]+?)\s*(?=\||$)", m.group(2)))
            card = {
                "card_id": m.group(1),
                "date": fields.get("date"),
                "supersedes": fields.get("supersedes"),
                "src": fields.get("src"),
            }
            for part in _pack(rest, 1400):
                yield {"claim": heading, "body": part, **card}
            if not rest.strip():
                yield {"claim": heading, "body": "", **card}
            continue
        claim = f"{stem}: {desc}" + (f" / {heading}" if heading else "")
        for part in _pack(sec):
            yield {"claim": claim, "body": part, "card_id": None, "date": None, "supersedes": None, "src": None}


def find_secret(text):
    for rx in _SECRETS:
        m = rx.search(text)
        if m:
            return m.group(0)
    return None


def redact(text):
    for rx in _SECRETS:
        text = rx.sub("[REDACTED]", text)
    return text


def new_id():
    return "m-" + format(time.time_ns() // 1000, "x") + secrets.token_hex(4)


def add_card(lane, topic, type_, claim, body, supersedes=None, src=None):
    """Append a card to <lane>/<topic>.md, creating it with frontmatter. Returns (card_id, path)."""
    claim = " ".join(claim.split())
    if not claim:
        raise ValueError("claim is required")
    if type_ not in TYPES:
        raise ValueError(f"type must be one of {TYPES}")
    hit = find_secret(claim + "\n" + body)
    if hit:
        raise ValueError(f"refusing to store a secret-looking value: {hit[:12]}...")
    if not LANE_RE.match(lane):
        raise ValueError(f"invalid lane {lane!r}: use global, repos/<owner>/<repo> or dirs/<slug>")
    path = os.path.join(mem_root(), *lane.split("/"), slug(topic) + ".md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cid = new_id()
    meta = f"id: {cid} | date: {time.strftime('%Y-%m-%d')}"
    meta += f" | supersedes: {supersedes}" if supersedes else ""
    meta += f" | src: {src}" if src else ""
    card = f"\n## {claim}\n{meta}\n{body.strip()}\n"
    lock = path + ".lock"
    for _ in range(40):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL)
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 10:  # stale lock left by a killed writer
                    os.remove(lock)
                    continue
            except OSError:
                pass
            time.sleep(0.05)
    else:
        raise TimeoutError(f"memory file locked: {lock}")
    try:
        new = not os.path.exists(path)
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if new:
                f.write(f"---\nname: {slug(topic)}\ndescription: {claim}\nmetadata:\n  type: {type_}\n---\n")
            f.write(card)
    finally:
        os.close(fd)
        os.remove(lock)
    return cid, path
