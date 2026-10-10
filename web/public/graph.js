(() => {
  'use strict';
  const COL = {sound: '#3CB371', guessed: '#E08A1E', unknown: '#8A8578'};
  const GL = {sound: '●', guessed: '◐', unknown: '○'};
  const input = document.getElementById('in'), status = document.getElementById('status');
  const select = document.getElementById('node-select'), detailBox = document.getElementById('detail');
  const diagram = document.getElementById('g'), integrity = document.getElementById('integrity');
  const controls = [...document.querySelectorAll('[data-view]')];
  let simulation, zoomControl, loadEpoch = 0, hasRendered = false, previous = false, diagramNote = '';

  function announce(message) {
    status.textContent = `${previous ? 'Previous graph (not current input). ' : ''}${message}${diagramNote ? ` ${diagramNote}` : ''}`;
  }
  function invalid(message) {
    document.getElementById('input-error').textContent = message;
    previous = hasRendered;
    announce(hasRendered ? 'Input rejected. The previous graph remains visible.' : 'Input rejected. No graph has been rendered.');
    input.setAttribute('aria-invalid', 'true');
    input.focus();
  }
  function view(action) {
    if (!zoomControl || typeof d3 === 'undefined') return;
    const svg = d3.select(diagram);
    if (action === 'reset') svg.call(zoomControl.transform, d3.zoomIdentity);
    else if (action === 'in' || action === 'out') svg.call(zoomControl.scaleBy, action === 'in' ? 1.3 : 1 / 1.3);
    else svg.call(zoomControl.translateBy, action === 'left' ? 60 : action === 'right' ? -60 : 0,
      action === 'up' ? 60 : action === 'down' ? -60 : 0);
  }
  function clearDiagram() {
    if (simulation) simulation.stop();
    simulation = null; zoomControl = null;
    if (typeof d3 !== 'undefined') d3.select(diagram).on('.zoom', null);
    diagram.replaceChildren();
    controls.forEach(button => { button.disabled = true; });
  }
  function render() {
    let payload;
    try { payload = ViewerInput.parse(input.value, 'graph'); }
    catch (failure) { invalid(failure.message); return; }
    input.removeAttribute('aria-invalid');
    document.getElementById('input-error').textContent = '';
    clearDiagram();
    hasRendered = true; previous = false; diagramNote = '';
    const evidence = payload.graph_evidence || {}, payloadClaimsComplete = evidence.approvable === true;
    // No pasted payload can establish live repository/worktree provenance.
    const approvable = false;
    integrity.textContent = `CANDIDATE GRAPH ONLY · this static viewer cannot verify pasted graph provenance${
      payloadClaimsComplete ? ' (the payload claims complete evidence)' : ''
    }. Run amu map and amu plan --approve in the repository. ${
      (evidence.reasons || []).concat(payload.warnings || []).join('; ')
    }`;
    const nodes = payload.nodes.map(raw => {
      const node = {...raw};
      if (!approvable && node.confidence === 'sound') {
        node.confidence = 'unknown'; node.reason = `candidate only: ${node.reason || 'provider relation'}`;
      }
      return node;
    });
    const edges = payload.edges, index = new Map(nodes.map(node => [node.id, node]));
    const adjacency = new Map(nodes.map(node => [node.id, []]));
    for (const edge of edges) {
      adjacency.get(edge.from).push(edge);
      if (edge.from !== edge.to) adjacency.get(edge.to).push(edge);
    }
    function detail(node) {
      if (!node) return;
      select.value = node.id;
      const related = adjacency.get(node.id).map(edge => `${index.get(edge.from).name || edge.from} → ${edge.relation || 'relation unspecified'} → ${index.get(edge.to).name || edge.to}`);
      detailBox.textContent = `${node.name || node.id}\n${node.kind || ''} ${node.path || ''}\nConfidence: ${node.confidence || 'unknown'}\nReason: ${node.reason || 'Not provided'}\nVerify: ${node.verify || 'Not provided'}\nRelationships:\n${related.join('\n') || 'No relationships in this payload.'}`;
      announce(`Selected ${node.name || node.id}. ${related.length} relationships. Details follow the legend.`);
    }
    select.replaceChildren(); select.disabled = !nodes.length;
    for (const node of nodes) {
      const option = document.createElement('option');
      option.value = node.id; option.textContent = `${node.name || node.id} — ${node.path || node.id}`;
      select.append(option);
    }
    select.onchange = () => detail(index.get(select.value));
    detailBox.style.whiteSpace = 'pre-wrap';
    if (!nodes.length) {
      detailBox.textContent = 'No nodes in this payload.';
      announce('Rendered an empty graph.');
      return;
    }
    const limits = ViewerInput.LIMITS;
    if (nodes.length > limits.diagramNodes || edges.length > limits.diagramEdges) {
      diagramNote = `Diagram limited to 250 nodes and 1,500 edges. This ${nodes.length}-node, ${edges.length}-edge payload is available in full through Inspect node; no partial diagram is shown.`;
      detail(nodes[0]); return;
    }
    if (typeof d3 === 'undefined') {
      diagramNote = 'Diagram unavailable. Use Inspect node for all details and relationships.';
      detail(nodes[0]); return;
    }
    detail(nodes[0]);
    const links = edges.map(edge => ({source: edge.from, target: edge.to, relation: edge.relation,
      guessed: ['co_changes', 'siblings'].includes(edge.section), incomplete: !approvable}));
    const svg = d3.select(diagram), width = diagram.clientWidth, height = diagram.clientHeight;
    svg.append('defs').append('marker').attr('id', 'arrow').attr('viewBox', '0 -5 10 10')
      .attr('refX', 22).attr('refY', 0).attr('markerWidth', 6).attr('markerHeight', 6).attr('orient', 'auto')
      .append('path').attr('d', 'M0,-5L10,0L0,5').attr('fill', '#E08A1E');
    const group = svg.append('g');
    zoomControl = d3.zoom().on('zoom', event => group.attr('transform', event.transform));
    svg.call(zoomControl).call(zoomControl.transform, d3.zoomIdentity);
    controls.forEach(button => { button.disabled = false; });
    const link = group.append('g').selectAll('path').data(links).join('path')
      .attr('class', data => `edge${data.guessed ? ' guessed' : ''}${data.incomplete ? ' incomplete' : ''}`)
      .attr('marker-end', 'url(#arrow)');
    const labels = group.append('g').selectAll('text').data(links).join('text').attr('class', 'elabel').text(data => data.relation);
    const node = group.append('g').selectAll('g').data(nodes).join('g').attr('class', 'node')
      .attr('tabindex', 0).attr('role', 'button')
      .attr('aria-label', data => `${data.name || data.id}: inspect with Enter; move with arrow keys`)
      .call(d3.drag().on('drag', (event, data) => { data.x = event.x; data.y = event.y; draw(); }));
    node.append('circle').attr('r', data => data.id === payload.focus ? 14 : 9)
      .attr('fill', data => COL[data.confidence] || COL.unknown)
      .attr('stroke', data => data.id === payload.focus ? '#F5B32E' : 'none').attr('stroke-width', 3);
    node.append('text').attr('dx', 14).attr('dy', 4).text(data => `${GL[data.confidence] || GL.unknown} ${data.name || data.id}`);
    node.on('click', (event, data) => detail(data));
    node.on('keydown', (event, data) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); detail(data); }
      else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
        event.preventDefault();
        data.x += event.key === 'ArrowLeft' ? -20 : event.key === 'ArrowRight' ? 20 : 0;
        data.y += event.key === 'ArrowUp' ? -20 : event.key === 'ArrowDown' ? 20 : 0;
        draw();
      }
    });
    function draw() {
      link.attr('d', data => `M${data.source.x},${data.source.y}L${data.target.x},${data.target.y}`);
      labels.attr('x', data => (data.source.x + data.target.x) / 2).attr('y', data => (data.source.y + data.target.y) / 2);
      node.attr('transform', data => `translate(${data.x},${data.y})`);
    }
    simulation = d3.forceSimulation(nodes).force('link', d3.forceLink(links).id(data => data.id).distance(110))
      .force('charge', d3.forceManyBody().strength(-320)).force('center', d3.forceCenter(width / 2, height / 2))
      .force('y', d3.forceY(data => data.section === 'callers' ? height * .25 : data.section === 'callees' ? height * .75 : height / 2).strength(.25));
    simulation.stop(); simulation.tick(150); draw();
  }
  document.getElementById('render').addEventListener('click', () => { loadEpoch++; render(); });
  controls.forEach(button => button.addEventListener('click', () => view(button.dataset.view)));
  input.addEventListener('input', () => {
    loadEpoch++; previous = hasRendered;
    announce(hasRendered ? 'Input changed. The previous graph remains visible until Render succeeds.' : 'Input changed. Render to inspect it.');
  });
  document.getElementById('file').addEventListener('change', async event => {
    const epoch = ++loadEpoch, file = event.target.files[0];
    if (!file) return;
    if (file.size > ViewerInput.LIMITS.bytes) { invalid('File exceeds 4 MiB. Choose a smaller graph slice.'); return; }
    previous = hasRendered;
    announce('Reading local JSON file. Nothing is uploaded.');
    try {
      const text = await file.text();
      if (epoch !== loadEpoch) return;
      input.value = text; render();
    } catch {
      if (epoch === loadEpoch) invalid('Could not read file. Paste the JSON or choose another file.');
    }
  });
})();
