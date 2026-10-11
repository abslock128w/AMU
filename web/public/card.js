(() => {
  'use strict';

  const input = document.getElementById('in');
  const output = document.getElementById('out');
  const raw = document.getElementById('raw');
  const approve = document.getElementById('approve');
  const renderButton = document.getElementById('render');
  const viewJsonButton = document.getElementById('view-json');
  const status = document.getElementById('status');
  const error = document.getElementById('input-error');
  let hasRendered = false, previous = false;
  function invalid(message) {
    error.textContent = message;
    previous = hasRendered;
    status.textContent = hasRendered
      ? 'Input rejected. The previous map remains visible; it does not represent the current input.'
      : 'Input rejected. No map has been rendered.';
    input.setAttribute('aria-invalid', 'true');
    input.focus();
  }

  function isRecord(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function toText(value, fallback = '') {
    return String(value ?? fallback);
  }

  function createElement(name, value) {
    const element = document.createElement(name);
    if (arguments.length > 1) {
      element.textContent = toText(value);
    }
    return element;
  }

  function createBadge(value, modifier) {
    const badge = createElement('span', value);
    badge.classList.add('badge');
    if (modifier) {
      badge.classList.add(modifier);
    }
    return badge;
  }

  function confidenceClass(value) {
    return value === 'sound' || value === 'guessed' || value === 'unknown'
      ? value
      : 'unknown';
  }

  function appendTile(container, label, value) {
    const tile = createElement('div');
    tile.className = 'tile';
    const labelElement = createElement('span', label);
    labelElement.className = 'dim';
    tile.append(labelElement, createElement('b', value));
    container.append(tile);
  }

  function appendNodeRow(table, rawNode) {
    const node = isRecord(rawNode) ? rawNode : {};
    const row = createElement('tr');

    row.append(createElement('td', node.id));

    const nodeCell = createElement('td');
    nodeCell.append(document.createTextNode(toText(node.path)));
    if (node.symbol) {
      nodeCell.append(document.createTextNode(`#${toText(node.symbol)}`));
    }
    nodeCell.append(createElement('br'));
    const feature = createElement('span', node.feature);
    feature.className = 'dim';
    nodeCell.append(feature);
    row.append(nodeCell);

    const badges = createElement('td');
    const confidence = node.confidence === 'sound' ? 'claimed sound' : node.confidence;
    badges.append(
      createBadge(confidence, confidenceClass(node.confidence)),
      createBadge(node.risk),
    );
    row.append(badges);

    row.append(createElement('td', node.reason));
    const verify = createElement('code', node.verify);
    const verifyCell = createElement('td');
    verifyCell.append(verify);
    row.append(verifyCell);

    const answerCell = createElement('td');
    if (node.confidence !== 'sound') {
      const answer = createElement('button', `Answer ${toText(node.id)}`);
      answer.type = 'button';
      answer.addEventListener('click', () => {
        status.textContent = `${previous ? 'Previous map (not current input). ' : ''}Answer ${toText(node.id)} in the repository: run\n${toText(node.verify)}`;
      });
      answerCell.append(answer);
    }
    row.append(answerCell);
    table.append(row);
  }

  function render() {
    let map;
    try {
      map = ViewerInput.parse(input.value, 'card');
    } catch (failure) {
      invalid(failure.message);
      return;
    }

    if (!isRecord(map)) {
      invalid('Map must be a JSON object. Paste amu map --json output.');
      return;
    }

    const resolution = isRecord(map.resolution) ? map.resolution : {};
    error.textContent = '';
    input.removeAttribute('aria-invalid');
    const nodes = Array.isArray(map.nodes) ? map.nodes : [];
    const ripples = Array.isArray(map.ripples) ? map.ripples : [];
    const graphEvidence = isRecord(map.graph_evidence) ? map.graph_evidence : {};
    const payloadClaimsComplete = graphEvidence.approvable === true;

    raw.textContent = input.value;
    // Pasted JSON has no authenticated repository/worktree provenance. This
    // static viewer can explain a map, but only the repository CLI can approve
    // it after its live lifecycle checks.
    approve.disabled = true;
    approve.title = 'Presentation only: run amu plan --approve in the repository.';

    const fragment = document.createDocumentFragment();
    const tiles = createElement('div');
    tiles.className = 'tiles';
    appendTile(tiles, 'claimed sound', resolution.sound ?? 0);
    appendTile(tiles, 'payload guessed', resolution.guessed ?? 0);
    appendTile(tiles, 'payload unknown', resolution.unknown ?? 0);
    appendTile(tiles, 'ripples', ripples.length);
    fragment.append(tiles);

    const qualifier = createElement('p', resolution.qualifier || '');
    qualifier.className = 'dim';
    fragment.append(qualifier);

    const evidenceReasons = Array.isArray(graphEvidence.reasons) ? graphEvidence.reasons : [];
    const integrity = createElement(
      'p',
      `Candidate map only: this static page cannot verify pasted provenance${
        payloadClaimsComplete ? ' (the payload claims complete graph evidence)' : ''
      }. Run amu map and amu plan --approve in the repository. ${
        evidenceReasons.join('; ') || ''
      }`,
    );
    integrity.className = 'blocked';
    fragment.append(integrity);

    const table = createElement('table');
    table.append(createElement('caption', 'Candidate map nodes'));
    const header = createElement('tr');
    for (const label of ['id', 'node', 'badges', 'reason', 'verify', 'actions']) {
      const heading = createElement('th', label);
      heading.scope = 'col';
      header.append(heading);
    }
    table.append(header);
    for (const node of nodes) {
      appendNodeRow(table, node);
    }
    const scroll = createElement('div');
    scroll.className = 'table-scroll';
    scroll.tabIndex = 0;
    scroll.setAttribute('role', 'region');
    scroll.setAttribute('aria-label', 'Candidate map nodes');
    scroll.append(table);
    fragment.append(scroll);

    const next = createElement('p');
    next.append(
      createElement('b', 'payload suggestion (unverified)'),
      document.createTextNode(' '),
      createElement('code', map.next || ''),
    );
    fragment.append(next);

    output.replaceChildren(fragment);
    hasRendered = true; previous = false;
    status.textContent = `Rendered ${nodes.length} candidate nodes. Approval remains in the repository CLI.`;
  }

  input.addEventListener('input', () => {
    previous = hasRendered;
    if (hasRendered) status.textContent = 'Input changed. The previous map remains visible until Render succeeds.';
  });
  renderButton.addEventListener('click', render);
  viewJsonButton.addEventListener('click', () => {
    raw.hidden = !raw.hidden;
    viewJsonButton.setAttribute('aria-expanded', String(!raw.hidden));
  });
})();
