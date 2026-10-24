// SPDX-FileCopyrightText: 2026 dorsaltegmentum
// SPDX-License-Identifier: AGPL-3.0-or-later
/* Small DOM boundary double for node tests. It parses trees; capture.js owns all filtering.
 * Browser parsing is separately exercised by the optional headless-browser regression. */
'use strict';
class Node {
  constructor(type, tag = '', attrs = {}, raw = '') {
    this.nodeType = type; this.localName = tag; this.attrs = attrs;
    this.raw = raw; this.childNodes = []; this.parentNode = null;
  }
  append(node) {node.parentNode = this; this.childNodes.push(node);}
  remove() {this.parentNode.childNodes = this.parentNode.childNodes.filter(x => x !== this);}
  hasAttribute(name) {return Object.hasOwn(this.attrs, name);}
  getAttribute(name) {return this.attrs[name] ?? null;}
  get innerHTML() {return this.childNodes.map(n => n.outerHTML).join('');}
  get outerHTML() {
    if (this.nodeType !== 1) return this.raw;
    return `<${this.localName}${Object.entries(this.attrs).map(([k,v]) => ` ${k}="${v}"`).join('')}>${this.innerHTML}</${this.localName}>`;
  }
}
class DOMParser {
  parseFromString(html) {
    const body = new Node(1, 'body');
    const stack = [body];
    // Script/style raw text is kept as one node, just as an inert browser parser does.
    const tokens = String(html).match(/<!--[\s\S]*?(?:-->|$)|<(script|style)\b[^>]*>[\s\S]*?<\/\1\s*>|<[^>]+>|[^<]+|</gi) || [];
    for (const tok of tokens) {
      const parent = stack.at(-1);
      if (tok.startsWith('<!--')) {parent.append(new Node(8, '', {}, tok)); continue;}
      if (/^<\//.test(tok)) {
        const tag = tok.match(/^<\/\s*([\w-]+)/)?.[1]?.toLowerCase();
        const idx = stack.findLastIndex(n => n.localName === tag);
        if (idx > 0) stack.length = idx;
        continue;
      }
      const open = tok.match(/^<([\w-]+)\b([^>]*?)(\/?)>/);
      if (!open) {parent.append(new Node(3, '', {}, tok)); continue;}
      const tag = open[1].toLowerCase(), attrs = {};
      const re = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
      for (const a of open[2].matchAll(re)) attrs[a[1].toLowerCase()] = a[2] ?? a[3] ?? a[4] ?? '';
      const node = new Node(1, tag, attrs);
      parent.append(node);
      if (/^(script|style)$/.test(tag)) {
        node.append(new Node(3, '', {}, tok.slice(open[0].length).replace(/<\/[\w-]+\s*>$/, '')));
      } else if (!open[3] && !/^(area|base|br|col|embed|hr|img|input|link|meta|param|source|track|wbr)$/.test(tag)) stack.push(node);
    }
    return {body, childNodes: [body]};
  }
}
module.exports = {DOMParser};
