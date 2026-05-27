/* mememo web UI client (T034 / FR-031).
 *
 * Talks to the FastAPI routes:
 *   GET /repos
 *   GET /memories?repo_id&branch&limit&offset
 *   GET /relations?repo_id&branch&community&limit
 *   GET /communities?repo_id&branch
 *   GET /snapshots/{sha}?repo_id&branch
 *
 * Renders three views from the same data: a D3-force graph, a paged
 * memories table, and a time-travel state where only memories alive
 * at the given SHA are shown.
 */

const state = {
  repoId: null,
  branch: null,
  community: null,
  page: 0,
  pageSize: 50,
  total: 0,
  asOfSha: null, // null = live; sha prefix = filter to memories alive at that SHA
  aliveSet: null, // populated from /snapshots so the graph view can dim nodes
  colorByCommunity: false, // off = neutral fill; on = community palette
};

// Edge stroke by relation type. Unknown types fall back to the muted gray.
const EDGE_COLORS = {
  CALLS: '#6cbef7',
  IMPORTS: '#6cf7a9',
  EXTENDS: '#f7a06c',
  IMPLEMENTS: '#c98cf7',
  USES: '#f7e06c',
  DOCUMENTS: '#8a93a3',
};
const edgeColor = (t) => EDGE_COLORS[t] || '#5a667a';

const $ = (id) => document.getElementById(id);

const basename = (p) => (p ? p.split(/[\\/]/).pop() : '');

// Compact node label: "file.py:Class.fn", falling back to the resolved
// symbol, then a short UUID. Built client-side from the /relations parts.
function makeLabel(file, cls, fn, symbol, id) {
  const qualified = `${cls ? cls + '.' : ''}${fn || ''}`;
  if (file) return qualified ? `${basename(file)}:${qualified}` : basename(file);
  if (qualified) return qualified;
  if (symbol) return symbol;
  return (id || '').slice(0, 8);
}

const repoSelect = $('repo-select');
const branchInput = $('branch-input');
const communitySelect = $('community-select');
const refreshBtn = $('refresh');
const shaInput = $('sha-input');
const snapshotBtn = $('snapshot');
const snapshotClearBtn = $('snapshot-clear');
const snapshotState = $('snapshot-state');
const tbody = document.querySelector('#memories tbody');
const pageInfo = $('page-info');
const prevBtn = $('prev');
const nextBtn = $('next');
const svg = d3.select('#graph');
const legend = $('graph-legend');
const fitBtn = $('fit');
const resetBtn = $('reset');
const communityToggle = $('community-toggle');

// Set per render so the header controls can drive the live graph.
let zoomBehavior = null;
let fitView = () => {};
let recolorNodes = () => {};

