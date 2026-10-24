// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* The worked example's interactive page for the JavaScript tests.

   fixtures/toy-case-page.html is the built page of skills/mechanistic-diagram/examples/toy-case, cut down by
   make_page_fixture.py: no base64 payloads or fonts, and the marker ENGINE_SCRIPTS where the page inlines
   artifact_logic.js and artifact_ui.js. pageHtml() puts the engine's current files back in, so the tests
   always run today's scripts on the example's figures. FIG_PAGE=<a built page> tests that page instead. */
'use strict';
const fs = require('node:fs');
const path = require('node:path');

const ENGINE = path.join(__dirname, '..', '..', 'skills', 'mechanistic-diagram', 'engine');
const FIXTURE = path.join(__dirname, 'fixtures', 'toy-case-page.html');
const MARKER = '/* ENGINE_SCRIPTS */';

function pageHtml() {
  if (process.env.FIG_PAGE) return fs.readFileSync(process.env.FIG_PAGE, 'utf8');
  const html = fs.readFileSync(FIXTURE, 'utf8');
  if (!html.includes(MARKER)) throw new Error('page fixture without the ENGINE_SCRIPTS marker');
  const js = fs.readFileSync(path.join(ENGINE, 'artifact_logic.js'), 'utf8') + '\n' +
    fs.readFileSync(path.join(ENGINE, 'artifact_ui.js'), 'utf8');
  return html.replace(MARKER, () => js);
}

module.exports = {ENGINE, FIXTURE, pageHtml};
