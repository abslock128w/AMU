const {test, expect} = require('@playwright/test');
const boundary = require('../public/viewer-input.js');
const good = {focus: 'a', nodes: [{id: 'a', name: 'Alpha', path: 'src/Alpha.py'}, {id: 'b', name: 'Beta', path: 'src/Beta.py'}],
  edges: [{from: 'a', to: 'b', relation: 'CALLS'}]};

async function render(page, payload, viewer = 'graph') {
  await page.getByLabel(viewer === 'card' ? 'Map JSON' : 'Graph JSON', {exact: true})
    .fill(typeof payload === 'string' ? payload : JSON.stringify(payload));
  await page.getByRole('button', {name: 'Render', exact: true}).click();
}

test('pure boundary accepts command payload variants and rejects malformed input', () => {
  expect(boundary.parse('{"root":null,"nodes":[],"ripples":[],"resolution":{}}', 'card').nodes).toEqual([]);
  expect(boundary.parse('{"focus":null,"nodes":[],"edges":[]}', 'graph').edges).toEqual([]);
  expect(() => boundary.parse('{"nodes":[],"edges":[],"extra":1e999}', 'graph')).toThrow(/finite/);
  expect(() => boundary.parse(' '.repeat(boundary.LIMITS.bytes + 1), 'graph')).toThrow(/4 MiB/);
  expect(() => boundary.parse('{"nodes":[],"edges":[],"extra":' + '['.repeat(65) + '0' + ']'.repeat(65) + '}', 'graph')).toThrow(/nesting/);
});

for (const viewer of ['card', 'graph']) {
  test(`${viewer}: rejects malformed arrays, duplicate IDs and invalid endpoints without replacing previous data`, async ({page}) => {
    await page.goto(`/${viewer}.html`);
    await render(page, good, viewer);
    for (const [payload, message] of [
      [{nodes: null, edges: []}, 'nodes must be an array'],
      [{nodes: [null], edges: []}, 'must be an object'],
      [{nodes: [{id: ' '}], edges: []}, 'non-empty'],
      [{nodes: [{id: 'a'}, {id: 'a'}], edges: []}, 'Duplicate'],
      [{nodes: [{id: 'a'}], edges: [{from: 'a', to: 'missing'}]}, 'endpoints'],
      [{nodes: [], edges: [], error: 'provider failure'}, 'Error payloads'],
    ]) {
      await render(page, payload, viewer);
      await expect(page.locator('#input-error')).toContainText(message);
      await expect(page.locator('#in')).toHaveAttribute('aria-invalid', 'true');
      await expect(page.getByRole('status')).toContainText('previous');
      await expect(page.locator(viewer === 'card' ? '#out' : '#detail')).toContainText('Alpha');
      await expect(page.locator('#in')).toHaveValue(JSON.stringify(payload));
    }
    if (viewer === 'graph') {
      await page.getByLabel('Inspect node', {exact: true}).selectOption('b');
      await expect(page.getByRole('status')).toContainText('Previous graph (not current input)');
    } else {
      await expect(page.locator('#approve')).toBeDisabled();
      await page.getByRole('button', {name: 'Answer b'}).click();
      await expect(page.getByRole('status')).toContainText('Previous map (not current input)');
    }
  });

  test(`${viewer}: bounded numeric, depth, byte, node and edge failures recover to a genuine empty payload`, async ({page}) => {
    await page.goto(`/${viewer}.html`);
    for (const [payload, message] of [
      ['{"nodes":[],"edges":[],"metadata":1e999}', 'finite'],
      ['{"nodes":[],"edges":[],"metadata":' + '['.repeat(65) + '0' + ']'.repeat(65) + '}', 'nesting'],
      [' '.repeat(boundary.LIMITS.bytes + 1), '4 MiB'],
      [{nodes: Array.from({length: 1001}, (_, i) => ({id: String(i)})), edges: []}, '1,000 nodes'],
      [{nodes: [{id: 'a'}], edges: Array.from({length: 5001}, () => ({from: 'a', to: 'a'}))}, '5,000 edges'],
    ]) {
      await render(page, payload, viewer);
      await expect(page.locator('#input-error')).toContainText(message);
      await expect(page.locator('#in')).toHaveAttribute('aria-invalid', 'true');
    }
    await render(page, {nodes: [], edges: []}, viewer);
    await expect(page.locator('#input-error')).toBeEmpty();
    await expect(page.locator('#in')).not.toHaveAttribute('aria-invalid', 'true');
    await expect(page.getByRole('status')).toContainText(viewer === 'card' ? '0 candidate nodes' : 'empty graph');
  });

  test(`${viewer}: hostile text stays text, no upload occurs, and pasted evidence never grants approval`, async ({page}) => {
    const external = [], errors = [];
    page.on('request', request => { if (!request.url().startsWith('http://127.0.0.1:8765/')) external.push(request.url()); });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(`/${viewer}.html`);
    const hostile = '<img src="https://invalid.example/x" onerror="window.__injected=true">';
    await render(page, {...good, nodes: good.nodes.map(node => ({...node, name: hostile, path: hostile,
      reason: hostile, confidence: 'sound'})), graph_evidence: {approvable: true, reasons: [hostile]}}, viewer);
    await expect(page.locator(viewer === 'card' ? '#out' : '#detail')).toContainText(hostile);
    expect(await page.evaluate(() => window.__injected)).toBeUndefined();
    await expect(page.locator('#out img, #detail img, #integrity img')).toHaveCount(0);
    if (viewer === 'card') await expect(page.locator('#approve')).toBeDisabled();
    else await expect(page.locator('#detail')).toContainText('Confidence: unknown');
    expect(external).toEqual([]);
    expect(errors).toEqual([]);
  });
}