async function fetchJson(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> ${r.status}`);
  return r.json();
}

function qs(params) {
  const u = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== null && v !== undefined && v !== '') u.set(k, v);
  });
  const s = u.toString();
  return s ? `?${s}` : '';
}

async function loadRepos() {
  const repos = await fetchJson('/repos');
  repoSelect.innerHTML = '';
  repos.forEach((r) => {
    const opt = document.createElement('option');
    opt.value = r.repo_id;
    opt.textContent = `${r.repo_name || r.repo_id.slice(0, 8)} (${r.memories})`;
    repoSelect.appendChild(opt);
  });
  if (repos.length > 0) state.repoId = repos[0].repo_id;
}

async function loadCommunities() {
  const data = await fetchJson(
    '/communities' + qs({ repo_id: state.repoId, branch: state.branch })
  );
  communitySelect.innerHTML = '<option value="">(all)</option>';
  data.items.forEach((c) => {
    const opt = document.createElement('option');
    opt.value = c.id;
    opt.textContent = `c${c.id} — ${c.members} nodes, ${c.edges} edges`;
    communitySelect.appendChild(opt);
  });
}

async function loadMemories() {
  // Server-side as_of_sha keeps total + pagination math consistent with the
  // alive set; no client-side filtering required.
  const data = await fetchJson(
    '/memories' +
      qs({
        repo_id: state.repoId,
        branch: state.branch,
        as_of_sha: state.asOfSha,
        limit: state.pageSize,
        offset: state.page * state.pageSize,
      })
  );
  state.total = data.total;
  tbody.innerHTML = '';
  data.items.forEach((row) => {
    const tr = document.createElement('tr');
    const cls = row.class_name ? `${row.class_name}.` : '';
    const fn = row.function_name || '';
    const risk = row.risk_grade || '';
    tr.innerHTML = `
      <td class="id">${row.id.slice(0, 8)}</td>
      <td>${row.file_path || ''}</td>
      <td>${cls}${fn}</td>
      <td>${row.content_type}</td>
      <td class="risk-${risk}">${risk}</td>
      <td class="id">${(row.created_at_sha || '').slice(0, 8)}</td>`;
    tbody.appendChild(tr);
  });
  const start = data.total === 0 ? 0 : state.page * state.pageSize + 1;
  pageInfo.textContent = `${start}–${
    state.page * state.pageSize + data.items.length
  } of ${data.total}${state.asOfSha ? ` (as of ${state.asOfSha})` : ''}`;
}

async function loadGraph() {
  const params = {
    repo_id: state.repoId,
    branch: state.branch,
    limit: 1000,
  };
  if (state.community !== null && state.community !== '') {
    params.community = state.community;
  }
  const data = await fetchJson('/relations' + qs(params));
  renderGraph(data.items);
}

function renderGraph(edges) {
  const nodeIds = new Set();
  const labels = new Map();
  const links = [];
  edges.forEach((e) => {
    if (!e.target_memory_id) return; // skip unresolved
    nodeIds.add(e.source_memory_id);
    nodeIds.add(e.target_memory_id);
    labels.set(
      e.source_memory_id,
      makeLabel(e.source_file, e.source_class, e.source_fn, null, e.source_memory_id)
    );
    labels.set(
      e.target_memory_id,
      makeLabel(e.target_file, e.target_class, e.target_fn, e.target_symbol, e.target_memory_id)
    );
    links.push({
      source: e.source_memory_id,
      target: e.target_memory_id,
      type: e.type,
      community: e.community,
    });
  });
  // Degree from the raw id-keyed links (forceLink mutates source/target into
  // node objects later, so count now while they're still ids).
  const degree = new Map();
  links.forEach((l) => {
    degree.set(l.source, (degree.get(l.source) || 0) + 1);
    degree.set(l.target, (degree.get(l.target) || 0) + 1);
  });
  const nodes = Array.from(nodeIds).map((id) => ({
    id,
    label: labels.get(id) || id.slice(0, 8),
    degree: degree.get(id) || 0,
    faded: state.aliveSet && !state.aliveSet.has(id),
  }));
  const maxDegree = d3.max(nodes, (d) => d.degree) || 1;
  const radiusFor = d3.scaleSqrt().domain([1, maxDegree]).range([4, 14]).clamp(true);

  svg.selectAll('*').remove();
  const w = svg.node().clientWidth;
  const h = svg.node().clientHeight;
  const viewport = svg.append('g').attr('class', 'viewport');

  zoomBehavior = d3
    .zoom()
    .scaleExtent([0.1, 8])
    .on('zoom', (event) => {
      viewport.attr('transform', event.transform);
      // Labels would be an unreadable smear at fit-out scale; reveal on zoom-in.
      viewport.classed('zoomed-in', event.transform.k > 1.5);
    });
  svg.call(zoomBehavior);

  const palette = d3.schemeTableau10;
  const colorFor = (c) =>
    c === null || c === undefined ? '#5a667a' : palette[c % palette.length];

  const sim = d3
    .forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(40))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(w / 2, h / 2));

  const link = viewport
    .append('g')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('stroke', (d) => edgeColor(d.type))
    .attr('class', (d) =>
      'link ' + (state.aliveSet && (!state.aliveSet.has(d.source.id || d.source) ||
                                    !state.aliveSet.has(d.target.id || d.target))
        ? 'faded'
        : '')
    );

  const node = viewport
    .append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('class', (d) => 'node' + (d.faded ? ' faded' : ''))
    .attr('r', (d) => radiusFor(d.degree || 1))
    .call(
      d3
        .drag()
        .on('start', (event, d) => {
          event.sourceEvent.stopPropagation(); // node-drag, not pan
          if (!event.active) sim.alphaTarget(0.3).restart();
          d.fx = d.x;
          d.fy = d.y;
        })
        .on('drag', (event, d) => {
          d.fx = event.x;
          d.fy = event.y;
        })
        .on('end', (event, d) => {
          if (!event.active) sim.alphaTarget(0);
          d.fx = null;
          d.fy = null;
        })
    );

  // Default fill is neutral (degree drives size, type drives edges); the
  // community palette is an opt-in toggle since real data is unclustered.
  const adjacency = new Map(nodes.map((d) => [d.id, null]));
  links.forEach((l) => {
    const s = l.source.id || l.source;
    const t = l.target.id || l.target;
    if (l.community != null) {
      adjacency.set(s, l.community);
      adjacency.set(t, l.community);
    }
  });
  const nodeFill = (d) =>
    state.colorByCommunity ? colorFor(adjacency.get(d.id)) : '#6cbef7';
  node.attr('fill', nodeFill);
  recolorNodes = () => node.attr('fill', nodeFill);

  node.append('title').text((d) => d.label);

  const labelSel = viewport
    .append('g')
    .attr('class', 'labels')
    .selectAll('text')
    .data(nodes)
    .join('text')
    .attr('class', 'node-label')
    .attr('dx', 8)
    .attr('dy', 3)
    .text((d) => d.label);

  sim.on('tick', () => {
    link
      .attr('x1', (d) => d.source.x)
      .attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x)
      .attr('y2', (d) => d.target.y);
    node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
    labelSel.attr('x', (d) => d.x).attr('y', (d) => d.y);
  });

  fitView = () => {
    if (nodes.length === 0) return;
    const minX = d3.min(nodes, (d) => d.x);
    const maxX = d3.max(nodes, (d) => d.x);
    const minY = d3.min(nodes, (d) => d.y);
    const maxY = d3.max(nodes, (d) => d.y);
    const gw = maxX - minX || 1;
    const gh = maxY - minY || 1;
    const scale = Math.max(0.1, Math.min(8, 0.9 * Math.min(w / gw, h / gh)));
    const tx = w / 2 - (scale * (minX + maxX)) / 2;
    const ty = h / 2 - (scale * (minY + maxY)) / 2;
    svg
      .transition()
      .duration(400)
      .call(zoomBehavior.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  };
  // Frame the graph once the layout settles.
  sim.on('end', fitView);

  const types = Array.from(new Set(links.map((l) => l.type))).filter(Boolean).sort();
  legend.innerHTML = types
    .map((t) => `<span style="color:${edgeColor(t)}">━</span> ${t}`)
    .join('  ');
}

async function applySnapshot() {
  const sha = shaInput.value.trim();
  if (!sha) return;
  // Hit /snapshots to populate the graph's alive set; /memories uses
  // as_of_sha server-side so total + pagination stay accurate.
  const data = await fetchJson(
    `/snapshots/${encodeURIComponent(sha)}` +
      qs({ repo_id: state.repoId, branch: state.branch })
  );
  state.asOfSha = sha;
  state.aliveSet = new Set(data.alive_memory_ids);
  state.page = 0;
  snapshotState.textContent = `snapshot ${sha} (${state.aliveSet.size} alive)`;
  snapshotState.className = 'state-snapshot';
  await refresh();
}

function clearSnapshot() {
  state.asOfSha = null;
  state.aliveSet = null;
  state.page = 0;
  snapshotState.textContent = 'live';
  snapshotState.className = 'state-live';
  refresh();
}

async function refresh() {
  state.branch = branchInput.value.trim() || null;
  state.community = communitySelect.value || null;
  await Promise.all([loadCommunities(), loadMemories(), loadGraph()]);
}

repoSelect.addEventListener('change', () => {
  state.repoId = repoSelect.value;
  state.page = 0;
  refresh();
});
refreshBtn.addEventListener('click', () => {
  state.page = 0;
  refresh();
});
communitySelect.addEventListener('change', () => {
  state.community = communitySelect.value;
  refresh();
});
communityToggle.addEventListener('change', () => {
  state.colorByCommunity = communityToggle.checked;
  recolorNodes();
});
fitBtn.addEventListener('click', () => fitView());
resetBtn.addEventListener('click', () => {
  if (zoomBehavior) svg.transition().duration(300).call(zoomBehavior.transform, d3.zoomIdentity);
});
snapshotBtn.addEventListener('click', () => applySnapshot());
snapshotClearBtn.addEventListener('click', () => clearSnapshot());
prevBtn.addEventListener('click', () => {
  state.page = Math.max(0, state.page - 1);
  loadMemories();
});
nextBtn.addEventListener('click', () => {
  if ((state.page + 1) * state.pageSize < state.total) {
    state.page += 1;
    loadMemories();
  }
});

(async function init() {
  try {
    await loadRepos();
    await refresh();
  } catch (e) {
    document.body.innerHTML += `<pre style="color:#f76c7c">init failed: ${e}</pre>`;
  }
})();
