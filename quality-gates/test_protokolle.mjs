// Lokaler Test der Protokoll-Nodes ohne n8n: node quality-gates/test_protokolle.mjs
// Simuliert die Schleife P1 -> P2 -> Urteil -> Ueberarbeitung mit Attrappen fuer Groq und Gemini.
import fs from 'fs';
import assert from 'assert/strict';

const dir = new URL('./nodes/', import.meta.url);
const lade = (f, ersatz = {}) => {
  let s = fs.readFileSync(new URL(f, dir), 'utf8');
  for (const [a, b] of Object.entries(ersatz)) s = s.replace(a, b);
  return new Function('$input', '$', `return (async () => {${s}})()`);
};
const P1 = lade('p1_regelcheck.js');
const URTEIL = lade('urteil_bilden.js', { __MAX__: '2' });
const LESEN = lade('ueberarbeitung_lesen.js');

const ARTIKEL = { title: 'claude-code-templates: one CLI to configure and monitor Claude Code',
  link: 'https://github.com/davila7/claude-code-templates',
  description: 'Open-source CLI under the MIT license. Started July 2025, 31303 stars.' };

async function lauf(fn, input, refs) {
  const $ = name => ({ first: () => ({ json: refs[name] }), item: { json: refs[name] } });
  return (await fn({ first: () => ({ json: input }) }, $))[0].json;
}
const gemini = obj => ({ candidates: [{ content: { parts: [{ text: '```json\n' + JSON.stringify(obj) + '\n```' }] },
  groundingMetadata: { webSearchQueries: ['claude-code-templates release date'] } }] });

// Echtes Skript aus Exec 980 (hat "A new open source CLI" - faktisch falsch)
const S980 = "Stop manually configuring your Claude Code agents. A new open source CLI called claude code templates installs ready made agents and slash commands with one command. It also includes a dashboard to monitor active sessions. The project is under the MIT license and currently has thirty one thousand and three hundred GitHub stars. Use it to deploy complex agent setups in minutes instead of hours. My take, it saves setup time but only works if you are already using Claude Code. It does not fix broken prompts, just makes them easier to manage. Keep your workflow clean with claude code templates.";

let ok = 0;
const pruefe = (name, f) => f().then(() => { ok++; console.log('✓', name); });

await pruefe('P1: echtes Skript 980 besteht die harten Regeln', async () => {
  const r = await lauf(P1, { title: 't', scriptText: S980 }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(r.p1.bestanden, true, r.p1.hart.join(' | '));
  assert.equal(r.versuch, 0);
  assert.match(r.p2Prompt, /Opus 4\.8/);
  assert.match(r.p2Prompt, /claude-code-templates/);
});

await pruefe('P1: Ziffern, Hype, CTA, fehlender Take, "It"-Hook -> hart', async () => {
  const s = 'It is insane. OpenAI cut GPT prices by 50% today. ' + 'word '.repeat(80) + 'Follow for more.';
  const r = await lauf(P1, { scriptText: s }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(r.p1.bestanden, false);
  const alles = r.p1.hart.join(' ');
  for (const t of ['50%', 'insane', 'follow for more', 'VERDICT', '"It"']) assert.ok(alles.includes(t), t + ' fehlt: ' + alles);
});

await pruefe('P1: zu kurz -> hart', async () => {
  const kurz = await lauf(P1, { scriptText: 'Cursor pricing changed. My take, fine.' }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(kurz.p1.bestanden, false);
  assert.match(kurz.p1.hart[0], /words/);
});

await pruefe('Urteil: P1-Verstoss -> FIX mit P1-Anweisungen, P2 wird gespart', async () => {
  const p1 = await lauf(P1, { scriptText: 'Cursor changed pricing by 20%.' }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  const u = await lauf(URTEIL, p1, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(u.verdict, 'FIX');
  assert.match(u.revisionPrompt, /Spell out/);
});

await pruefe('Urteil: Gemini FIX ("new" falsch) -> FIX, Revision bekommt die Anweisung', async () => {
  const p1 = await lauf(P1, { title: ARTIKEL.title, scriptText: S980 }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  const g = gemini({ verdict: 'FIX', score: 6, problems: ['„new" ist falsch: Projekt seit Juli 2025'], fix: 'Replace "A new open source CLI" with "An open source CLI".', newer_version: '' });
  const u = await lauf(URTEIL, g, { 'P1: Regelcheck': p1, 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(u.verdict, 'FIX');
  assert.match(u.revisionPrompt, /Replace "A new open source CLI"/);
  assert.deepEqual(u.suchanfragen, ['claude-code-templates release date']);
});

await pruefe('Urteil: veraltetes Thema -> REJECT mit neuerer Version', async () => {
  const p1 = await lauf(P1, { title: 'Opus 4.8', scriptText: S980 }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  const g = gemini({ verdict: 'REJECT', score: 2, problems: ['Opus 4.8 ist überholt'], fix: '', newer_version: 'Claude Opus 5.5 (2026)' });
  const u = await lauf(URTEIL, g, { 'P1: Regelcheck': p1, 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(u.verdict, 'REJECT');
  assert.equal(u.newerVersion, 'Claude Opus 5.5 (2026)');
});

await pruefe('Urteil: PASS mit Score 6 -> FIX', async () => {
  const p1 = await lauf(P1, { scriptText: S980 }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  const u = await lauf(URTEIL, gemini({ verdict: 'PASS', score: 6, problems: [] }), { 'P1: Regelcheck': p1, 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(u.verdict, 'FIX');
});

await pruefe('Urteil: Gemini-Fehler -> PASS mit Warnung (Pipeline stirbt nicht still)', async () => {
  const p1 = await lauf(P1, { scriptText: S980 }, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
  const u = await lauf(URTEIL, { error: { message: 'model not found' } }, { 'P1: Regelcheck': p1, 'Normalisieren + Duplikat prüfen': ARTIKEL });
  assert.equal(u.verdict, 'PASS');
  assert.match(u.warnung, /model not found/);
});

await pruefe('Schleife: nach 2 Ueberarbeitungen weiter FIX -> REJECT', async () => {
  let item = { title: 't', scriptText: S980 };
  let u;
  for (let i = 0; i < 5; i++) {
    const p1 = await lauf(P1, item, { 'Normalisieren + Duplikat prüfen': ARTIKEL });
    u = await lauf(URTEIL, gemini({ verdict: 'FIX', score: 5, problems: ['x'], fix: 'y' }), { 'P1: Regelcheck': p1, 'Normalisieren + Duplikat prüfen': ARTIKEL });
    if (u.verdict !== 'FIX') break;
    item = await lauf(LESEN, { choices: [{ message: { content: S980 } }] }, { 'Urteil bilden': u });
  }
  assert.equal(u.verdict, 'REJECT');
  assert.equal(u.versuch, 2);
  assert.match(u.problems[0], /Nach 2/);
});

await pruefe('Ueberarbeitung lesen: Groq-Fehler behaelt altes Skript, Zaehler steigt', async () => {
  const r = await lauf(LESEN, { error: 'x' }, { 'Urteil bilden': { scriptText: 'alt', versuch: 1 } });
  assert.equal(r.scriptText, 'alt');
  assert.equal(r.versuch, 2);
});

console.log(`\n${ok} Tests bestanden`);
