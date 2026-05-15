"""mememo web UI (T033 / T034 / FR-030, FR-031).

A localhost-only FastAPI app + a single-page frontend that browses the
memory graph. `mememo serve` boots uvicorn against this app on 127.0.0.1.
"""

from .app import create_app

__all__ = ["create_app"]