test('graph: checks file bytes before reading and retains the previous input', async ({page}) => {
  await page.goto('/graph.html');
  await render(page, good);
  await page.evaluate(() => {
    window.__fileReadCount = 0;
    File.prototype.text = function () { window.__fileReadCount++; return Promise.resolve('bad'); };
  });
  await page.getByLabel('Load graph JSON file', {exact: true}).setInputFiles({name: 'too-large.json',
    mimeType: 'application/json', buffer: Buffer.alloc(boundary.LIMITS.bytes + 1, 32)});
  await expect(page.locator('#input-error')).toContainText('File exceeds 4 MiB');
  expect(await page.evaluate(() => window.__fileReadCount)).toBe(0);
  await expect(page.locator('#in')).toHaveValue(JSON.stringify(good));
  await expect(page.getByRole('status')).toContainText('previous');
});

test('graph: file read failures and malformed files remain recoverable', async ({page}) => {
  await page.goto('/graph.html');
  await render(page, good);
  await page.evaluate(() => { File.prototype.text = () => Promise.reject(new Error('disk failure')); });
  await page.getByLabel('Load graph JSON file', {exact: true}).setInputFiles({name: 'failed.json', mimeType: 'application/json', buffer: Buffer.from('{}')});
  await expect(page.locator('#input-error')).toContainText('Could not read file');
  await expect(page.locator('#in')).toHaveValue(JSON.stringify(good));
  await page.evaluate(() => { File.prototype.text = () => Promise.resolve('malformed file contents'); });
  await page.getByLabel('Load graph JSON file', {exact: true}).setInputFiles({name: 'bad.json', mimeType: 'application/json', buffer: Buffer.from('bad')});
  await expect(page.locator('#input-error')).toContainText('Invalid JSON');
  await expect(page.locator('#in')).toHaveValue('malformed file contents');
  await render(page, good);
  await expect(page.locator('#input-error')).toBeEmpty();
});

async function deferredFiles(page) {
  await page.evaluate(() => {
    window.__pendingFiles = [];
    File.prototype.text = function () {
      return new Promise(resolve => window.__pendingFiles.push({name: this.name, resolve}));
    };
  });
}
async function loadDeferred(page, name) {
  await page.getByLabel('Load graph JSON file', {exact: true}).setInputFiles({name, mimeType: 'application/json', buffer: Buffer.from('{}')});
}
async function finishDeferred(page, name, payload) {
  await page.evaluate(({name, payload}) => window.__pendingFiles.find(file => file.name === name).resolve(JSON.stringify(payload)), {name, payload});
}

test('graph: an older file promise cannot overwrite a newer file', async ({page}) => {
  await page.goto('/graph.html');
  await deferredFiles(page);
  await loadDeferred(page, 'old.json');
  await loadDeferred(page, 'new.json');
  const newer = {...good, nodes: [{id: 'a', name: 'Newer'}], edges: []};
  await finishDeferred(page, 'new.json', newer);
  await expect(page.locator('#detail')).toContainText('Newer');
  await finishDeferred(page, 'old.json', good);
  await expect(page.locator('#in')).toHaveValue(JSON.stringify(newer));
  await expect(page.locator('#detail')).toContainText('Newer');
});

test('graph: manual edits and renders cancel pending file replacement', async ({page}) => {
  await page.goto('/graph.html');
  await deferredFiles(page);
  await loadDeferred(page, 'edit-race.json');
  await page.locator('#in').fill('manual input, not rendered');
  await finishDeferred(page, 'edit-race.json', good);
  await expect(page.locator('#in')).toHaveValue('manual input, not rendered');
  await loadDeferred(page, 'render-race.json');
  await render(page, good);
  await finishDeferred(page, 'render-race.json', {nodes: [], edges: []});
  await expect(page.locator('#in')).toHaveValue(JSON.stringify(good));
  await expect(page.locator('#detail')).toContainText('Alpha');
});

test('graph: accepted large graphs expose the entire payload without a partial or costly diagram', async ({page}) => {
  await page.goto('/graph.html');
  const large = {nodes: Array.from({length: 1000}, (_, i) => ({id: String(i), name: `Node ${i}`})),
    edges: Array.from({length: 999}, (_, i) => ({from: String(i), to: String(i + 1), relation: 'CALLS'}))};
  await render(page, large);
  await expect(page.getByRole('status')).toContainText('Diagram limited');
  await expect(page.locator('#g .node')).toHaveCount(0);
  await expect(page.getByRole('button', {name: 'Zoom in', exact: true})).toBeDisabled();
  await expect(page.locator('#node-select option')).toHaveCount(1000);
  await page.getByLabel('Inspect node', {exact: true}).selectOption('999');
  await expect(page.locator('#detail')).toContainText('Node 998 → CALLS → Node 999');
  await expect(page.getByRole('status')).toContainText('Diagram limited');
  await render(page, {...good, edges: Array.from({length: 1501}, () => good.edges[0])});
  await expect(page.getByRole('status')).toContainText('1501-edge payload');
  await expect(page.locator('#g .node')).toHaveCount(0);
  await render(page, good);
  await expect(page.locator('#g .node')).toHaveCount(2);
  await expect(page.getByRole('button', {name: 'Zoom in', exact: true})).toBeEnabled();
});
