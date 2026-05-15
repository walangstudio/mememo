"""cypher_query MCP tool (T035 / FR-032).

Thin wrapper over mememo/core/cypher_parser.py — parses the documented
subset, translates to SQL against the relations + memories tables,
executes, and returns rows as a list of dicts. Rejects everything else
with UnsupportedCypherError surfaced as a structured error response.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..core.cypher_parser import (
    UnsupportedCypherError,
    install_regexp,
    parse_cypher,
    translate_to_sql,
)

if TYPE_CHECKING:
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


class CypherQueryParams(BaseModel):
    query: str = Field(min_length=1, description="Cypher query (documented subset only)")


class CypherQueryResponse(BaseModel):
    success: bool
    message: str
    rows: list[dict] = Field(default_factory=list)
    row_count: int = 0
    error_kind: str | None = None


async def cypher_query(
    params: CypherQueryParams, memory_manager: MemoryManager
) -> CypherQueryResponse:
    try:
        ast = parse_cypher(params.query)
        sql, sql_params, projection_keys = translate_to_sql(ast)
    except UnsupportedCypherError as e:
        return CypherQueryResponse(
            success=False,
            message=str(e),
            error_kind="unsupported",
        )

    conn = memory_manager.storage_manager.conn
    install_regexp(conn)

    try:
        cursor = conn.execute(sql, sql_params)
        # Project only the columns the user asked for in RETURN.
        all_rows = cursor.fetchall()
    except Exception as e:
        logger.error("cypher_query SQL failure", exc_info=True)
        return CypherQueryResponse(
            success=False,
            message=f"SQL error: {e}",
            error_kind="sql",
        )

    rows_out: list[dict] = []
    for row in all_rows:
        rows_out.append({user_key: row[alias] for user_key, alias in projection_keys})

    return CypherQueryResponse(
        success=True,
        message=f"{len(rows_out)} rows",
        rows=rows_out,
        row_count=len(rows_out),
    )
