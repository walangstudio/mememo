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
  aliveSet: null, // null = live; Set<string> = filtered to those ids
};

const $ = (id) => document.getElementById(id);

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

function rowMeetsFilter(row) {
  if (!state.aliveSet) return true;
  return state.aliveSet.has(row.id);
}

async function loadMemories() {
  const data = await fetchJson(
    '/memories' +
      qs({
        repo_id: state.repoId,
        branch: state.branch,
        limit: state.pageSize,
        offset: state.page * state.pageSize,
      })
  );
  state.total = data.total;
  const visible = data.items.filter(rowMeetsFilter);
  tbody.innerHTML = '';
  visible.forEach((row) => {
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
  pageInfo.textContent = `${state.page * state.pageSize + 1}–${
    state.page * state.pageSize + visible.length
  } of ${data.total}${state.aliveSet ? ` (filtered to ${state.aliveSet.size} alive)` : ''}`;
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
  const links = [];
  edges.forEach((e) => {
    if (!e.target_memory_id) return; // skip unresolved
    nodeIds.add(e.source_memory_id);
    nodeIds.add(e.target_memory_id);
    links.push({
      source: e.source_memory_id,
      target: e.target_memory_id,
      type: e.type,
      community: e.community,
    });
  });
  const nodes = Array.from(nodeIds).map((id) => ({
    id,
    faded: state.aliveSet && !state.aliveSet.has(id),
  }));

  svg.selectAll('*').remove();
  const w = svg.node().clientWidth;
  const h = svg.node().clientHeight;

  const palette = d3.schemeTableau10;
  const colorFor = (c) =>
    c === null || c === undefined ? '#5a667a' : palette[c % palette.length];

  const sim = d3
    .forceSimulation(nodes)
    .force('link', d3.forceLink(links).id((d) => d.id).distance(40))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(w / 2, h / 2));

  const link = svg
    .append('g')
    .attr('stroke', '#5a667a')
    .selectAll('line')
    .data(links)
    .join('line')
    .attr('class', (d) =>
      'link ' + (state.aliveSet && (!state.aliveSet.has(d.source.id || d.source) ||
                                    !state.aliveSet.has(d.target.id || d.target))
        ? 'faded'
        : '')
    );

  const node = svg
    .append('g')
    .selectAll('circle')
    .data(nodes)
    .join('circle')
    .attr('class', (d) => 'node' + (d.faded ? ' faded' : ''))
    .attr('r', 5)
    .attr('fill', (d) => {
      const e = links.find((l) =>
        (l.source.id || l.source) === d.id || (l.target.id || l.target) === d.id
      );
      return colorFor(e ? e.community : null);
    })
    .call(
      d3
        .drag()
        .on('start', (event, d) => {
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

  node.append('title').text((d) => d.id);

  sim.on('tick', () => {
    link
      .attr('x1', (d) => d.source.x)
      .attr('y1', (d) => d.source.y)
      .attr('x2', (d) => d.target.x)
      .attr('y2', (d) => d.target.y);
    node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
  });

  const communities = Array.from(new Set(edges.map((e) => e.community))).sort();
  legend.innerHTML = communities
    .map(
      (c) =>
        `<span style="color:${colorFor(c)}">●</span> ${
          c === null ? 'unclustered' : 'c' + c
        }`
    )
    .join(' ');
}

async function applySnapshot() {
  const sha = shaInput.value.trim();
  if (!sha) return;
  const data = await fetchJson(
    `/snapshots/${encodeURIComponent(sha)}` +
      qs({ repo_id: state.repoId, branch: state.branch })
  );
  state.aliveSet = new Set(data.alive_memory_ids);
  snapshotState.textContent = `snapshot ${sha} (${state.aliveSet.size} alive)`;
  snapshotState.className = 'state-snapshot';
  await refresh();
}

function clearSnapshot() {
  state.aliveSet = null;
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
