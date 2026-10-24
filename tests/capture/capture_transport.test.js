// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Authentication regressions over real local listeners and captured request bytes. */
'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {once} = require('node:events');
const {createHash, createHmac} = require('node:crypto');
const H = require('./vm_harness.js');
const {demoEnv} = require('./scenarios.js');

for (const host of ['localhost', '[::1]']) {
  test(`transport-auth: ${host} is refused without any request`, async () => {
    const r = await H.runCapture(demoEnv(), {args: {sink: `http://${host}:8731`}});
    assert.equal(r.receipt.ok, false);
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.hellos, []);
  });
}

test('transport-auth: proofs cover exact name and body; the secret never travels', async () => {
  const r = await H.runCapture(demoEnv());
  assert.equal(r.receipt.ok, true);
  assert.equal(r.hellos.length, 1);
  assert.match(new URL(r.hellos[0].url).searchParams.get('n'), /^[0-9a-f]{32}$/);
  for (const req of r.posts) {
    const name = new URL(req.url).pathname.slice(1);
    const digest = createHash('sha256').update(req.body).digest('hex');
    const proof = createHmac('sha256', H.TOKEN).update(`${name}|${digest}`).digest('hex');
    assert.equal(req.headers['X-Lt-Suite-Auth'], proof);
  }
  assert.equal(JSON.stringify([...r.hellos, ...r.posts]).includes(H.TOKEN), false);
});

for (const family of ['ipv4-racing-port', 'ipv6-other-family']) {
  test(`transport-auth: ${family} impostor receives no content or token`, async t => {
    const requests = [];
    const server = http.createServer((req, res) => {
      let body = '';
      req.on('data', b => {body += b;});
      req.on('end', () => {
        requests.push({method: req.method, url: req.url, headers: req.headers, body});
        res.writeHead(200, {'Content-Type': 'application/json'});
        res.end('{"ok":true,"proof":"unproven"}');
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
    const port = server.address().port;
    let ipv4;
    try {
      if (family === 'ipv6-other-family') {
        ipv4 = http.createServer((req, res) => res.end('legitimate IPv4 family'));
        ipv4.listen(port, '127.0.0.1');
        await once(ipv4, 'listening');
      } else {
        // A competing listener has already won the bind before the sink starts.
        const competitor = http.createServer();
        competitor.on('error', () => {});
        competitor.listen(port, '127.0.0.1');
        const [error] = await once(competitor, 'error');
        assert.equal(error.code, 'EADDRINUSE');
      }
      const proxy = async (_name, req) => fetch(req.url, {
        method: req.method, headers: req.headers, body: req.body
      });
      const sink = `http://${family === 'ipv6-other-family' ? '[::1]' : '127.0.0.1'}:${port}`;
      const r = await H.runCapture(demoEnv(), {args: {sink}, reply: proxy, helloReply: proxy});
      assert.equal(r.receipt.ok, false);
      if (family === 'ipv4-racing-port') assert.equal(r.receipt.fatal, 'sink-unauthenticated');
      assert.deepEqual(r.downloads, []);
      assert.equal(requests.filter(r => r.method === 'POST').length, 0);
      assert.equal(JSON.stringify(requests).includes(H.TOKEN), false);
    } finally {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
      if (ipv4) await new Promise(resolve => ipv4.close(resolve));
    }
  });
}

test('transport-auth: wrong-origin or echoed proofs fail closed', async () => {
  for (const origin of ['https://other.example.invalid', '']) {
    const r = await H.runCapture(demoEnv(), {helloReply: (_name, req) => {
      const nonce = new URL(req.url).searchParams.get('n');
      return H.response(200, {proof: createHmac('sha256', H.TOKEN)
        .update(`lt-suite-sink|${nonce}|${origin}`).digest('hex')});
    }});
    assert.equal(r.receipt.fatal, 'sink-unauthenticated');
    assert.deepEqual(r.posts, []);
    assert.deepEqual(r.downloads, []);
  }
});
