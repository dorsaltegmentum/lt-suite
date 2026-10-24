// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* fetch_media.js in a vm context with a stand-in DOM: rendered <img> nodes owned by panels
 * (found through ko.dataFor, as on the live page), a fake image host and a fake sink.
 * Checks the file names the exporter joins on, the token and type on every POST, the
 * explicit downloads, refusals, and the gap report (keys_not_fetched).
 */
'use strict';
const { describe, test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {createHmac, createHash, webcrypto} = require('node:crypto');
const H = require('./vm_harness.js');
const { obs, coll, mdl } = H;

const PNG = Buffer.from('89504e470d0a1a0a0000000d49484452', 'hex');
const IMG_HOST = 'https://lt.example.invalid/media/';

/* page: { panels: [{ vm, imgs: [{ src, w, h }] }], current } */
async function runMedia({ panels = [], pathname, args = {}, reply, entry = false, imageReply, helloReply, extraImgs = [] } = {}) {
  const posts = [];
  const requests = [];
  const downloads = [];
  const nodes = [];
  const owners = new Map();
  for (const p of panels) {
    const panelNode = { parentElement: null };
    owners.set(panelNode, p.vm);
    for (const im of p.imgs || []) {
      nodes.push({ src: im.src, currentSrc: im.src, complete: true, naturalWidth: im.w || 400,
                   naturalHeight: im.h || 300, parentElement: panelNode });
    }
  }
  for (const im of extraImgs) {
    nodes.push({ src: im.src, currentSrc: im.src, complete: true, naturalWidth: 10, naturalHeight: 10, parentElement: null });
  }
  const pageVm = { current: obs(true), dataKey: obs(1), allPanels: obs(panels.map(p => p.vm)) };
  const root = { lessonFlowPages: () => [pageVm] };
  const sandbox = {
    console, URL, Blob, AbortController, TextEncoder, crypto: webcrypto,
    document: {
      querySelector: sel => (sel === 'div.pageList' ? { __el: true } : null),
      querySelectorAll: sel => (sel === 'img' ? nodes : []),
      createElement: () => ({ click() { downloads.push(this.download); }, remove() {} }),
      body: { appendChild() {}, scrollHeight: 900 }
    },
    window: { scrollTo() {}, scrollY: 0 },
    ko: Object.assign({
      contextFor: () => ({ $root: root }),
      dataFor: n => owners.get(n) || null
    }, H.KO),
    location: { origin: H.ORIGIN, pathname: pathname || H.PAGE_PATH, href: H.ORIGIN + (pathname || H.PAGE_PATH) },
    fetch: async (url, o) => {
      const u = String(url);
      requests.push({url: u, headers: o?.headers || {}, body: o?.body, method: o?.method});
      if (u.includes('/hello?')) {
        if (helloReply) return helloReply(u, o);
        const n = new URL(u).searchParams.get('n');
        return H.response(200, {proof: createHmac('sha256', H.TOKEN)
          .update(`lt-suite-sink|${n}|${H.ORIGIN}`).digest('hex')});
      }
      if (u.startsWith(IMG_HOST)) {
        if (imageReply) return imageReply(u);
        return { type: 'basic', ok: true, status: 200, blob: async () => new Blob([PNG], { type: 'image/png' }) };
      }
      posts.push({ url: u, headers: (o && o.headers) || {}, credentials: o && o.credentials, body: o?.body });
      return reply ? reply(u, o) : H.response(200, { ok: true });
    },
    ...H.fastTimers()
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const all = Object.assign({ transport: 'sink', sink: H.SINK, token: H.TOKEN }, args);
  const src = H.source('fetch_media');
  let receipt;
  if (entry) {
    receipt = await vm.runInContext(`(async () => {\nconst __LT_ARGS__ = Object.freeze(${JSON.stringify(all)});\n${src}\n})()`, sandbox);
  } else {
    await vm.runInContext(`(async () => {\n${src}\n})()`, sandbox);
    receipt = await sandbox.fetchLtMedia(all);
  }
  return { receipt: JSON.parse(JSON.stringify(receipt)), posts, downloads, requests };
}

const vmPanel = (key, content, input) => ({
  pluginKey: obs('image-annotation'), panelKey: () => key,
  panelState: obs(Object.assign({ content }, input === undefined ? {} : { input }))
});

describe('fetch-media', () => {
  const author = { vm: vmPanel('21', { predefinedImageKey: '30' }), imgs: [{ src: `${IMG_HOST}30?sig=abc` }] };
  const upload = { vm: vmPanel('22', { inputMode: 'allowImageAndAnnotation' }, { imageKey: '31' }),
                   imgs: [{ src: `${IMG_HOST}31?sig=def` }] };

  test('fetch-media: author and student images get the exporter join names', async () => {
    const r = await runMedia({ panels: [author, upload] });
    assert.equal(r.receipt.ok, true);
    assert.deepEqual(r.receipt.images.map(i => [i.file, i.role, i.paired, i.result]), [
      [`lt-${H.MODULE_ID}-30.png`, 'author-supplied', 'by-key-in-url', 'saved'],
      [`lt-${H.MODULE_ID}-u31.png`, 'student-upload', 'by-key-in-url', 'saved']]);
    assert.deepEqual(r.posts.map(p => p.url), [
      `${H.SINK}/media/lt-${H.MODULE_ID}-30.png`, `${H.SINK}/media/lt-${H.MODULE_ID}-u31.png`]);
    for (const p of r.posts) {
      assert.equal(p.headers['X-Lt-Suite-Token'], undefined);
      assert.match(p.headers['X-Lt-Suite-Auth'], /^[0-9a-f]{64}$/);
      assert.equal(p.headers['Content-Type'], 'image/png');
      assert.equal(p.credentials, 'omit');
    }
    assert.deepEqual(r.receipt.summary.keys_not_fetched, []);
  });

  test('fetch-media: the receipt carries URL shapes, never the signed URL', async () => {
    const r = await runMedia({ panels: [author] });
    assert.equal(JSON.stringify(r.receipt).includes('sig=abc'), false);
    assert.deepEqual(r.receipt.url_shape[0].query_keys, ['sig']);
    assert.equal(r.receipt.url_shape[0].same_origin, true);
  });

  test('fetch-media: one key and one image pair by position when the src lacks the key', async () => {
    const p = { vm: vmPanel('23', { imageResourceKey: '40' }), imgs: [{ src: `${IMG_HOST}opaque-name` }] };
    const r = await runMedia({ panels: [p] });
    assert.deepEqual([r.receipt.images[0].paired, r.receipt.images[0].file],
      ['by-position', `lt-${H.MODULE_ID}-40.png`]);
  });

  test('fetch-media: a key matches the src only as a whole token', async () => {
    const p = { vm: vmPanel('24', { predefinedImageKey: '12' }, { imageKey: '123' }),
                imgs: [{ src: `${IMG_HOST}123?x=1` }, { src: `${IMG_HOST}12?x=1` }] };
    const r = await runMedia({ panels: [p] });
    assert.deepEqual(r.receipt.images.map(i => [i.key, i.role]), [['123', 'student-upload'], ['12', 'author-supplied']]);
    assert.deepEqual(r.receipt.images.map(i => i.file), [`lt-${H.MODULE_ID}-u123.png`, `lt-${H.MODULE_ID}-12.png`]);
  });

  test('fetch-media: several unpaired images on one panel get distinct names', async () => {
    const p = { vm: vmPanel('28', { predefinedImageKey: '60' }, { imageKey: '61' }),
                imgs: [{ src: `${IMG_HOST}a` }, { src: `${IMG_HOST}b` }] };
    const r = await runMedia({ panels: [p] });
    assert.deepEqual(r.receipt.images.map(i => i.file), [`lt-${H.MODULE_ID}-panel-28.png`, `lt-${H.MODULE_ID}-panel-28-2.png`]);
    assert.deepEqual(r.receipt.summary.keys_not_fetched.sort(), ['60', 'u61']);
  });

  test('fetch-media: a failed upload is reported even when an author image shares its key', async () => {
    const p = { vm: vmPanel('29', { predefinedImageKey: '23' }, { imageKey: '23' }), imgs: [{ src: `${IMG_HOST}23` }] };
    const r = await runMedia({ panels: [p] });
    assert.deepEqual(r.receipt.summary.keys_not_fetched, ['u23']);
  });

  test('fetch-media: an image with no owner panel is saved under a panel name and counted', async () => {
    const r = await runMedia({ panels: [], extraImgs: [{ src: `${IMG_HOST}logo` }] });
    assert.equal(r.receipt.images[0].paired, 'unpaired-no-owner-panel');
    assert.equal(r.receipt.images[0].file, `lt-${H.MODULE_ID}-panel-null.png`);
    assert.equal(r.receipt.summary.imgs_without_a_captured_key, 1);
  });

  test('fetch-media: upload network failures stay failures with no download', async () => {
    let calls = 0;
    const r = await runMedia({ panels: [author, upload], reply: () => { calls++; throw new TypeError('Failed to fetch'); } });
    assert.equal(calls, 2, 'each authenticated upload reports its failure');
    assert.deepEqual(r.downloads, []);
    assert.deepEqual(r.receipt.images.map(i => i.result), ['failed', 'failed']);
  });

  test('fetch-media: an HTTP refusal is recorded and never retried by download', async () => {
    const r = await runMedia({ panels: [author], reply: () => H.response(415, 'refused: leading bytes do not match') });
    assert.equal(r.receipt.images[0].result, 'failed');
    assert.match(r.receipt.images[0].error, /leading bytes/);
    assert.deepEqual(r.downloads, []);
  });

  test('fetch-media: download transport never touches the sink', async () => {
    const r = await runMedia({ panels: [author], args: { transport: 'download', token: '' } });
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.downloads, [`lt-${H.MODULE_ID}-30.png`]);
  });

  test('fetch-media: an opaque cross-origin response is blocked and reported', async () => {
    const r = await runMedia({ panels: [author], imageReply: () => ({ type: 'opaque', ok: false, status: 0 }) });
    assert.match(r.receipt.images[0].error, /blocked-cross-origin/);
    assert.equal(r.receipt.summary.cross_origin_blocked, 1);
    assert.deepEqual(r.receipt.summary.keys_not_fetched, ['30']);
  });

  test('fetch-media: a non-raster type is refused rather than given a guessed extension', async () => {
    const r = await runMedia({ panels: [author], imageReply: () => ({ type: 'basic', ok: true, status: 200,
      blob: async () => new Blob(['%PDF-1.4'], { type: 'application/pdf' }) }) });
    assert.match(r.receipt.images[0].error, /unsupported content type application\/pdf/);
    assert.deepEqual(r.posts, []);
  });

  test('fetch-media: subpage children and hotspot base images are counted as page keys', async () => {
    const child = mdl({ pluginKey: 'image', panelKey: '26', panelState: { content: { imageResourceKey: '50' } } });
    const sub = { vm: { pluginKey: obs('subpage'), panelKey: () => '25', panelState: obs({ content: {
      subpages: coll(mdl({ rows: coll(mdl({ cells: coll(mdl({ panels: coll(child) })) })) })) } }) }, imgs: [] };
    const hot = { vm: vmPanel('27', { hotspots: [{ hotspotType: 'CUSTOM', imageResourceKey: '51' }] }), imgs: [] };
    const r = await runMedia({ panels: [sub, hot] });
    assert.equal(r.receipt.summary.keys_on_page, 2);
    assert.deepEqual(r.receipt.summary.keys_not_fetched.sort(), ['50', '51']);
  });

  test('fetch-media: the report page and /run/complete are refused', async () => {
    for (const pathname of ['/i/inst0001/student/courses/1/runs/101/run/complete',
                            '/i/inst0001/student/courses/1/runs/101/run/report']) {
      const r = await runMedia({ panels: [author], pathname });
      assert.equal(r.receipt.ok, false, pathname);
      assert.deepEqual(r.posts, []);
    }
  });

  test('fetch-media: /run/background is a content route', async () => {
    const r = await runMedia({ panels: [author], pathname: '/i/inst0001/student/courses/1/runs/101/run/background' });
    assert.equal(r.receipt.ok, true);
    assert.equal(r.receipt.page_index, 1);
  });

  test('fetch-media: a non-loopback sink or a missing token is refused before any fetch', async () => {
    const far = await runMedia({ panels: [author], args: { sink: 'https://collector.example.invalid' } });
    assert.equal(far.receipt.ok, false);
    const noTok = await runMedia({ panels: [author], args: { token: '' } });
    assert.equal(noTok.receipt.ok, false);
    assert.deepEqual([...far.posts, ...noTok.posts], []);
  });

  test('fetch-media: an unsafe module_id is refused', async () => {
    const r = await runMedia({ panels: [author], args: { module_id: '../x' } });
    assert.equal(r.receipt.ok, false);
  });

  test('fetch-media: the rendered entry line returns the receipt', async () => {
    const r = await runMedia({ panels: [author], entry: true });
    assert.equal(r.receipt.ok, true);
    assert.equal(r.receipt.images.length, 1);
  });
});


const author = {vm: vmPanel('21', {predefinedImageKey: '30'}), imgs: [{src: `${IMG_HOST}30`}]};

for (const host of ['localhost', '[::1]']) {
  test(`media-auth: ${host} is refused before requests`, async () => {
    const r = await runMedia({panels: [author], args: {sink: `http://${host}:8731`}});
    assert.equal(r.receipt.ok, false);
    assert.deepEqual(r.requests, []);
  });
}

test('media-auth: unproven listener gets no images, content or token', async () => {
  const r = await runMedia({panels: [author], helloReply: () => H.response(200, {proof: 'impostor'})});
  assert.equal(r.receipt.fatal, 'sink-unauthenticated');
  assert.equal(r.requests.length, 1);
  assert.equal(r.requests[0].method, 'GET');
  assert.equal(JSON.stringify(r.requests).includes(H.TOKEN), false);
  assert.deepEqual(r.posts, []);
  assert.deepEqual(r.downloads, []);
});

test('media-auth: a well-formed proof for another origin or secret fails closed', async () => {
  // Each proof has the right shape, so only the HMAC check can reject it.
  for (const [secret, origin] of [[H.TOKEN, 'https://other.example.invalid'], [H.TOKEN, ''],
                                  ['another-secret', H.ORIGIN]]) {
    const r = await runMedia({panels: [author], helloReply: u => {
      const nonce = new URL(u).searchParams.get('n');
      return H.response(200, {proof: createHmac('sha256', secret)
        .update(`lt-suite-sink|${nonce}|${origin}`).digest('hex')});
    }});
    assert.equal(r.receipt.fatal, 'sink-unauthenticated');
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.downloads, []);
  }
});

