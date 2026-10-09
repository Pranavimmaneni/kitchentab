"""Render .dc.html mockups to PNG with a minimal template expander.

usage: python3 render.py OUTDIR MODE NATURAL file.dc.html[:stateJSON] ...
  MODE: light | dark ; NATURAL: 1 = drop inner overflow and measure natural height
Prints "<name> <naturalHeight>" per file.
"""
import json, sys, pathlib, re
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
FONTS = HERE / 'node_modules' / '@fontsource'

FONT_CSS = ''.join(
    f"@font-face{{font-family:'{fam}';font-weight:{w};src:url('file://{FONTS}/{pkg}/files/{pkg}-latin-{w}-normal.woff2') format('woff2')}}"
    for fam, pkg, ws in [('Space Mono', 'space-mono', [400, 700]), ('Work Sans', 'work-sans', [400, 500, 600, 700])]
    for w in ws)

SHIM = r"""
window.DCLogic = class { constructor(p){ this.props = p || {}; this.state = {}; } setState(s){ Object.assign(this.state, typeof s === 'function' ? s(this.state) : s); } forceUpdate(){} };
window.__render = function(props, stateOverride, natural){
  const lookup = (scope, path) => { path = path.trim(); if (path === 'true') return true; if (path === 'false') return false; if (/^-?\d/.test(path)) return Number(path);
    let v = scope; for (const k of path.split('.')) { if (v == null) return undefined; v = v[k]; } return v; };
  const interp = (s, scope) => s.replace(/\{\{\s*([^}]+?)\s*\}\}/g, (m, p) => { const v = lookup(scope, p); return v == null ? '' : String(v); });
  const strip = s => s.replace(/[{}\s]/g, '');
  const SVG = 'http://www.w3.org/2000/svg';
  function expand(node, scope, out){
    if (node.nodeType === 3) { out.appendChild(document.createTextNode(interp(node.nodeValue, scope))); return; }
    if (node.nodeType !== 1) return;
    const tag = node.tagName.toLowerCase();
    if (tag === 'sc-for') { (lookup(scope, strip(node.getAttribute('list'))) || []).forEach((it, i) => { const s = Object.assign({}, scope, { [node.getAttribute('as')]: it, $index: i }); node.childNodes.forEach(ch => expand(ch, s, out)); }); return; }
    if (tag === 'sc-if') { if (lookup(scope, strip(node.getAttribute('value')))) node.childNodes.forEach(ch => expand(ch, scope, out)); return; }
    if (tag === 'helmet') return;
    const el = node.namespaceURI === SVG ? document.createElementNS(SVG, node.tagName) : document.createElement(node.tagName);
    for (const a of node.attributes) { if (/^on/i.test(a.name)) continue; el.setAttribute(a.name, interp(a.value, scope)); }
    node.childNodes.forEach(ch => expand(ch, scope, el));
    out.appendChild(el);
  }
  const xdc = document.querySelector('x-dc');
  const helmet = xdc.querySelector('helmet');
  if (helmet) [...helmet.children].forEach(ch => { if (ch.tagName === 'STYLE') document.head.appendChild(ch.cloneNode(true)); });
  const code = document.querySelector('script[type="text/x-dc"]').textContent;
  const Comp = new Function(code + '; return Component;')();
  const comp = new Comp(props);
  Object.assign(comp.state, stateOverride || {});
  const vals = comp.renderVals();
  const host = document.createElement('div'); host.id = 'host';
  xdc.childNodes.forEach(ch => expand(ch, vals, host));
  xdc.replaceWith(host);
  const root = host.firstElementChild;
  if (natural) {
    root.querySelectorAll('*').forEach(e => { if (/overflow/.test(e.getAttribute('style') || '')) { e.style.overflow = 'visible'; e.style.overflowX = 'visible'; } });
    root.style.height = 'auto'; root.style.minHeight = root.getAttribute('data-h') || '844px';
  }
  return Math.ceil(root.getBoundingClientRect().height);
};
"""

def main():
    out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    mode = sys.argv[2]; natural = sys.argv[3] == '1'
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
        for spec in sys.argv[4:]:
            f, _, st = spec.partition(':')
            state = json.loads(st) if st else {}
            src = pathlib.Path(f).read_text()
            src = src.replace('<script src="./support.js"></script>', f'<style>{FONT_CSS}</style><script>{SHIM}</script>')
            src = re.sub(r'<link rel="stylesheet" href="https://fonts[^>]*>', '', src)
            tmp = HERE / '_page.html'; tmp.write_text(src)
            pg.goto(tmp.as_uri())
            h = pg.evaluate('([p,s,n]) => window.__render(p,s,n)', [{'mode': mode}, state, natural])
            pg.evaluate('document.fonts.ready')
            pg.wait_for_timeout(700)
            name = pathlib.Path(f).stem.replace('.dc', '') + ('' if not st else '_' + re.sub(r'\W+', '', st)[:20]) + '_' + mode
            pg.set_viewport_size({'width': 390, 'height': max(h, 844)})
            pg.screenshot(path=str(out / f'{name}.png'), full_page=True)
            pg.set_viewport_size({'width': 390, 'height': 844})
            print(name, h)
        b.close()

main()
