// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const chrome = require('../../shared/lt_chrome/lt-chrome.cjs');

// These are parser boundaries, not executions against browser profiles.
test('lt-chrome TOML: multiline strings cannot add browser settings', () => {
  assert.deepEqual(chrome.readBrowser('[other]\nx="""\n[browser]\nadapter="manual-paste"\n"""\n[browser]\nadapter="auto"'), {adapter:'auto'});
});
test('lt-chrome TOML: literal and escaped Unicode strings', () => {
  assert.deepEqual(chrome.readBrowser("[browser]\nadapter='manual-paste'\nchromium_user_data_dir=\"caf\\u00e9\""), {adapter:'manual-paste',chromium_user_data_dir:'café'});
});
test('lt-chrome TOML: multiline fold and literal quotes', () => {
  assert.equal(chrome.readBrowser('[browser]\nadapter="""manual-\\\n   paste"""').adapter,'manual-paste');
  assert.equal(chrome.readBrowser("[browser]\nchromium_user_data_dir='''folder'one''' ").chromium_user_data_dir,"folder'one");
});
test('lt-chrome TOML: dotted and quoted keys', () => {
  assert.deepEqual(chrome.readBrowser('"browser" . "adapter" = "auto"'),{adapter:'auto'});
});
test('lt-chrome TOML: duplicate and non-string kept values fail closed', () => {
  for (const text of ['[browser]\nadapter="auto"\nadapter="auto"','[browser]\nadapter=[]','browser={adapter="auto"}']) {
    assert.throws(()=>chrome.readBrowser(text),/line [0-9]+/);
  }
});
test('lt-chrome TOML: browser inline table cannot be extended', () => {
  assert.throws(()=>chrome.readBrowser('browser={}\nbrowser.adapter="auto"'),/line 2/);
  assert.throws(()=>chrome.readBrowser('browser.adapter="auto"\n[browser]\nchromium="chrome"'),/line 2/);
});
test('lt-chrome URL: only literal HTTP loopback with a valid raw port', () => {
  for (const url of ['http://127.0.0.1:80','http://[::1]:65535/','']) assert.equal(chrome.validUrl(url),true);
  for (const url of ['http://2130706433:9222','http://127.0.0.1:0','http://127.0.0.1:9222\n','http://localhost:9222','http://127.0.0.1:99999']) assert.equal(chrome.validUrl(url),false);
});
test('lt-chrome env: Windows casing cannot turn third-party tools on', () => {
  assert.deepEqual(chrome.hardenedEnv({chrome_devtools_mcp_category_experimental_third_party:'1',Plugin_Data:'ignored',PATH:'retained'},'win32').env, {
    PATH:'retained',CHROME_DEVTOOLS_MCP_NO_CONFIG_DISCOVERY:'1',CHROME_DEVTOOLS_MCP_NO_UPDATE_CHECKS:'1'});
});
test('lt-chrome dirs: unexpanded host variables are ignored', () => {
  assert.equal(chrome.directories('win32',{HOME:'C:\\Synthetic',APPDATA:'${APPDATA}',LOCALAPPDATA:'${LOCALAPPDATA}'}).config_dir,'C:\\Synthetic\\AppData\\Roaming\\lt-suite');
});
test('lt-chrome args: profile values stay in one argument', () => {
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'lt-chrome-test-'));
  try {
    const file=path.join(dir,'lt-suite.toml');
    fs.writeFileSync(file,'[browser]\nchromium_user_data_dir="--isolated x"');
    const result=chrome.makePlan(['--plan','--cwd',dir,'--env-json',JSON.stringify({HOME:dir,USERPROFILE:dir,APPDATA:dir,LOCALAPPDATA:dir,XDG_CONFIG_HOME:dir,XDG_CACHE_HOME:dir})]);
    assert.equal(result.action,'start');
    assert.ok(result.argv.includes('--userDataDir='+path.join(dir,'--isolated x')));
    assert.equal(result.shell,false);
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
test('lt-chrome TOML: nested headers repeat inside distinct array entries', () => {
  const text='[[other]]\n[other.child]\nx=1\n[[other]]\n[other.child]\nx=2\n[browser]\nadapter="manual-paste"';
  assert.deepEqual(chrome.readBrowser(text),{adapter:'manual-paste'});
});
test('lt-chrome env: unexpanded host variables cannot reach Chrome discovery', () => {
  const env=chrome.hardenedEnv({XDG_CONFIG_HOME:'${XDG_CONFIG_HOME}',LOCALAPPDATA:'${LOCALAPPDATA}',PATH:'retained'},'linux').env;
  assert.equal(env.XDG_CONFIG_HOME,undefined);
  assert.equal(env.LOCALAPPDATA,undefined);
  assert.equal(env.PATH,'retained');
});