test('media-auth: upload proof authenticates the exact image bytes and destination', async () => {
  const r = await runMedia({panels: [author]});
  const digest = createHash('sha256').update(PNG).digest('hex');
  const expected = createHmac('sha256', H.TOKEN)
    .update(`media/lt-${H.MODULE_ID}-30.png|${digest}`).digest('hex');
  assert.equal(r.posts[0].headers['X-Lt-Suite-Auth'], expected);
  assert.equal(JSON.stringify(r.requests).includes(H.TOKEN), false);
});

test('fetch-media: image HTTP errors never save error bodies as images', async () => {
  for (const status of [404, 500]) {
    const r = await runMedia({panels: [author], imageReply: () => ({
      type: 'basic', ok: false, status, blob: async () => new Blob([PNG], {type: 'image/png'})
    })});
    assert.equal(r.receipt.images[0].result, 'failed');
    assert.equal(r.receipt.images[0].error, `http ${status}`);
    assert.equal(r.receipt.summary.saved, 0);
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.downloads, []);
  }
});


for (const family of ['ipv4-racing-port', 'ipv6-other-family']) {
  test(`media-auth: real ${family} impostor receives no content or token`, async t => {
    const http = require('node:http');
    const {once} = require('node:events');
    const received = [];
    const server = http.createServer((req, res) => {
      let body = '';
      req.on('data', data => {body += data;});
      req.on('end', () => {
        received.push({method: req.method, headers: req.headers, body});
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end('{"proof":"impostor"}');
      });
    });
    try {
      server.listen({host: family === 'ipv6-other-family' ? '::1' : '127.0.0.1', port: 0, ipv6Only: true});
      await once(server, 'listening');
    } catch (e) {
      if (family === 'ipv6-other-family' && ['EADDRNOTAVAIL', 'EAFNOSUPPORT'].includes(e.code)) {
        t.skip(`IPv6 unavailable: ${e.code}`); return;
      }
      throw e;
    }
    let ipv4;
    try {
      const port = server.address().port;
      if (family === 'ipv6-other-family') {
        ipv4 = http.createServer((req, res) => res.end('IPv4 listener'));
        ipv4.listen(port, '127.0.0.1');
        await once(ipv4, 'listening');
      }
      const sink = `http://${family === 'ipv6-other-family' ? '[::1]' : '127.0.0.1'}:${port}`;
      const proxy = (url, opts) => fetch(url, {method: opts.method, headers: opts.headers, body: opts.body});
      const r = await runMedia({panels: [author], args: {sink}, helloReply: proxy, reply: proxy});
      assert.equal(r.receipt.ok, false);
      if (family === 'ipv4-racing-port') assert.equal(r.receipt.fatal, 'sink-unauthenticated');
      assert.deepEqual(r.downloads, []);
      assert.equal(received.filter(req => req.method === 'POST').length, 0);
      assert.equal(JSON.stringify(received).includes(H.TOKEN), false);
    } finally {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
      if (ipv4) await new Promise(resolve => ipv4.close(resolve));
    }
  });
}
