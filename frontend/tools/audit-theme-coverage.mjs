import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import postcss from 'postcss';
import { parse as parseSfc } from '@vue/compiler-sfc';
import { parse as parseTemplate } from '@vue/compiler-dom';
import { parseExpression } from '@babel/parser';
import { transform } from 'lightningcss';

export const SURFACES = ['studio', 'library', 'dossier', 'designer', 'reader', 'chat', 'jobs'];
export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export function registry(source) {
  const block = source.match(/export const THEME_IDS\s*=\s*\[([\s\S]*?)\]\s*as const/);
  if (!block) throw new Error('THEME_IDS registry format not recognized');
  const ids = [...block[1].matchAll(/["']([a-z][a-z0-9-]*)["']/g)].map(m => m[1]);
  if (!/ThemeId\s*=.*\|\s*["']classic["']/.test(source) || !/delete document\.documentElement\.dataset\.theme/.test(source)) throw new Error('classic fallback requires review');
  ids.push('classic');
  const meta = source.match(/export const THEME_META[^=]*=\s*\{([\s\S]*?)\n\};/);
  const keys = [...(meta?.[1] || '').matchAll(/^\s{2}([a-z][a-z0-9-]*):\s*\{/gm)].map(m => m[1]);
  if (new Set(ids).size !== ids.length || keys.sort().join() !== [...ids].sort().join()) throw new Error('Registry and THEME_META disagree');
  return ids;
}

// Only literal class alternatives are recovered; expressions are never executed.
function classAlternatives(node) {
  if (!node) return [[]];
  if (node.type === 'StringLiteral') return [node.value.split(/\s+/).filter(Boolean)];
  if (node.type === 'ConditionalExpression') return [...classAlternatives(node.consequent), ...classAlternatives(node.alternate)];
  if (node.type === 'LogicalExpression') return [[], ...classAlternatives(node.right)];
  const combine = groups => groups.reduce((a, b) => a.flatMap(x => b.map(y => [...x, ...y])).slice(0, 128), [[]]);
  if (node.type === 'ArrayExpression') return combine(node.elements.map(classAlternatives));
  if (node.type === 'ObjectExpression') return combine(node.properties.map(p => !p.computed && p.type === 'ObjectProperty' ? [[], [(p.key.name || p.key.value)]] : [[]]));
  return [[]];
}
export function markupElements(source, file, surfaces = []) {
  const { descriptor, errors } = parseSfc(source, { filename: file });
  if (errors.length) throw new Error(`Invalid SFC: ${file}`);
  const ast = parseTemplate(descriptor.template?.content || '');
  const elements = [];
  function visit(node, inherited, parent = null) {
    if ((node.props || []).some(p => p.type === 7 && p.name === 'if' && /^(false|0)$/.test(p.exp?.content || ''))) return;
    const attrs = Object.fromEntries((node.props || []).filter(p => p.type === 6).map(p => [p.name, p.value?.content || '']));
    const classes = (attrs.class || '').split(/\s+/).filter(Boolean);
    const binding = (node.props || []).find(p => p.type === 7 && p.name === 'bind' && p.arg?.content === 'class');
    let alternatives = [[]];
    try { if (binding?.exp) alternatives = classAlternatives(parseExpression(binding.exp.content)); } catch { /* unresolved binding */ }
    let scopes = inherited;
    if (file.endsWith('/App.vue')) {
      if (classes.some(c => /^(pool-|dossier|character-pool)/.test(c))) scopes = ['dossier'];
      if (classes.some(c => /^(chat-bubble|chat-messages|scene-chat)/.test(c))) scopes = ['chat'];
    }
    if (node.type === 1 && node.tagType === 0) {
      const element = { file, line: node.loc.start.line + (descriptor.template?.loc.start.line || 1) - 1, offset: node.loc.start.offset, tag: node.tag, classes, classAlternatives: alternatives.map(a => [...new Set([...classes, ...a])]), attrs, surfaces: scopes };
      Object.defineProperty(element, 'parent', { value: parent });
      elements.push(element);
      parent = element;
    } else if (node.type === 1 && node.tagType === 1) parent = null; // Component/Teleport boundary is not native ancestry.
    for (const child of node.children || []) visit(child, scopes, parent);
  }
  visit(ast, surfaces);
  return { elements, styles: descriptor.styles.map(s => s.content), scoped: descriptor.styles.map(s => s.scoped) };
}

function splitSelector(text, delimiter = ',') {
  const parts = []; let start = 0, depth = 0, quote = '';
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) { if (c === quote && text[i - 1] !== '\\') quote = ''; continue; }
    if (c === '"' || c === "'") { quote = c; continue; }
    if (c === '(' || c === '[') depth++;
    if (c === ')' || c === ']') depth--;
    if (!depth && c === delimiter) { parts.push(text.slice(start, i).trim()); start = i + 1; }
  }
  parts.push(text.slice(start).trim()); return parts.filter(Boolean);
}
export function expandIs(selector) {
  const match = /:(?:is|where)\(/.exec(selector);
  if (!match) return [selector];
  let depth = 1, end = match.index + match[0].length;
  const start = end;
  for (; end < selector.length && depth; end++) { if (selector[end] === '(') depth++; if (selector[end] === ')') depth--; }
  if (depth) return [];
  const before = selector.slice(0, match.index);
  const compoundStart = Math.max(before.lastIndexOf(' '), before.lastIndexOf('>')) + 1;
  const prefix = before.slice(compoundStart);
  return splitSelector(selector.slice(start, end - 1)).flatMap(branch => {
    const targetStart = Math.max(branch.lastIndexOf(' '), branch.lastIndexOf('>')) + 1;
    // .target:is(.ancestor .leaf) means .ancestor .target.leaf, not .target.ancestor .leaf.
    const target = branch.slice(targetStart);
    const joined = /^[.#[:]/.test(prefix) && /^[a-z*]/i.test(target) ? target + prefix : prefix + target;
    return expandIs(before.slice(0, compoundStart) + branch.slice(0, targetStart) + joined + selector.slice(end));
  }).slice(0, 512);
}
function compoundMatches(compound, element) {
  let rest = compound.replace(/::(?:before|after|marker|placeholder|selection)\b/g, '').replace(/:(?:hover|focus-visible|focus-within|focus|active|disabled|checked|open)\b/g, '');
  const classes = [...rest.matchAll(/\.([\w-]+)/g)].map(m => m[1]);
  if (!(element.classAlternatives || [element.classes]).some(a => classes.every(c => a.includes(c)))) return false;
  rest = rest.replace(/\.[\w-]+/g, '');
  let valid = true;
  rest = rest.replace(/\[([\w-]+)(?:\s*=\s*["']?([^\]"']+)["']?)?\]/g, (_, key, value) => { valid &&= Object.hasOwn(element.attrs || {}, key) && (value === undefined || element.attrs[key] === value); return ''; });
  rest = rest.replace(/#([\w-]+)/g, (_, id) => { valid &&= element.attrs?.id === id; return ''; });
  return valid && (rest === '' || rest === '*' || rest.toLowerCase() === element.tag.toLowerCase());
}
function matchesSelector(selector, element) {
  // Spaces inside attributes are preserved; unsupported siblings/pseudos remain unknown.
  const tokens = splitSelector(selector.replace(/\s*>\s*/g, '>'), ' ').flatMap(s => s.split(/(>)/)).filter(Boolean);
  function match(index, e) {
    if (!e || !compoundMatches(tokens[index], e)) return false;
    if (index === 0) return true;
    if (tokens[index - 1] === '>') return match(index - 2, e.parent);
    for (let p = e.parent; p; p = p.parent) if (match(index - 1, p)) return true;
    return false;
  }
  return tokens.length > 0 && match(tokens.length - 1, element);
}
const validity = new Map();
function validDeclaration(decl) {
  const key = `${decl.prop}:${decl.value}`;
  if (validity.has(key)) return validity.get(key);
  let valid = false;
  try { transform({ filename: 'declaration.css', code: Buffer.from(`a{${key}}`), visitor: { Declaration(d) {
    valid = d.property !== 'custom' && d.property !== 'unparsed';
    // Substitution values are structurally valid but still require computed-style verification.
    if (d.property === 'unparsed') valid = /\b(?:var|env)\(/.test(decl.value);
  } } }); } catch { /* invalid syntax */ }
  validity.set(key, valid); return valid;
}

export function audit({ themes, sheets, elements }) {
  const records = [], winners = new Map(), duplicates = [], unknownSelectors = new Set(), invalidDeclarations = new Map();
  const presence = Object.fromEntries(themes.map(t => [t, { maxWidth: false, reducedMotion: false, forcedColors: false }]));
  for (const sheet of sheets) {
    const root = postcss.parse(sheet.css, { from: sheet.file });
    root.walkRules(rule => {
      const parents = [];
      for (let p = rule.parent; p && p.type !== 'root'; p = p.parent) parents.unshift(p);
      if (parents.some(p => p.type === 'atrule' && /keyframes$/i.test(p.name))) return;
      const context = parents.map(p => p.type === 'atrule' ? `@${p.name} ${p.params.replace(/\s+/g, ' ').trim()}` : p.selector).join('|');
      for (const selector of splitSelector(rule.selector).flatMap(expandIs)) {
        const normalized = selector.trim().replace(/\s+/g, ' ');
        const explicit = [...normalized.matchAll(/\[data-theme\s*=\s*["']?([\w-]+)["']?\]/g)].map(m => m[1]);
        const classic = /:not\(\[data-theme\]\)/.test(normalized);
        const applicable = explicit.length || classic ? themes.filter(t => explicit.includes(t) || (classic && t === 'classic')) : /\[data-theme\]/.test(normalized) ? themes.filter(t => t !== 'classic') : themes;
        const themed = explicit.length > 0 || (classic && !rule.selector.replace(/:not\(\[data-theme\]\)/g, '').includes('[data-theme]'));
        // Theme roots are virtual document ancestors, never credited as interface elements.
        const simple = normalized.replace(/:not\(\[data-theme\]\)/g, '').replace(/\[data-theme[^\]]*\]/g, '').replace(/^html(?::root)?\s+|^:root\s+/, '').trim();
        const unsupported = /[()\\&+~]/.test(simple) || parents.some(p => p.type === 'rule');
        const target = simple.split(/\s+|>/).filter(Boolean).at(-1) || '';
        const targetHooks = [...target.matchAll(/\.([a-zA-Z_][\w-]*)/g)].map(m => m[1]);
        const matched = unsupported ? [] : elements.filter(e => (!sheet.scopedFile || e.file === sheet.scopedFile) && matchesSelector(simple, e));
        if (!matched.length) unknownSelectors.add(`${sheet.file}:${normalized}`);
        for (const theme of applicable) {
          if (themed) {
            presence[theme].maxWidth ||= /@media.*max-width/.test(context);
            presence[theme].reducedMotion ||= /@media.*prefers-reduced-motion\s*:\s*reduce/.test(context);
            presence[theme].forcedColors ||= /@media.*forced-colors\s*:\s*active/.test(context);
          }
          rule.each(decl => {
            if (decl.type !== 'decl') return;
            if (!decl.prop.startsWith('--') && !validDeclaration(decl)) { invalidDeclarations.set(`${sheet.file}:${decl.source.start.offset}`, { file: sheet.file, line: decl.source.start.line, property: decl.prop, value: decl.value }); return; }
            const id = `${sheet.file}:${decl.source.start.offset}`;
            const key = `${theme}|${sheet.scopedFile || ''}|${context}|${normalized}|${decl.prop.startsWith('--') ? decl.prop : decl.prop.toLowerCase()}`;
            const record = { id, theme, file: sheet.file, line: decl.source.start.line, selector: normalized, context, property: decl.prop, value: decl.value, important: !!decl.important, themed, variable: decl.prop.startsWith('--'), matched, hooks: targetHooks };
            const previous = winners.get(key);
            if (previous) {
              duplicates.push({ theme, selector: normalized, context, property: decl.prop, overridden: previous.important && !record.important ? id : previous.id, winner: previous.important && !record.important ? previous.id : id, surfaces: [...new Set([...previous.matched, ...matched].flatMap(e => e.surfaces))] });
              if (previous.important && !record.important) return;
            }
            winners.set(key, record);
          });
        }
      }
    });
  }
  records.push(...winners.values());
  // A physical declaration has exactly one owner in the entire theme × interface matrix.
  // Prefer an explicit theme branch, then registry order and SURFACES order.
  const ownership = new Map();
  for (const r of [...records].sort((a, b) => Number(b.themed) - Number(a.themed) || themes.indexOf(a.theme) - themes.indexOf(b.theme))) {
    if (r.variable || !r.matched.length || ownership.has(r.id)) continue;
    const surface = SURFACES.find(s => r.matched.some(e => e.surfaces.includes(s)));
    if (surface) ownership.set(r.id, { id: r.id, theme: r.theme, interface: surface, shared: !r.themed });
  }
  const cells = themes.flatMap(theme => SURFACES.map(surface => {
    const applicable = records.filter(r => r.theme === theme && !r.variable && r.matched.some(e => e.surfaces.includes(surface)) && ownership.get(r.id)?.theme === theme && ownership.get(r.id)?.interface === surface);
    const direct = new Set(applicable.filter(r => r.themed).map(r => r.id));
    const shared = new Set(applicable.filter(r => !r.themed && !direct.has(r.id)).map(r => r.id));
    const hookSet = new Set(applicable.flatMap(r => r.hooks));
    // One native markup signature is one element kind, not every class, state, pseudo-element or v-for instance.
    const elementKinds = new Set(applicable.flatMap(r => r.matched.filter(e => e.surfaces.includes(surface)).map(e => `${e.tag}.${[...e.classes].sort().join('.')}`)));
    const effective = direct.size + shared.size;
    return { theme, interface: surface, directDeclaredCount: direct.size, sharedApplicableCount: shared.size, effectiveDeclarationCount: effective, declarationShortfall: Math.max(0, 1000 - effective), elementKindShortfall: Math.max(0, 10 - elementKinds.size), uniqueElementHooks: [...hookSet].sort(), uniqueElementKindCount: elementKinds.size, duplicatedDeclarationCount: duplicates.filter(d => d.theme === theme && d.surfaces.includes(surface)).length, deadUnknownDeclarationCount: new Set(records.filter(r => r.theme === theme && !r.matched.length).map(r => r.id)).size, sourceThresholdMet: effective >= 1000 && elementKinds.size >= 10, status: effective >= 1000 && elementKinds.size >= 10 ? 'SOURCE_ONLY_NOT_VISUALLY_VERIFIED' : 'UNMET' };
  }));
  return { schemaVersion: 2, scope: 'source-only', visualVerified: false, invalidOrUnsupportedDeclarations: [...invalidDeclarations.values()], declarationOwnership: [...ownership.values()], ownershipPolicy: 'One physical declaration, one matrix cell; explicit theme first, then theme registry order and interface manifest order. Shared applicability is not additional credit.', thresholds: { effectiveDeclarationsPerCell: 1000, actualElementKindsPerCell: 10 }, themes, interfaces: SURFACES, cells, responsivePresence: presence, projectUniqueApplicableDeclarationCount: new Set(records.filter(r => !r.variable && r.matched.length).map(r => r.id)).size, excludedVariableDeclarationCount: new Set(records.filter(r => r.variable).map(r => r.id)).size, unknownSelectorCount: unknownSelectors.size, duplicates, declarationEvidence: records.map(({ matched, ...r }) => ({ ...r, elements: matched.map(e => ({ file: e.file, line: e.line, tag: e.tag, classes: e.classes, surfaces: e.surfaces })) })), limitations: ['Native template ancestry is checked; runtime conditions, component fallthrough, Teleport placement and actual rendering are not proven.', 'Literal dynamic class alternatives, tag targets and :is/:where combinations are supported. Other functional/escaped/nested/sibling selectors and computed class expressions are conservatively unknown. Expansion caps are 128 class alternatives and 512 selector branches; unknown does not mean proven dead.', 'Exact selector + at-rule context + property overrides are deduplicated (including !important); specificity, shorthand, overlapping media and cross-layer cascade need browser verification.', 'Variables, keyframes, invalid and unsupported declarations never count. Unparsed var/env values require browser computed-style validation. Every physical declaration is credited to only one cell; matrix counts may be summed. Element kinds are native tag + static class signatures, not class/state/v-for counts.', 'Responsive flags only show theme-scoped source presence. No visual AA, foreground contrast, usable responsive layout or motif quality claim.'] };
}

export function projectAudit() {
  const src = path.join(ROOT, 'frontend/src');
  const inputs = [];
  const read = file => { const text = fs.readFileSync(file, 'utf8'); inputs.push({ file, sha256: createHash('sha256').update(text).digest('hex') }); return text; };
  const themes = registry(read(path.join(src, 'themeSwitch.ts')));
  const mainFile = path.join(src, 'assets/main.css');
  const main = read(mainFile);
  const imports = [...main.matchAll(/@import\s+["']\.\/themes\/([\w-]+)\.css["']/g)].map(m => m[1]);
  if ([...imports].sort().join() !== [...themes].sort().join()) throw new Error('Theme import manifest differs from supported registry');
  const sheets = imports.map(t => { const file = path.join(src, `assets/themes/${t}.css`); return { file, css: read(file) }; });
  sheets.push({ file: mainFile, css: main });
  const map = { 'components/CharacterDossier.vue': ['dossier'], 'App.vue': ['studio'], 'components/LibraryScene.vue': ['library'], 'views/CharacterDesigner.vue': ['designer'], 'components/OriginalReaderModal.vue': ['reader'], 'components/ReaderChatPanel.vue': ['chat'], 'components/reader/ReaderCharactersPanel.vue': ['dossier', 'reader'], 'components/reader/ReaderBookmarksPanel.vue': ['reader'], 'components/reader/ReaderAnchorsPanel.vue': ['reader'], 'components/PreparationPanel.vue': ['studio'], 'components/PreparationJobPanel.vue': ['jobs'], 'components/GenerationStatePanel.vue': ['jobs'] };
  const elements = [];
  for (const [relative, surfaces] of Object.entries(map)) {
    const file = path.join(src, relative).replaceAll('\\', '/');
    const parsed = markupElements(read(file), file, surfaces);
    elements.push(...parsed.elements);
    parsed.styles.forEach((css, i) => sheets.push({ file: `${file}#style-${i}`, scopedFile: parsed.scoped[i] ? file : undefined, css }));
  }
  return { ...audit({ themes, sheets, elements }), generatedAt: new Date().toISOString(), registryVerified: true, inputs, surfaceManifest: map };
}

export function writeEvidence(output, report) {
  const destination = path.resolve(output);
  const relative = path.relative(ROOT, destination);
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) throw new Error('--output must be inside workspace');
  // Parent must already exist; realpath blocks symlink/junction escapes. Never overwrite evidence.
  const parent = fs.realpathSync(path.dirname(destination));
  const physical = path.relative(fs.realpathSync(ROOT), parent);
  if (physical.startsWith('..') || path.isAbsolute(physical)) throw new Error('Output parent escapes workspace');
  fs.writeFileSync(destination, JSON.stringify(report, null, 2) + '\n', { flag: 'wx' });
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const args = process.argv.slice(2);
    if (args.length && (args.length !== 2 || args[0] !== '--output')) throw new Error('Usage: node frontend/tools/audit-theme-coverage.mjs [--output workspace/unique-existing-directory/report.json]');
    const report = projectAudit();
    if (args.length) writeEvidence(args[1], report);
    const unmet = report.cells.filter(c => !c.sourceThresholdMet).length;
    console.log(JSON.stringify({ themes: report.themes.length, cellCount: report.cells.length, unmet, projectUniqueApplicableDeclarationCount: report.projectUniqueApplicableDeclarationCount, status: unmet ? 'UNMET' : 'SOURCE_ONLY_NOT_VISUALLY_VERIFIED', visualVerified: false, invalidOrUnsupportedDeclarationCount: report.invalidOrUnsupportedDeclarations.length, unknownSelectorCount: report.unknownSelectorCount, cells: report.cells.map(c => ({ theme: c.theme, interface: c.interface, declarations: c.effectiveDeclarationCount, elementKinds: c.uniqueElementKindCount, declarationShortfall: c.declarationShortfall, elementKindShortfall: c.elementKindShortfall })), output: args[1] || null }));
    process.exitCode = unmet ? 1 : 0;
  } catch (error) { console.error(error.message); process.exitCode = 2; }
}
