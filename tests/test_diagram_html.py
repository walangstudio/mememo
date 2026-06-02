"""Tests for mememo/diagram_html.py — self-contained Mermaid HTML viewer."""

from __future__ import annotations

from mememo.diagram_html import render_html, write_html


def test_render_html_is_self_contained_document() -> None:
    html = render_html("classDiagram\nclass A", title="t")
    assert html.lstrip().startswith("<!doctype html>")
    # mermaid.js from the pinned CDN + SRI so it renders with no install.
    assert "cdn.jsdelivr.net/npm/mermaid@11" in html
    assert 'integrity="sha384-' in html
    assert 'class="mermaid"' in html
    assert "classDiagram" in html


def test_render_html_escapes_source() -> None:
    # Indexed code (file paths, class/function names) can contain HTML-special
    # chars. They must be escaped so a label like </pre><script> can't break out
    # of the <pre> and inject markup.
    html = render_html('graph TD\nA["</pre><script>alert(1)</script>"] --> B')
    assert "</pre><script>" not in html
    assert "<script>alert(1)" not in html
    assert "&lt;/pre&gt;&lt;script&gt;" in html
    assert "&amp;" in render_html("graph TD\nA & B")


def test_render_html_multiple_tabs() -> None:
    html = render_html([("Class", "classDiagram\nclass A"), ("Module", "flowchart LR\nX-->Y")])
    assert html.count('class="tab') >= 2
    assert "Class" in html and "Module" in html
    assert "panel-0" in html and "panel-1" in html


def test_render_html_empty_falls_back() -> None:
    html = render_html([])
    assert "<!doctype html>" in html.lower()
    assert "No data for this diagram" in html


def test_render_html_empty_diagram_shows_message_not_broken_mermaid() -> None:
    # A header+comment-only diagram would throw a mermaid parse error; render a
    # "no data" message instead of a <pre class="mermaid"> block.
    html = render_html("classDiagram\n%% no data")
    assert "No data for this diagram" in html
    assert 'class="mermaid"' not in html


def test_write_html_writes_file(tmp_path) -> None:
    out = tmp_path / "d.html"
    returned = write_html("classDiagram\nclass A", str(out), title="x")
    assert returned == str(out)
    assert out.exists()
    assert "classDiagram" in out.read_text(encoding="utf-8")
