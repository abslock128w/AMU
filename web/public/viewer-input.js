/* Shared, local-only input boundary. Also export pure logic for Node tests. */
(function (root, factory) {
  'use strict';
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.ViewerInput = api;
})(typeof globalThis === 'object' ? globalThis : this, function () {
  'use strict';
  const LIMITS = Object.freeze({bytes: 4 * 1024 * 1024, depth: 64, values: 100000,
    nodes: 1000, edges: 5000, diagramNodes: 250, diagramEdges: 1500});
  function fail(message) { throw new Error(message); }
  function record(value) { return value !== null && typeof value === 'object' && !Array.isArray(value); }

  // Count UTF-8 bytes without allocating a second, potentially huge buffer.
  function checkText(text) {
    if (typeof text !== 'string') fail('Input must be JSON text.');
    if (text.length > LIMITS.bytes) fail('Input exceeds 4 MiB. Export a smaller graph slice.');
    let bytes = 0, depth = 0, quoted = false, escaped = false;
    for (let i = 0; i < text.length; i++) {
      const code = text.charCodeAt(i), char = text[i];
      if (code < 0x80) bytes++;
      else if (code < 0x800) bytes += 2;
      else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length &&
               text.charCodeAt(i + 1) >= 0xdc00 && text.charCodeAt(i + 1) <= 0xdfff) {
        bytes += 4; i++;
      } else bytes += 3;
      if (bytes > LIMITS.bytes) fail('Input exceeds 4 MiB. Export a smaller graph slice.');
      if (quoted) {
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') quoted = false;
      } else if (char === '"') quoted = true;
      else if (char === '{' || char === '[') {
        if (++depth > LIMITS.depth) fail('JSON nesting exceeds 64 levels. Export a shallower payload.');
      } else if (char === '}' || char === ']') depth--;
    }
    return bytes;
  }

  function checkValues(value) {
    // Iterator frames bound auxiliary memory by depth rather than array width.
    const frames = [{values: [value], index: 0}];
    let count = 0;
    while (frames.length) {
      const frame = frames[frames.length - 1];
      if (frame.index === frame.values.length) { frames.pop(); continue; }
      const item = frame.values[frame.index++];
      if (++count > LIMITS.values) fail('JSON contains too many values. Export a smaller graph slice.');
      if (typeof item === 'number' && !Number.isFinite(item)) fail('All numeric values must be finite.');
      if (Array.isArray(item)) frames.push({values: item, index: 0});
      else if (record(item)) frames.push({values: Object.values(item), index: 0});
    }
  }
  function textField(object, key, where) {
    if (object[key] != null && typeof object[key] !== 'string') fail(`${where}.${key} must be text or null.`);
  }
  function textArray(object, key, where) {
    if (object[key] === undefined) return;
    if (!Array.isArray(object[key]) || object[key].some(item => typeof item !== 'string'))
      fail(`${where}.${key} must be an array of text.`);
  }
  function validate(payload, kind) {
    checkValues(payload);
    if (!record(payload)) fail('Payload must be a JSON object. Paste the repository command output.');
    if (Object.prototype.hasOwnProperty.call(payload, 'error'))
      fail('Error payloads cannot be rendered. Resolve the repository error and export again.');
    if (!Array.isArray(payload.nodes)) fail('nodes must be an array, including [] for an empty result.');
    if (payload.nodes.length > LIMITS.nodes) fail('Graph exceeds 1,000 nodes. Export a smaller graph slice.');
    const ids = new Set();
    for (let i = 0; i < payload.nodes.length; i++) {
      const node = payload.nodes[i], where = `nodes[${i}]`;
      if (!record(node)) fail(`${where} must be an object.`);
      if (typeof node.id !== 'string' || !node.id.trim()) fail(`${where}.id must be non-empty text.`);
      if (ids.has(node.id)) fail(`Duplicate node id at ${where}. Each node needs a unique id.`);
      ids.add(node.id);
      for (const key of ['name', 'path', 'symbol', 'kind', 'confidence', 'reason', 'verify', 'feature', 'section', 'risk'])
        textField(node, key, where);
    }
    if (kind === 'graph' || payload.edges !== undefined) {
      if (!Array.isArray(payload.edges)) fail('edges must be an array, including [] for an empty result.');
      if (payload.edges.length > LIMITS.edges) fail('Graph exceeds 5,000 edges. Export a smaller graph slice.');
      for (let i = 0; i < payload.edges.length; i++) {
        const edge = payload.edges[i], where = `edges[${i}]`;
        if (!record(edge)) fail(`${where} must be an object.`);
        if (!ids.has(edge.from) || !ids.has(edge.to)) fail(`${where} endpoints must reference existing node ids.`);
        textField(edge, 'relation', where);
        textField(edge, 'section', where);
      }
    }
    if (payload.ripples !== undefined) {
      if (!Array.isArray(payload.ripples)) fail('ripples must be an array.');
      if (payload.ripples.length > LIMITS.edges) fail('Map exceeds 5,000 ripples. Export a smaller map.');
      if (payload.ripples.some(item => !record(item))) fail('Each ripple must be an object.');
    }
    if (payload.resolution !== undefined) {
      if (!record(payload.resolution)) fail('resolution must be an object.');
      for (const key of ['sound', 'guessed', 'unknown']) {
        const n = payload.resolution[key];
        if (n != null && (!Number.isInteger(n) || n < 0)) fail(`resolution.${key} must be a non-negative integer.`);
      }
      textField(payload.resolution, 'qualifier', 'resolution');
    }
    if (payload.graph_evidence !== undefined) {
      if (!record(payload.graph_evidence)) fail('graph_evidence must be an object.');
      textArray(payload.graph_evidence, 'reasons', 'graph_evidence');
      if (payload.graph_evidence.approvable !== undefined && typeof payload.graph_evidence.approvable !== 'boolean')
        fail('graph_evidence.approvable must be a boolean.');
    }
    textArray(payload, 'warnings', 'payload');
    textField(payload, 'next', 'payload');
    textField(payload, 'focus', 'payload');
    return payload;
  }
  function parse(text, kind) {
    checkText(text);
    let payload;
    try { payload = JSON.parse(text); }
    catch { fail('Invalid JSON. Correct the payload and render again.'); }
    return validate(payload, kind);
  }
  return Object.freeze({LIMITS, checkText, parse, validate});
});
