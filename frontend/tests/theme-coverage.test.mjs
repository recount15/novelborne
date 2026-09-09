import test from 'node:test';
import assert from 'node:assert/strict';
import { audit, markupElements, projectAudit, expandIs } from '../tools/audit-theme-coverage.mjs';

const file = 'fixture.vue';
function run(css, template = '<div class="root"><button class="leaf" /></div>', themes = ['a'], surfaces = ['studio']) {
  return audit({ themes, sheets: [{ file: 'fixture.css', css }], elements: markupElements(`<template>${template}</template>`, file, surfaces).elements });
}
const count = report => report.cells.reduce((n, c) => n + c.effectiveDeclarationCount, 0);

test('duplicate declarations, grouped branches and important have one credit', () => {
  const report = run('.leaf,.root .leaf { color:red!important; color:blue; padding:1px; padding:2px } .leaf {color:green}');
  assert.equal(count(report), 2);
  assert.equal(new Set(report.declarationOwnership.map(d => d.id)).size, 2);
  assert.ok(report.duplicates.length);
});

test('invalid, unknown, comments, variables, keyframes and false branches do not inflate counts', () => {
  const report = run('/* color:red */ .leaf {made-up: 2; color:bogus; width:nope; --x:1; padding:1px} .missing{color:red} .dead{color:red} @keyframes x {to{opacity:0}}', '<div class="leaf"/><div v-if="false" class="dead"/>');
  assert.equal(count(report), 1);
  assert.equal(report.invalidOrUnsupportedDeclarations.length, 3);
  assert.equal(report.excludedVariableDeclarationCount, 1);
  assert.ok(report.unknownSelectorCount >= 2);
});

test('class coincidence is not DOM ancestry; tag-only native targets work', () => {
  assert.equal(count(run('.root .leaf{color:red}', '<div class="root"/><button class="leaf"/>')), 0);
  assert.equal(count(run('.root > button{color:red}')), 1);
  assert.equal(count(run('.root > .leaf{color:red}', '<div class="root"><section><button class="leaf"/></section></div>')), 0);
  assert.equal(count(run('.root .leaf{color:red}', '<div class="root"><Teleport to="body"><button class="leaf"/></Teleport></div>')), 0);
});

test(':is products, nested alternatives, combinators and theme roots', () => {
  const report = run('html:root:is([data-theme], :not([data-theme])) :is(.absent, .root) :is(button, input):focus-visible {outline:2px solid red}', undefined, ['a', 'classic']);
  assert.equal(count(report), 1);
  assert.equal(report.unknownSelectorCount > 0, true);
  assert.equal(count(run('.leaf:is(.root > button){color:red}')), 1);
  assert.equal(count(run(':is(.no, :is(.leaf,.root)) {color:red}')), 1);
  assert.equal(expandIs(':is(.a,.b) :is(.c,.d)').length, 4);
  assert.equal(count(run('html[data-theme="a"] .leaf{color:red}', undefined, ['b', 'classic'])), 0);
});

test('literal dynamic classes are native alternatives, not invented simultaneous states', () => {
  const template = '<button class="leaf" :class="on ? \'active\' : \'idle\'"/>';
  assert.equal(count(run('.leaf.active {color:red}', template)), 1);
  assert.equal(count(run('.active.idle {color:red}', template)), 0);
  assert.equal(count(run('.leaf.active {color:red}', '<Icon class="leaf active"/>')), 0);
});

test('shared declarations have explicit single ownership across themes and interfaces', () => {
  const report = run('.leaf,.root {color:red; padding:1px}', undefined, ['a', 'b'], ['studio', 'reader']);
  assert.equal(count(report), 2);
  assert.equal(report.projectUniqueApplicableDeclarationCount, count(report));
  assert.deepEqual(report.declarationOwnership.map(d => [d.theme, d.interface]), [['a', 'studio'], ['a', 'studio']]);
  assert.equal(report.visualVerified, false);
  assert.ok(report.cells.every(c => c.status === 'UNMET'));
});

test('new CharacterDossier participates in the actual 28 × 7 project audit', () => {
  const report = projectAudit();
  assert.equal(report.themes.length, 28);
  assert.equal(report.cells.length, 196);
  assert.deepEqual(report.surfaceManifest['components/CharacterDossier.vue'], ['dossier']);
  assert.ok(report.declarationEvidence.some(d => d.elements.some(e => e.file.endsWith('/CharacterDossier.vue') && e.classes.includes('dossier-identity'))));
  assert.equal(count(report), report.projectUniqueApplicableDeclarationCount);
  assert.equal(report.visualVerified, false);
  assert.ok(report.cells.every(c => c.status !== 'PASS'));
});
