// ---------------------------------------------------------------------------
// Ueberarbeitetes Skript lesen und zurueck in P1 geben (Schleife).
// Scheitert Groq, bleibt das alte Skript stehen - der Zaehler steigt trotzdem,
// die Schleife endet also spaetestens nach MAX_UEBERARBEITUNGEN mit REJECT.
// ---------------------------------------------------------------------------
const antwort = $input.first().json;
const vorher = $('Urteil bilden').item.json;
const roh = ((((antwort.choices || [])[0] || {}).message || {}).content) || '';
const neu = String(roh).replace(/<think>[\s\S]*?<\/think>/g, '').trim();

return [{ json: {
  title: vorher.title,
  link: vorher.link,
  incomingKeywords: vorher.incomingKeywords,
  isTopStory: vorher.isTopStory,
  scriptSuccess: true,
  scriptText: neu || vorher.scriptText,
  errorMessage: '',
  versuch: (vorher.versuch || 0) + 1,
  vorherigeProbleme: vorher.problems,
} }];
