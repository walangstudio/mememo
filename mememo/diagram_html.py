"""Render Mermaid text into a self-contained, double-clickable HTML page.

A ``.mmd`` file is useless to a non-developer — nothing opens it. ``render_html``
wraps the Mermaid source in a standalone page that renders in any browser with
no install (mermaid.js from the same CDN + SRI the web UI pins). Pass one
diagram or several (rendered as tabs).
"""

from __future__ import annotations

# Same pinned mermaid build + SRI hash as mememo/web/static/index.html — keep in sync.
_MERMAID_SRC = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"
_MERMAID_SRI = "sha384-yQ4mmBBT+vhTAwjFH0toJXNYJ6O4usWnt6EPIdWwrRvx2V/n5lXuDZQwQFeSFydF"


def _esc(s: str) -> str:
    """Escape so the browser hands mermaid back the exact source via textContent.

    Escapes &, <, > — sufficient because every call site emits into element text
    (``<pre>``/``<title>``/``<button>``), never an attribute value. If you ever
    interpolate this into an attribute, also escape quotes.
    """
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render_html(diagrams: str | list[tuple[str, str]], title: str = "mememo diagram") -> str:
    """Return a complete HTML document rendering the given Mermaid diagram(s).

    diagrams: a single Mermaid string, or a list of (tab_label, mermaid) pairs.
    """
    if isinstance(diagrams, str):
        items = [(title, diagrams)]
    else:
        items = list(diagrams)
    if not items:
        items = [(title, "%% (empty)")]

    tabs = "".join(
        f'<button class="tab{" active" if i == 0 else ""}" data-i="{i}">{_esc(name)}</button>'
        for i, (name, _) in enumerate(items)
    )
    panels = "".join(
        f'<div class="panel{" active" if i == 0 else ""}" id="panel-{i}">'
        f'<pre class="mermaid">{_esc(src or "%% (empty)")}</pre></div>'
        for i, (_, src) in enumerate(items)
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(title)}</title>
<script src="{_MERMAID_SRC}" integrity="{_MERMAID_SRI}" crossorigin="anonymous"></script>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin: 0; font: 14px/1.5 system-ui, sans-serif; background: #14161a; color: #e6e6e6; }}
  header {{ padding: 12px 16px; border-bottom: 1px solid #2a2e36; font-weight: 600; }}
  .tabs {{ display: flex; gap: 4px; padding: 8px 16px 0; flex-wrap: wrap; }}
  .tab {{ background: #1c1f26; color: #b8c0cc; border: 1px solid #2a2e36; border-bottom: none;
          padding: 6px 14px; border-radius: 6px 6px 0 0; cursor: pointer; }}
  .tab.active {{ background: #14161a; color: #fff; border-color: #3a8bfd; }}
  .panel {{ display: none; padding: 24px 16px; overflow: auto; }}
  .panel.active {{ display: block; }}
  .mermaid {{ background: transparent; }}
  #err {{ display: none; margin: 16px; padding: 12px; background: #3a1d1d; border: 1px solid #a33;
          border-radius: 6px; white-space: pre-wrap; }}
</style>
</head>
<body>
<header>{_esc(title)}</header>
<div class="tabs">{tabs}</div>
{panels}
<div id="err"></div>
<script>
  const tabs = document.querySelectorAll('.tab');
  const panels = document.querySelectorAll('.panel');
  tabs.forEach(t => t.addEventListener('click', () => {{
    const i = t.dataset.i;
    tabs.forEach(x => x.classList.toggle('active', x === t));
    panels.forEach(p => p.classList.toggle('active', p.id === 'panel-' + i));
  }}));
  function boot() {{
    if (typeof mermaid === 'undefined') {{
      const e = document.getElementById('err');
      e.style.display = 'block';
      e.textContent = 'mermaid.js failed to load (no internet?). The diagram source is in the page; ' +
        'paste it into https://mermaid.live to view offline.';
      return;
    }}
    mermaid.initialize({{ startOnLoad: false, theme: 'dark', securityLevel: 'strict' }});
    mermaid.run();
  }}
  window.addEventListener('load', boot);
</script>
</body>
</html>
"""


def write_html(
    diagrams: str | list[tuple[str, str]], out_path: str, title: str = "mememo diagram"
) -> str:
    """Write the rendered HTML to out_path and return the path."""
    from pathlib import Path

    html = render_html(diagrams, title=title)
    Path(out_path).write_text(html, encoding="utf-8")
    return out_path
