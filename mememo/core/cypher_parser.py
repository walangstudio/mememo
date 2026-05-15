"""Cypher subset parser + SQL translator (T035 / FR-032).

Supported grammar (case-insensitive keywords):

    MATCH (a)-[r:TYPE]->(b)         # directed single-hop
    MATCH (a)-[r:TYPE]-(b)          # undirected
    [WHERE <expr> [AND/OR <expr>]*] # expressions: a.prop OP literal
                                    # OPs: =, <>, =~ (regex)
    RETURN <projection>[, ...]      # projection: ident.prop [AS alias]
    [LIMIT n]

The translator emits a parametrised SQL query over the ``memories`` +
``relations`` tables. Any other Cypher construct (variable-length paths,
WITH, MERGE/CREATE/DELETE, aggregations, OPTIONAL MATCH, etc.) raises
``UnsupportedCypherError`` naming the offending token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal


class UnsupportedCypherError(ValueError):
    """Raised when a query uses a construct outside the documented subset."""


_RESERVED_UNSUPPORTED = (
    "WITH",
    "OPTIONAL",
    "CREATE",
    "MERGE",
    "DELETE",
    "SET",
    "REMOVE",
    "DETACH",
    "CALL",
    "UNWIND",
    "ORDER",
    "SKIP",
    "UNION",
    "FOREACH",
)


# ---------- AST -------------------------------------------------------------


@dataclass
class _Pattern:
    src_var: str
    src_label: str | None  # not used in v0.6 — present for future widening
    rel_var: str
    rel_type: str
    direction: Literal["out", "undirected"]
    tgt_var: str
    tgt_label: str | None = None


@dataclass
class _Predicate:
    lhs_var: str
    lhs_prop: str
    op: Literal["=", "<>", "=~"]
    literal: str


@dataclass
class _Projection:
    var: str
    prop: str
    alias: str | None = None


@dataclass
class CypherQuery:
    pattern: _Pattern
    predicates: list[_Predicate] = field(default_factory=list)
    connectors: list[Literal["AND", "OR"]] = field(default_factory=list)
    projections: list[_Projection] = field(default_factory=list)
    limit: int | None = None


# ---------- regex tokens ----------------------------------------------------

_PAT_RE = re.compile(
    r"\(\s*(?P<src>\w+)\s*\)"
    r"\s*-\s*\[\s*(?P<rel>\w+)\s*:\s*(?P<type>\w+)\s*\]"
    r"\s*(?P<arrow>->|-)\s*"
    r"\(\s*(?P<tgt>\w+)\s*\)",
    re.IGNORECASE,
)

_EXPR_RE = re.compile(
    r"(?P<var>\w+)\s*\.\s*(?P<prop>\w+)"
    r"\s*(?P<op>=~|<>|=)\s*"
    r"(?P<lit>'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|-?\d+(?:\.\d+)?)"
)


# ---------- parse -----------------------------------------------------------


def _ensure_no_unsupported(query_upper: str) -> None:
    """Reject any unsupported keyword up front so error messages are clear."""
    for kw in _RESERVED_UNSUPPORTED:
        if re.search(rf"\b{kw}\b", query_upper):
            raise UnsupportedCypherError(f"Unsupported Cypher construct: {kw}")
    # Variable-length paths look like `-[*]->` or `-[r:T*1..3]->`.
    if re.search(r"\[[^\]]*\*[^\]]*\]", query_upper):
        raise UnsupportedCypherError("Variable-length paths (e.g. *1..3) are not supported")


def _parse_match(query: str) -> _Pattern:
    m = _PAT_RE.search(query)
    if not m:
        raise UnsupportedCypherError(
            "MATCH must be a single-hop pattern: (a)-[r:TYPE]->(b) or " "(a)-[r:TYPE]-(b)"
        )
    return _Pattern(
        src_var=m.group("src"),
        src_label=None,
        rel_var=m.group("rel"),
        rel_type=m.group("type"),
        direction="out" if m.group("arrow") == "->" else "undirected",
        tgt_var=m.group("tgt"),
    )


def _parse_where(where_clause: str) -> tuple[list[_Predicate], list[str]]:
    preds: list[_Predicate] = []
    connectors: list[str] = []
    cursor = 0
    while cursor < len(where_clause):
        m = _EXPR_RE.search(where_clause, cursor)
        if not m:
            raise UnsupportedCypherError(
                f"Unsupported WHERE expression at: {where_clause[cursor:].strip()!r}"
            )
        lit = m.group("lit")
        if lit.startswith(("'", '"')):
            lit = lit[1:-1]
        preds.append(
            _Predicate(
                lhs_var=m.group("var"),
                lhs_prop=m.group("prop"),
                op=m.group("op"),  # type: ignore[arg-type]
                literal=lit,
            )
        )
        cursor = m.end()
        tail = where_clause[cursor:].lstrip()
        if not tail:
            break
        connector = tail.split(None, 1)[0].upper()
        if connector not in ("AND", "OR"):
            raise UnsupportedCypherError(f"WHERE connector must be AND/OR; got {connector!r}")
        connectors.append(connector)
        cursor = where_clause.index(connector, cursor) + len(connector)
    return preds, connectors


def _parse_return(ret_clause: str) -> list[_Projection]:
    projections: list[_Projection] = []
    for item in ret_clause.split(","):
        item = item.strip()
        if not item:
            continue
        alias = None
        if " AS " in item.upper():
            head, alias = re.split(r"\s+AS\s+", item, maxsplit=1, flags=re.IGNORECASE)
            head = head.strip()
            alias = alias.strip()
        else:
            head = item
        if "." not in head:
            raise UnsupportedCypherError(
                f"RETURN items must be property access (var.prop): {head!r}"
            )
        var, prop = head.split(".", 1)
        projections.append(_Projection(var=var.strip(), prop=prop.strip(), alias=alias))
    if not projections:
        raise UnsupportedCypherError("RETURN clause requires at least one projection")
    return projections


def parse_cypher(query: str) -> CypherQuery:
    """Parse a query in the documented subset; raise UnsupportedCypherError otherwise."""
    q = query.strip().rstrip(";")
    if not q:
        raise UnsupportedCypherError("empty query")
    upper = q.upper()
    _ensure_no_unsupported(upper)
    if not upper.startswith("MATCH "):
        raise UnsupportedCypherError("Query must start with MATCH")

    # Split into clauses by keyword. Hand-rolled because the parser is tiny.
    # Find clause boundaries by scanning for the keywords in order.
    clauses: dict[str, str] = {}
    keywords = ["MATCH", "WHERE", "RETURN", "LIMIT"]
    positions: list[tuple[str, int]] = []
    for kw in keywords:
        m = re.search(rf"\b{kw}\b", upper)
        if m:
            positions.append((kw, m.start()))
    positions.sort(key=lambda p: p[1])
    for i, (kw, start) in enumerate(positions):
        end = positions[i + 1][1] if i + 1 < len(positions) else len(q)
        body = q[start + len(kw) : end].strip()
        clauses[kw] = body

    pattern = _parse_match(clauses["MATCH"])
    predicates: list[_Predicate] = []
    connectors: list[str] = []
    if "WHERE" in clauses:
        predicates, connectors = _parse_where(clauses["WHERE"])
    if "RETURN" not in clauses:
        raise UnsupportedCypherError("Query missing RETURN clause")
    projections = _parse_return(clauses["RETURN"])

    limit: int | None = None
    if "LIMIT" in clauses:
        try:
            limit = int(clauses["LIMIT"].strip())
        except ValueError as e:
            raise UnsupportedCypherError(f"LIMIT must be an integer: {e}") from e

    return CypherQuery(
        pattern=pattern,
        predicates=predicates,
        connectors=connectors,  # type: ignore[arg-type]
        projections=projections,
        limit=limit,
    )


# ---------- SQL translation -------------------------------------------------


# Map (alias_kind, prop_name) -> SQL column expression.
_MEMORY_PROP_TO_COL = {
    "id": "{a}.id",
    "file_path": "{a}.file_path",
    "function_name": "{a}.function_name",
    "class_name": "{a}.class_name",
    "language": "{a}.language",
    "branch": "{a}.branch_name",
    "stale": "{a}.stale",
    "risk_grade": "{a}.risk_grade",
    "created_at_sha": "{a}.created_at_sha",
}
_REL_PROP_TO_COL = {
    "id": "{r}.id",
    "type": "{r}.type",
    "confidence": "{r}.confidence",
    "community": "{r}.community",
    "created_at_sha": "{r}.created_at_sha",
    "target_symbol": "{r}.target_symbol",
}


def _column_for(var: str, prop: str, query: CypherQuery) -> str:
    if var == query.pattern.rel_var:
        tmpl = _REL_PROP_TO_COL.get(prop)
        if tmpl is None:
            raise UnsupportedCypherError(
                f"Unknown relation property {var}.{prop} (allowed: "
                f"{', '.join(sorted(_REL_PROP_TO_COL))})"
            )
        return tmpl.format(r="r")
    if var in (query.pattern.src_var, query.pattern.tgt_var):
        tmpl = _MEMORY_PROP_TO_COL.get(prop)
        if tmpl is None:
            raise UnsupportedCypherError(
                f"Unknown node property {var}.{prop} (allowed: "
                f"{', '.join(sorted(_MEMORY_PROP_TO_COL))})"
            )
        alias = "src" if var == query.pattern.src_var else "tgt"
        return tmpl.format(a=alias)
    raise UnsupportedCypherError(
        f"Unknown variable {var} (declared: "
        f"{query.pattern.src_var}, {query.pattern.rel_var}, {query.pattern.tgt_var})"
    )


def _projection_alias(proj: _Projection) -> str:
    """Stable SQL alias for a Cypher projection, matching what the tool
    layer reads back out of the row."""
    return f"col_{proj.var}_{proj.prop}"


def translate_to_sql(
    query: CypherQuery,
) -> tuple[str, list, list[tuple[str, str]]]:
    """Translate parsed CypherQuery into a parametrised SQL string + params.

    Returns ``(sql, params, projection_keys)`` where ``projection_keys`` is
    a list of ``(user_key, sql_column_alias)`` pairs in projection order.
    Callers iterate that mapping to read rows back without needing to know
    the SQL alias scheme.
    """
    sql_parts: list[str] = []
    params: list = []

    # Build SELECT list from projections — each gets a stable alias.
    select_parts: list[str] = []
    projection_keys: list[tuple[str, str]] = []
    for proj in query.projections:
        col = _column_for(proj.var, proj.prop, query)
        alias = _projection_alias(proj)
        select_parts.append(f"{col} AS {alias}")
        user_key = proj.alias or f"{proj.var}.{proj.prop}"
        projection_keys.append((user_key, alias))
    sql_parts.append("SELECT " + ", ".join(select_parts))

    sql_parts.append(
        "FROM relations r "
        "INNER JOIN memories src ON src.id = r.source_memory_id "
        "LEFT JOIN memories tgt ON tgt.id = r.target_memory_id "
        "WHERE r.type = ?"
    )
    params.append(query.pattern.rel_type)

    if query.predicates:
        sql_parts.append("AND (")
        for i, pred in enumerate(query.predicates):
            if i > 0:
                sql_parts.append(query.connectors[i - 1])
            col = _column_for(pred.lhs_var, pred.lhs_prop, query)
            if pred.op == "=~":
                sql_parts.append(f"{col} REGEXP ?")
            else:
                sql_parts.append(f"{col} {pred.op} ?")
            params.append(pred.literal)
        sql_parts.append(")")

    if query.limit is not None:
        sql_parts.append("LIMIT ?")
        params.append(query.limit)

    return " ".join(sql_parts), params, projection_keys


# Custom REGEXP function for sqlite3 — installed lazily by the tool layer so
# importing this module doesn't depend on a live connection.
def install_regexp(connection) -> None:
    """Register a REGEXP function on a sqlite3 connection so ``=~`` works."""

    def _regexp(pattern, value):
        if pattern is None or value is None:
            return 0
        try:
            return 1 if re.search(str(pattern), str(value)) else 0
        except re.error:
            return 0

    connection.create_function("REGEXP", 2, _regexp)
