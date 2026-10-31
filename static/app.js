"use strict";

const view = document.getElementById("view");
// user = angemeldet; ohne Anmeldung gibt es nur die Rezepte zum Lesen
// publicRecipes/publicShopping: was Besucher ohne Anmeldung sehen dürfen (Einstellungen → Öffentlich)
const state = { user: false, publicRecipes: false, publicShopping: false, recipes: [], shopping: [], categories: [], q: "", cat: "", ai: { providers: [], default: null }, draft: null, images: [], shopOffers: {}, shopOffersFor: "" };

const CART = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 4h2.2l2.3 10.5a1.5 1.5 0 0 0 1.5 1.2h8.4a1.5 1.5 0 0 0 1.5-1.1L21 8H6.3"/><circle cx="9.5" cy="19.5" r="1.3"/><circle cx="17" cy="19.5" r="1.3"/></svg>';
// Unsplash verlangt bei der Namensnennung Links mit diesen utm-Angaben
const UNSPLASH = "https://unsplash.com/?utm_source=rezepte&utm_medium=referral";
const CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7"/></svg>';

// --- Hilfen ----------------------------------------------------------------------
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

async function api(path, method = "GET", data) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (data !== undefined) opts.body = JSON.stringify(data);
  let resp;
  try {
    resp = await fetch("/api" + path, opts);
  } catch (e) {
    throw new Error("Keine Verbindung zum Server.");
  }
  if (resp.status === 401) {
    // Beim Lesen einfach zur Anmeldung. Beim Speichern NICHT wegspringen, sonst ist das Getippte weg.
    if (method === "GET") location.href = "/login";
    throw new Error("Du bist abgemeldet. Bitte in einem neuen Tab anmelden und dann hier nochmal versuchen.");
  }
  const json = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(json.error || `Fehler ${resp.status}`);
  return json;
}

let noteTimer;
function note(text, isError = false) {
  const el = document.getElementById("note");
  el.textContent = text;
  el.classList.toggle("is-error", isError);
  el.classList.add("show");
  clearTimeout(noteTimer);
  noteTimer = setTimeout(() => el.classList.remove("show"), isError ? 5000 : 2600);
}
const noteError = (err) => note(err.message, true);
const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

// --- Mengen lesen und schreiben ----------------------------------------------------
const UNITS = {
  g: "g", gr: "g", gramm: "g", kg: "kg", kilo: "kg", ml: "ml", cl: "cl", l: "l", liter: "l",
  el: "EL", esslöffel: "EL", tl: "TL", teelöffel: "TL", prise: "Prise", prisen: "Prise",
  msp: "Msp.", messerspitze: "Msp.", bund: "Bund", dose: "Dose", dosen: "Dose",
  pck: "Pck.", päckchen: "Pck.", packung: "Pck.", pkg: "Pck.", zehe: "Zehe", zehen: "Zehe",
  scheibe: "Scheibe", scheiben: "Scheibe", becher: "Becher", tasse: "Tasse", tassen: "Tasse",
  glas: "Glas", gläser: "Glas", zweig: "Zweig", zweige: "Zweig", würfel: "Würfel", stk: "Stk.", stück: "Stk.",
  handvoll: "Handvoll", schuss: "Schuss", blatt: "Blatt", kopf: "Kopf",
};
const GLYPHS = { "½": 0.5, "¼": 0.25, "¾": 0.75, "⅓": 1 / 3, "⅔": 2 / 3, "⅛": 0.125 };
// Menge am Zeilenanfang; Gruppe 2 ist ein Bereich wie "2-3"
const AMOUNT = /^(\d+\s+\d+\/\d+|\d+\/\d+|\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?\s*[½¼¾⅓⅔⅛]?|[½¼¾⅓⅔⅛])(\s*[-–]\s*\d+(?:[.,]\d+)?)?/;
// "Mehl Type 405", "Eier Größe M 2": die Zahl gehört zum Namen, ist keine Menge
const NO_AMOUNT_BEFORE = /(?:^|\s)(type|typ|größe|gr\.?|nr\.?|nummer|stufe)$/i;
// Mehrzahl für die Anzeige ("2 Dosen" statt "2 Dose")
const PLURAL_UNITS = { "Dose": "Dosen", "Zehe": "Zehen", "Tasse": "Tassen", "Scheibe": "Scheiben", "Prise": "Prisen", "Zweig": "Zweige", "Glas": "Gläser" };

function toNumber(s) {
  s = s.trim();
  let total = 0;
  for (const [g, v] of Object.entries(GLYPHS)) if (s.includes(g)) { total += v; s = s.replace(g, "").trim(); }
  for (const part of s.split(/\s+/).filter(Boolean)) {
    if (part.includes("/")) { const [a, b] = part.split("/").map(Number); if (b) total += a / b; }
    else if (/^\d{1,3}(\.\d{3})+$/.test(part)) total += Number(part.replace(/\./g, ""));  // "1.000" = tausend
    else total += parseFloat(part.replace(",", ".")) || 0;
  }
  return total || null;
}

const unitOf = (word) => UNITS[(word || "").toLowerCase().replace(/\.$/, "")];

/** "250 g Linsen", "1/2 TL Salz", "Milch 1 l", "Salz" → {amount, unit, name} */
function parseLine(line) {
  let rest = line.trim().replace(/^[-*•·]\s*/, "");
  if (!rest) return null;
  let amount = null, unit = "";
  const m = rest.match(AMOUNT);
  if (m && m[2]) {
    // Bereich wie "2-3 Äpfel": bleibt als Text stehen, damit nichts verloren geht
    return { amount: null, unit: "", name: rest };
  }
  if (m) {
    amount = toNumber(m[1]);
    rest = rest.slice(m[0].length).trim();
    const [first, ...more] = rest.split(/\s+/);
    if (unitOf(first) && more.length) { unit = unitOf(first); rest = more.join(" "); }
  } else {
    // Menge hinten: "Milch 1 l" – ohne Einheit nur kleine Zahlen ("Eier 6"), nie nach "Type"/"Größe"
    const t = rest.match(/^(.*\S)\s+(\d+(?:[.,]\d+)?)\s*([^\s\d]+)?$/);
    const n = t ? toNumber(t[2]) : null;
    if (t && !NO_AMOUNT_BEFORE.test(t[1]) && (t[3] ? unitOf(t[3]) : n <= 50)) {
      rest = t[1]; amount = n; unit = t[3] ? unitOf(t[3]) : "";
    }
  }
  return rest ? { amount, unit, name: rest } : null;
}

function fmtNumber(n) {
  if (n < 0.1) return String(Math.round(n * 100) / 100).replace(".", ",");
  const whole = Math.floor(n), frac = n - whole;
  for (const [g, v] of Object.entries(GLYPHS)) {
    if (Math.abs(frac - v) < 0.04) return (whole ? whole + " " : "") + g;
  }
  if (frac < 0.04) return String(whole);
  if (frac > 0.96) return String(whole + 1);
  return n.toFixed(1).replace(".", ",");
}

/** Für die Anzeige: gerundet, große Gramm-/ml-Zahlen als kg/l. */
function fmtAmount(amount, unit) {
  if (amount == null) return unit || "";
  // erst runden, dann umrechnen – sonst wird aus 999 g "1000 g" statt "1 kg"
  if ((unit === "g" || unit === "ml") && amount >= 100) amount = Math.round(amount / 5) * 5;
  else if (amount >= 10) amount = Math.round(amount);
  if (unit === "g" && amount >= 1000) { amount /= 1000; unit = "kg"; }
  if (unit === "ml" && amount >= 1000) { amount /= 1000; unit = "l"; }
  if (amount > 1 && PLURAL_UNITS[unit]) unit = PLURAL_UNITS[unit];
  return (fmtNumber(amount) + " " + unit).trim();
}

/** Für das Formular: zurück in eine Zeile, die parseLine wieder genauso liest. */
function toLine(i) {
  const amt = i.amount == null ? "" : String(Math.round(i.amount * 100) / 100).replace(".", ",");
  return [amt, i.unit, i.name].filter(Boolean).join(" ");
}

// --- Kategorien ---------------------------------------------------------------------
const CAT_COLORS = {
  "Frühstück": "#ffd23a", "Suppen": "#ff7a00", "Salate": "#3fae49", "Hauptgerichte": "#e8312f",
  "Beilagen": "#c2621a", "Snacks": "#2f7fe0", "Kuchen & Süßes": "#7d47c9", "Getränke": "#38bdf8",
};
const catColor = (c) => CAT_COLORS[c] || "#9c9c9c";
const OTHER = "Sonstiges";

// --- Fleisch und Fisch nach Tier ------------------------------------------------------
// Für Suche und Tier-Reiter: Schweinebauch, Speck und Spam zählen alle zum Schwein.
// "specific" nennt das Tier selbst; "generic" (Schnitzel, Hack, Steak …) gilt nur, wenn kein Tier dasteht –
// "Putenschnitzel" ist Pute, nur "Schnitzel" ist Schwein. Kalb gehört zum Rind.
const ANIMALS = [
  { id: "Schwein", alias: ["schwein", "schweinefleisch", "pork"],
    specific: /schwein|speck|bacon|schinken|kass?eler|kassler|salami|chorizo|\bmett|pancetta|guanciale|prosciutto|pork|rippchen|spare ?ribs|eisbein|leberkäse|cabanossi|\bspam\b|luncheon/,
    generic: /schnitzel|kotelett|wurst|würstchen|\bhack/ },
  { id: "Rind", alias: ["rind", "rindfleisch", "kalb", "kalbfleisch", "beef"],
    specific: /rind(?!e(?!r))|kalb|beef|ochse|tafelspitz|bresaola/, generic: /steak|entrec[oô]te|ribeye|\bhack/ },
  { id: "Hähnchen", alias: ["hähnchen", "haehnchen", "huhn", "hühnchen", "chicken", "hendl"],
    specific: /huhn|hühn|hähnchen|haehnchen|chicken|poulet|hendl/, generic: /geflügel/ },
  { id: "Pute", alias: ["pute", "truthahn", "turkey"],
    specific: /pute|truthahn|turkey/ },
  { id: "Ente", alias: ["ente", "gans"], specific: /\bente|enten|gans\b|gänse|duck/ },
  { id: "Lamm", alias: ["lamm", "schaf", "lammfleisch"], specific: /lamm|schaf|hammel|lamb/ },
  { id: "Wild", alias: ["wild", "hirsch", "reh"], specific: /hirsch|\breh|kaninchen|hase(?!l)|wildschwein/ },
  { id: "Fisch", alias: ["fisch", "meeresfrüchte", "fish", "seafood"],
    specific: /fisch|lachs|kabeljau|dorsch|forelle|hering|makrele|sardine|sardelle|anchovi|garnele|shrimp|scampi|krabbe|muschel|calamar|oktopus|surimi|tuna|salmon|zander|scholle|seeteufel/ },
];
// Brühen und Soßen sind kein Fleisch ("Hühnerbrühe", "Fischsauce"), Butterschmalz auch nicht
const NOT_MEAT = /brühe|bouillon|fond\b|brühwürfel|fischsauce|fischsoße|austernsauce|gelatine|schmalz/;
// Zusatz nur fürs Rezept – "Salz (für den Lachs)" ist kein Fisch
const USE_NOTE = /\s*\((?:für|zum|zur|als)\b[^)]*\)\s*$/i;

function animalsOfName(name) {
  const n = name.replace(USE_NOTE, "").toLowerCase();
  if (NOT_MEAT.test(n)) return [];
  const found = ANIMALS.filter((a) => a.specific.test(n));
  return found.length ? found : ANIMALS.filter((a) => a.generic && a.generic.test(n));
}

const animalCache = new WeakMap();
/** Tiere eines Rezepts (Menge der ids), wird pro geladenem Rezept einmal ausgerechnet. */
function animalsOf(r) {
  if (!animalCache.has(r)) animalCache.set(r, new Set(r.ingredients.flatMap((i) => animalsOfName(i.name)).map((a) => a.id)));
  return animalCache.get(r);
}
const animalForQuery = (q) => ANIMALS.find((a) => a.alias.includes(q) || a.id.toLowerCase() === q);

// --- Kalorien, grob geschätzt -----------------------------------------------------------
// kcal pro 100 g und was 1 Stück ("") bzw. 1 EL ungefähr wiegt (TL = ein Drittel EL), ml = Gramm pro ml.
// Die erste passende Zeile gilt – darum "Sesamöl" vor "Sesam", "Reisessig" vor "Reis", "Zuckerschoten" vor "Zucker".
const K = (re, k, g = {}) => ({ re, k, g });
const KCAL = [
  K(/wasser(?!melone)|eiswürfel|salz|pfeffer(?!minz)|zimt|chili ?(flocken|pulver)|paprikapulver|kreuzkümmel|gewürz|backpulver|natron|vanille|petersilie|schnittlauch|koriander|basilikum|minze|dill|thymian|rosmarin|lorbeer|grüntee|teebeutel|tee-beutel|dashi|hondashi/, 0),
  K(/brühe|bouillon|fond\b/, 5, { würfel: 500 }),  // ein Würfel ergibt etwa einen halben Liter
  K(/curry.?roux|curry.?block|curry.?würfel/, 510, { "": 20, würfel: 20 }),
  K(/currypulver|curry pulver/, 325, { el: 6 }),
  K(/öl(?![a-zäöüß])/, 884, { el: 12, ml: 0.92 }),
  K(/schmalz/, 900, { el: 13 }),
  K(/butternut|kürbis/, 40, { "": 1000 }),
  K(/buttermilch/, 38),
  K(/butter/, 740, { el: 15 }),
  K(/mayo/, 680, { el: 14 }),
  K(/soja ?sauce|soja ?soße|sojasosse|shoyu|tamari/, 55, { el: 16 }),
  K(/fisch ?(sauce|soße)/, 35),
  K(/austern ?(sauce|soße)/, 120, { el: 18 }),
  K(/worcester/, 80),
  K(/ketchup/, 100, { el: 17 }),
  K(/teriyaki/, 90, { el: 18 }),
  K(/tonkatsu ?(sauce|soße)|bulldog|okonomi|bbq|barbecue/, 130, { el: 18 }),
  K(/sriracha|chilisauce|chilisoße|gochujang/, 150, { el: 17 }),
  K(/essig/, 20),
  K(/senf/, 90),
  K(/miso/, 200, { el: 18 }),
  K(/mirin/, 240),
  K(/sake|(?<!sch)wein(?!stein)/, 80),  // nicht "Schweinelende"
  K(/honig/, 304, { el: 21 }),
  K(/ahornsirup|agavendicksaft|sirup/, 260, { el: 20 }),
  K(/zuckerschote/, 42, { "": 4 }),
  K(/zucker/, 400, { el: 13 }),
  K(/stärke|kartoffelmehl/, 350, { el: 10 }),
  K(/panko|paniermehl|brösel/, 370, { el: 5, ml: 0.25 }),
  K(/haferflocken/, 370, { el: 8 }),
  K(/reisnudel|glasnudel/, 350),
  K(/udon/, 130, { "": 200, "pck.": 200 }),  // vorgekocht aus der Packung
  K(/soba/, 340, { bund: 100 }),
  K(/ramen|nudel|spaghetti|pasta|makkaroni/, 350, { bund: 100 }),
  K(/mehl/, 350, { el: 10 }),
  Object.assign(K(/(?<!p)reis/, 350, { "": 150, becher: 150, tasse: 150, el: 12, ml: 0.85 }), { rice: 130 }),
  K(/toast|brot|brötchen|semmel/, 260, { "": 60, scheibe: 30 }),
  K(/eigelb/, 320, { "": 18 }),
  K(/eiweiß|eiklar/, 50, { "": 33 }),
  K(/(^|[^a-zäöüß])ei(er)?([^a-zäöüß]|$)/, 150, { "": 55 }),
  K(/milch/, 64),
  K(/sahne/, 300),
  K(/schmand|crème fraîche|creme fraiche|saure sahne/, 250),
  K(/skyr|magerquark/, 65),
  K(/joghurt|quark/, 90),
  K(/parmesan/, 400, { el: 6 }),
  K(/mozzarella/, 250, { "": 125 }),
  K(/käse|cheddar|gouda/, 350, { scheibe: 20 }),
  K(/seidentofu/, 55, { "": 300 }),
  K(/tofu/, 120, { "": 400 }),
  K(/schweinebauch|bauchfleisch/, 500),
  K(/speck|bacon|pancetta/, 450, { scheibe: 15 }),
  K(/schinken/, 120, { scheibe: 15 }),
  K(/spam|luncheon|frühstücksfleisch/, 300, { scheibe: 30 }),
  K(/salami|chorizo/, 400, { scheibe: 5 }),
  K(/wurst|würstchen/, 280, { "": 50 }),
  K(/hack.*(huhn|hühn|hähnchen|pute|geflügel)|(huhn|hühn|hähnchen|pute|geflügel).*hack/, 150),
  K(/hack(?!t)|\bmett/, 250),  // nicht "Walnüsse, gehackt"
  K(/schwein|kotelett|schnitzel/, 170, { "": 120 }),
  K(/kalb/, 120, { "": 150 }),
  K(/rind|steak|beef|ribeye|entrec/, 200, { "": 200 }),
  K(/brust/, 110, { "": 200 }),  // Hähnchen- oder Putenbrust
  K(/schenkel|keule/, 180, { "": 150 }),
  K(/huhn|hühn|hähnchen|chicken|geflügel|pute|truthahn/, 150, { "": 150 }),
  K(/\bente|enten|gans\b|gänse/, 230, { "": 200 }),
  K(/lamm|schaf/, 250, { "": 150 }),
  K(/lachs/, 200, { "": 125, scheibe: 20 }),
  K(/makrele|hering|sardine/, 200, { "": 120 }),
  K(/thunfisch|tuna/, 130, { dose: 150 }),
  K(/garnele|shrimp|scampi|krabbe|gamba/, 80, { "": 15 }),
  K(/surimi|krebsfleisch|kanikama/, 95, { "": 17 }),
  K(/fisch|kabeljau|dorsch|seelachs|zander|scholle|forelle|tilapia|pangasius/, 90, { "": 150 }),
  K(/süßkartoffel/, 86, { "": 250 }),
  K(/kartoffel/, 77, { "": 150 }),
  K(/karotte|möhre/, 36, { "": 80 }),
  K(/frühlingszwiebel|lauchzwiebel/, 30, { "": 15, bund: 100 }),
  K(/zwiebel|schalotte/, 40, { "": 100 }),
  K(/lauch|porree/, 30, { "": 200 }),
  K(/knoblauch/, 140, { "": 4, el: 12 }),
  K(/ingwer/, 80, { "": 10 }),
  K(/kohl|wirsing/, 25, { "": 800 }),
  K(/rettich|daikon/, 18, { "": 400 }),
  K(/radieschen/, 16, { "": 10, bund: 150 }),
  K(/babygurke|mini-?gurke|snackgurke/, 14, { "": 80 }),
  K(/gurke/, 14, { "": 300 }),
  K(/tomate/, 18, { "": 100, dose: 400 }),
  K(/aubergine/, 25, { "": 300 }),
  K(/zucchini/, 20, { "": 250 }),
  K(/paprika/, 30, { "": 160 }),
  K(/pilz|champignon|shiitake|enoki|shimeji/, 25, { "": 15 }),
  K(/spinat/, 23),
  K(/sprossen/, 30),
  K(/grüne bohnen|brechbohnen|stangenbohnen|buschbohnen/, 30),
  K(/bohnen|erbsen|edamame/, 60),
  K(/mais/, 85, { dose: 285 }),
  K(/brokkoli|blumenkohl/, 30, { "": 400 }),
  K(/avocado/, 160, { "": 150 }),
  K(/salat/, 15, { "": 10 }),
  K(/zitronensaft|limettensaft/, 25),
  K(/zitrone|limette/, 30, { "": 80 }),
  K(/chili/, 40, { "": 10 }),
  K(/nori/, 300, { "": 3 }),
  K(/wakame|algen/, 200, { el: 3 }),
  K(/beere|erdbeer|himbeer|heidelbeer/, 45),
  K(/apfel/, 52, { "": 180 }),
  K(/banane/, 90, { "": 120 }),
  K(/sesam/, 570, { el: 9 }),
  K(/erdnussbutter|erdnussmus/, 600, { el: 16 }),
  K(/walnüss|walnuss|mandel|haselnüss|haselnuss|erdnüss|erdnuss|cashew|nüsse/, 620, { el: 8 }),
  K(/leinsamen|chiasamen|chia/, 500, { el: 10 }),
  K(/kakao/, 350, { el: 8 }),
  K(/schokolade/, 540),
  K(/gelatine/, 340, { "": 1.7 }),
];
const UNIT_G = { g: 1, kg: 1000, ml: 1, cl: 10, l: 1000, el: 15, prise: 0.4, msp: 0.3, "msp.": 0.3,
  zehe: 4, scheibe: 25, dose: 250, becher: 150, tasse: 150, bund: 30, "pck.": 200, würfel: 10 };
const VOLUME = { ml: 1, cl: 10, l: 1000 };
const SPRINKLE = /nori|algen|sesam(?!öl)|mandelblättchen|topping|garnier/;

/** Grob die Kalorien eines Rezepts: gesamt, pro Person (wenn die Personenzahl bekannt ist) und was fehlt. */
const kcalCache = new WeakMap();
function kcalOf(r) {
  if (kcalCache.has(r)) return kcalCache.get(r);
  let total = 0, counted = 0;
  const missing = new Set();
  for (const i of r.ingredients) {
    const name = i.name.replace(USE_NOTE, ""), n = name.toLowerCase();
    const e = KCAL.find((x) => x.re.test(n));
    if (e && e.k === 0) continue;               // Wasser, Salz, Gewürze, Kräuter
    // ohne Menge und kaum der Rede wert: "Salat nach Wahl", Ingwer, Sesam oder Nori zum Bestreuen
    if (e && i.amount == null && (e.k < 100 || SPRINKLE.test(n))) continue;
    if (!e || i.amount == null) { missing.add(name); continue; }
    let u = i.unit.toLowerCase();
    if (["stk.", "stk", "stück"].includes(u)) u = "";
    let grams = null;
    if (VOLUME[u]) grams = i.amount * VOLUME[u] * (e.g.ml ?? 1);
    else if (e.g[u] != null) grams = i.amount * e.g[u];
    else if (u === "tl") grams = i.amount * (e.g.el ?? UNIT_G.el) / 3;
    else if (u === "" && /(\d+(?:[.,]\d+)?)\s*g\b/.test(n)) grams = i.amount * parseFloat(n.match(/(\d+(?:[.,]\d+)?)\s*g\b/)[1].replace(",", "."));
    else if (UNIT_G[u] != null) grams = i.amount * UNIT_G[u];
    if (grams == null) { missing.add(name); continue; }
    let k = e.k;
    // Reis: in Gramm meist schon gekocht (200 g für eine Schüssel), Becher/EL/ml meinen rohen Reis
    if (e.rice && !/roh|ungekocht|trocken/.test(n)
        && (/gekocht|warm|gedämpft/.test(n) || u === "" || (u === "g" && grams / (r.servings || 1) >= 120))) k = e.rice;
    total += grams * k / 100;
    counted++;
  }
  const res = counted && total >= 20
    ? { total, perServing: r.servings ? total / r.servings : null, missing: [...missing] } : null;
  kcalCache.set(r, res);
  return res;
}
const fmtKcal = (k) => "≈ " + (Math.round(k / 10) * 10).toLocaleString("de-DE");

const ICONS = {
  pen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 20h4L19 9l-4-4L4 16z"/><path d="m13 7 4 4"/></svg>',
  image: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="2"/><path d="m21 17-5-5-9 8"/></svg>',
  text: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M5 6h14M5 10h14M5 14h10M5 18h7"/></svg>',
  bulb: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.5 1 1.2 1 2.1h5c0-.9.4-1.6 1-2.1A6 6 0 0 0 12 3z"/></svg>',
};

// --- Daten ------------------------------------------------------------------------
async function loadRecipes() { state.recipes = await api("/recipes"); }
async function loadShopping() { setShopping(await api("/shopping")); }

function setShopping(items) {
  state.shopping = items;
  renderCartbar();
}

function renderCartbar() {
  const bar = document.getElementById("cartbar");
  const items = state.shopping.filter((i) => !i.checked), open = items.length;
  // Nicht auf dem Zettel selbst, nicht in Formularen (dort stört sie beim Tippen) und nicht bei leerem Zettel
  const page = location.hash.replace(/^#\/?/, "").split("/")[0];
  bar.hidden = !(state.user || state.publicShopping) || !open || ["einkauf", "neu", "bearbeiten", "einstellungen"].includes(page);
  const peek = items.slice(0, 5).map((i) => i.name).join(", ") + (open > 5 ? " …" : "");
  bar.innerHTML = `<span class="cb-n">${open}</span><span class="txt"><b>Auf dem Zettel</b><small>${esc(peek)}</small></span><span class="go">${CART}<span>Öffnen</span></span>`;
}

async function addToCart(id, servings, button) {
  if (button) {
    if (button.disabled) return;  // Doppeltipp: sonst landet alles zweimal auf dem Zettel
    button.disabled = true;
  }
  try {
    const res = await api(`/recipes/${id}/cart`, "POST", { servings });
    setShopping(res.items);
    const bar = document.getElementById("cartbar");
    bar.classList.remove("bump"); void bar.offsetWidth; bar.classList.add("bump");
    const r = state.recipes.find((x) => x.id === id);
    note(`${plural(res.added, "Zutat", "Zutaten")} von „${r ? r.title : "Rezept"}“ auf dem Zettel.`);
    // Gleich nachschauen, was davon gerade im Angebot ist (läuft weiter, auch wenn man wegklickt)
    loadShopOffers().then((map) => {
      state.shopOffers = map;
      const fresh = res.items.filter((i) => !i.checked && i.sources.includes(r?.title) && map[i.id]).length;
      if (fresh) note(`${plural(fresh, "Zutat", "Zutaten")} davon gerade im Angebot – steht auf dem Zettel.`);
    }).catch(() => {});
    if (button) {
      const icon = button.querySelector("svg");
      if (icon) icon.outerHTML = CHECK;
      button.classList.add("done");
      await new Promise((ok) => setTimeout(ok, 1200));
      const svg = button.querySelector("svg");
      if (svg) svg.outerHTML = CART;
      button.classList.remove("done");
    }
  } catch (err) {
    noteError(err);
  } finally {
    if (button) button.disabled = false;
  }
}

// --- Supermarkt-Angebote (Marktguru + Globus, wie beim Angebote-Bot) -------------------
const euro = (n) => n == null ? "" : n.toFixed(2).replace(".", ",") + " €";
const SHOP_NAMES = { "netto marken-discount": "Netto", "aldi süd": "Aldi Süd", "aldi nord": "Aldi Nord", "globus": "Globus", "penny": "Penny", "rewe": "Rewe", "edeka": "Edeka" };
const shopName = (s) => SHOP_NAMES[s.toLowerCase()] || s;

/** "bis Sa 31.10." – oder "ab Mo 02.11.", wenn das Angebot erst noch kommt */
function dealWhen(o) {
  const day = (iso) => new Date(iso).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }).replace(",", "");
  if (o.from && new Date(o.from) > new Date()) return "ab " + day(o.from);
  return o.to ? "bis " + day(o.to) : "";
}

function dealHtml(o) {
  return `<li><span class="d-shop">${esc(shopName(o.retailer))}</span>
    <span class="d-what">${esc(o.title)}<small>${[o.unit_price, dealWhen(o), o.card ? "mit Kundenkarte" : ""].filter(Boolean).map(esc).join(" · ")}</small></span>
    <span class="d-price">${esc(euro(o.price))}${o.old_price && o.old_price > o.price ? `<s>${esc(euro(o.old_price))}</s>` : ""}</span></li>`;
}

/** Kleiner Hinweis hinter einer Zutat: das erste Angebot passt am besten (der Server sortiert so),
 *  das billigste ist oft etwas anderes ("Vanillin-Zucker" zu Zucker) */
function dealTag(offers) {
  if (!offers?.length) return "";
  const best = offers[0];
  return `<span class="deal-tag" title="${esc(best.title)}">${esc(shopName(best.retailer))} ${esc(euro(best.price))}</span>`;
}

const globusNote = (res) => res.globus === "lädt" ? "Globus-Prospekt wird noch geladen, gleich nochmal reinschauen." :
  res.globus === "fehler" ? "Globus gerade nicht erreichbar." : "";

/** Angebote zu allem Offenen auf dem Zettel, gemerkt nach Zettel-ID */
async function loadShopOffers() {
  const res = await api("/shopping/angebote");
  const map = {};
  for (const it of res.items) if (it.offers.length) map[it.id] = it.offers;
  // Globus noch nicht da: beim nächsten Öffnen des Zettels nochmal fragen
  if (res.globus === "lädt") state.shopOffersFor = "";
  return map;
}

function picHtml(r, size = "small") {
  if (r.image) return `<img src="${esc(r.image[size])}" alt="" loading="lazy">`;
  return `<span class="ph" style="--c:${catColor(r.category)}"><span><b>${esc((r.title[0] || "?").toUpperCase())}</b>${esc(r.category || "kein Foto")}</span></span>`;
}

// --- Ansicht: Rezepte ---------------------------------------------------------------
// Eine Rezept-Kachel ist ein Manga-Panel ("Koma"): dicker Rahmen, Foto als Rasterdruck, Zeit im Stern.
function cardHtml(r, i, big) {
  const kcal = kcalOf(r);
  return `
    <article class="koma${big ? " big" : ""}" style="--c:${catColor(r.category)}">
      <div class="pic">${picHtml(r)}</div>
      ${r.minutes ? `<span class="burst"><b>${r.minutes}</b>Min</span>` : ""}
      <div class="cap">
        <span class="ep">第${i + 1}話${r.category ? ` · ${esc(r.category)}` : ""}</span>
        <a class="name" href="#/r/${r.id}">${esc(r.title)}</a>
        ${kcal?.perServing ? `<span class="kc" title="grob geschätzt">${fmtKcal(kcal.perServing)} kcal p. P.</span>` : ""}
      </div>
      ${state.user ? `<button class="add" data-cart="${r.id}" title="Zutaten auf den Zettel" aria-label="Zutaten von ${esc(r.title)} auf den Zettel">${CART}</button>` : ""}
    </article>`;
}

function renderRecipes() {
  const all = state.recipes;
  if (!all.length) {
    return `<div class="empty"><span class="sfx">シーン…</span><span class="empty-h">Noch leer</span><p>Noch keine Rezepte eingetragen.</p>
      ${state.user ? `<a class="btn btn--red" href="#/neu">Erstes Rezept eintragen</a>` : ""}</div>`;
  }
  const q = state.q.trim().toLowerCase();
  // "schwein", "rind", "fisch" …: alle Rezepte mit Fleisch von diesem Tier, egal welches Stück
  const animal = animalForQuery(q);
  const hits = all.filter((r) => (!state.cat || (r.category || OTHER) === state.cat)
    && (!q || (animal && animalsOf(r).has(animal.id))
        || r.title.toLowerCase().includes(q) || r.ingredients.some((i) => i.name.toLowerCase().includes(q))));
  const order = [...state.categories, OTHER];

  // Ein Kapitel pro Kategorie. Kapitel fließen nebeneinander, so breit wie ihre Rezepte (--n),
  // die Panels darin wachsen, bis die Reihe voll ist – wie auf einer Manga-Seite.
  const chapter = (no, head, color, list) => `
    <section class="chapter" style="--c:${color};--n:${Math.min(list.length, 4)}">
      <h2 class="ch-h"><span class="ch-no">${no}</span><span class="ch-t">${head}</span><span class="ch-n">×${list.length}</span></h2>
      <div class="komas${list.length >= 3 ? " has-big" : ""}">${list.map((r, i) => cardHtml(r, i, i === 0 && list.length >= 3)).join("")}</div>
    </section>`;
  if (!q && !state.cat) {
    return order.map((c) => [c, all.filter((r) => (r.category || OTHER) === c)])
      .filter(([, l]) => l.length).map(([c, list], k) => chapter(`第${k + 1}章`, esc(c), catColor(c), list)).join("");
  }
  if (!hits.length) {
    return `<div class="empty"><span class="sfx">シーン…</span><span class="empty-h">Nix gefunden</span><p>Kein Rezept passt zu deiner Auswahl.</p></div>`;
  }
  if (animal) return chapter(animal.id === "Fisch" ? "Fisch" : "Fleisch", esc(animal.id) + (state.cat ? ` · ${esc(state.cat)}` : ""), "var(--red)", hits);
  return q ? chapter("検索", `„${esc(state.q.trim())}“`, "var(--red)", hits) : chapter("第" + (order.indexOf(state.cat) + 1) + "章", esc(state.cat), catColor(state.cat), hits);
}

/** Reiter "Fleisch & Fisch": je Tier, wie viele Rezepte es gibt (alle Stücke zusammengezählt). */
function meatsHtml() {
  const counts = ANIMALS.map((a) => [a, state.recipes.filter((r) => animalsOf(r).has(a.id)).length]).filter(([, n]) => n);
  if (!counts.length) return "";
  const active = animalForQuery(state.q.trim().toLowerCase());
  return `<div class="meats" id="meats"><span class="meats-k">Fleisch &amp; Fisch</span>
    ${counts.map(([a, n]) => `<button class="meat" data-animal="${esc(a.id)}" aria-pressed="${active === a}" title="Alle Rezepte mit ${esc(a.id)}">
      <span class="nm">${esc(a.id)}</span><span class="ct">${n}</span></button>`).join("")}
  </div>`;
}

function showList() {
  const used = new Set(state.recipes.map((r) => r.category || OTHER));
  const cats = [...state.categories, OTHER].filter((c) => used.has(c));
  // Gewählte Kategorie gibt es nicht mehr (z. B. letztes Rezept gelöscht) → zurück zu "Alle"
  if (state.cat && (!cats.includes(state.cat) || cats.length < 2)) state.cat = "";
  const count = (c) => state.recipes.filter((r) => (r.category || OTHER) === c).length;
  const row = (c, label, n, color) => `<button class="catrow" data-cat="${esc(c)}" aria-pressed="${state.cat === c}" style="--c:${color}">
    <span class="nm">${label}</span><span class="ct">${n}</span></button>`;
  const n = state.recipes.length;
  view.innerHTML = `
    <div class="floor">
      <div class="cover">
        <p class="cover-h" aria-hidden="true">Was gibt's<br>heute?!</p>
        <div class="cover-side">
          <label class="find"><span class="find-k">Suche</span><input type="search" id="q" placeholder="Name oder Zutat, z. B. Kürbis" autocomplete="off" value="${esc(state.q)}" aria-label="Rezepte suchen"></label>
          <div class="cover-row">
            <p class="tally"><b>${n}</b> ${n === 1 ? "Rezept" : "Rezepte"} · ${plural(cats.length, "Kapitel", "Kapitel")}</p>
            ${state.user ? `<a class="btn btn--red" href="#/neu">+ Neues Rezept</a>` : ""}
          </div>
        </div>
      </div>
      ${cats.length > 1 ? `<div class="cats" id="cats">
        ${row("", "Alles", n, "var(--ink)")}
        ${cats.map((c) => row(c, esc(c), count(c), catColor(c))).join("")}
      </div>` : ""}
      ${meatsHtml()}
      <div id="list">${renderRecipes()}</div>
    </div>`;
  const q = document.getElementById("q");
  const syncMeats = () => {
    const active = animalForQuery(state.q.trim().toLowerCase());
    document.querySelectorAll("#meats .meat").forEach((b) => b.setAttribute("aria-pressed", String(active?.id === b.dataset.animal)));
  };
  q.addEventListener("input", () => { state.q = q.value; syncMeats(); document.getElementById("list").innerHTML = renderRecipes(); });
  // Tier antippen = danach suchen; nochmal antippen = Suche leeren
  document.getElementById("meats")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-animal]");
    if (!btn) return;
    state.q = btn.getAttribute("aria-pressed") === "true" ? "" : btn.dataset.animal;
    q.value = state.q;
    syncMeats();
    document.getElementById("list").innerHTML = renderRecipes();
  });
  document.getElementById("cats")?.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-cat]");
    if (!btn) return;
    state.cat = btn.dataset.cat;
    document.querySelectorAll("#cats .catrow").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
    document.getElementById("list").innerHTML = renderRecipes();
  });
}

// --- Ansicht: Rezept ----------------------------------------------------------------
function showRecipe(id) {
  const r = state.recipes.find((x) => x.id === id);
  if (!r) { view.innerHTML = `<div class="empty"><span class="empty-h">Nicht da</span><p>Dieses Rezept gibt es nicht.</p><a class="btn" href="#/">Zu den Rezepten</a></div>`; return; }
  let servings = r.servings;
  let offers = [];  // je Zutat (gleiche Reihenfolge) die Angebote, sobald sie da sind
  const kcal = kcalOf(r);

  const bonHtml = () => r.ingredients.map((i, k) => {
    const amount = i.amount != null && servings && r.servings ? i.amount * servings / r.servings : i.amount;
    return `<li><span class="q">${esc(fmtAmount(amount, i.unit))}</span><span class="n">${esc(i.name)}${dealTag(offers[k])}</span></li>`;
  }).join("");

  view.innerHTML = `
    <a class="back" href="#/">← Zurück</a>
    <div class="rp-top">
      <figure class="koma photo"><div class="in">${r.image ? `<img src="${esc(r.image.large)}" alt="${esc(r.title)}">
        ${r.image.large.startsWith("/fotos/") ? "" : `<span class="credit">Foto von ${r.image.url ? `<a href="${esc(r.image.url)}" target="_blank" rel="noopener">${esc(r.image.by || "Unbekannt")}</a>` : esc(r.image.by || "Unbekannt")}
          auf <a href="${UNSPLASH}" target="_blank" rel="noopener">Unsplash</a></span>`}` : picHtml(r, "large")}</div></figure>
      <div class="koma title-koma"><div class="in">
        ${r.category ? `<span class="label" style="--c:${catColor(r.category)}">${esc(r.category)}</span>` : ""}
        <h1 class="r-title">${esc(r.title)}</h1>
        ${r.source ? `<p class="r-src">Quelle: ${esc(r.source)}</p>` : ""}
        <div class="facts">
          ${r.minutes ? `<span class="fact"><b>${r.minutes}</b>Minuten</span>` : ""}
          ${r.servings ? `<span class="fact"><button class="x" id="less" aria-label="Weniger Personen">−</button><b id="srv" aria-live="polite">${servings}</b><span id="srv-l">${servings === 1 ? "Person" : "Personen"}</span><button class="x" id="more" aria-label="Mehr Personen">+</button></span>` : ""}
          ${kcal ? `<span class="fact fact--kcal" title="grob aus den Zutaten geschätzt"><b>${fmtKcal(kcal.perServing ?? kcal.total)}</b>kcal ${kcal.perServing ? "pro Person" : "gesamt"}</span>` : ""}
        </div>
        ${state.user && (r.ingredients.length || state.partner) ? `<div class="r-btns">
          ${r.ingredients.length ? `<button class="btn btn--big btn--red" id="cart">${CART}Auf den Zettel</button>` : ""}
          ${state.partner ? `<a class="btn btn--big" id="cooked" href="${esc(state.partner)}/?gekocht=${encodeURIComponent(r.title)}"
            target="_blank" rel="noopener" title="Öffnet deinen Digimon-Partner">Gekocht! Partner füttern</a>` : ""}
        </div>` : ""}
      </div></div>
    </div>
    ${r.ingredients.length || r.steps.length || r.notes ? `<div class="rp-body">
      ${r.ingredients.length ? `<section class="koma ings-koma">
        <h2 class="box-h">Zutaten</h2>
        <p class="for" id="for">${servings ? `für ${servings} ${servings === 1 ? "Person" : "Personen"}` : "&nbsp;"}</p>
        <ul class="ings" id="bon">${bonHtml()}</ul>
        ${kcal ? `<p class="kcal-note">Kalorien grob aus den Zutaten geschätzt${kcal.missing.length
          ? ` – nicht mitgerechnet: ${esc(kcal.missing.join(", "))}` : ""}.</p>` : ""}
        ${state.user ? `<div class="deals" id="deals" aria-live="polite"><p class="deals-wait">Suche Angebote …</p></div>` : ""}
      </section>` : ""}
      <div class="rp-steps">
        ${r.steps.length ? `<h2 class="strip"><span>So geht's</span></h2>
          <ol class="steps">${r.steps.map((s, i) => `<li class="koma"><span class="num">${i + 1}</span><p>${esc(s)}</p></li>`).join("")}</ol>` : ""}
        ${r.notes ? `<section class="koma memo"><h3 class="box-h">Notiz</h3><p>${esc(r.notes)}</p></section>` : ""}
      </div>
    </div>` : `<div class="empty"><span class="sfx">シーン…</span><span class="empty-h">Noch leer</span><p>Für dieses Rezept fehlen Zutaten und Zubereitung.</p></div>`}
    ${state.user ? `<div class="foot">
      <a class="btn" href="#/bearbeiten/${r.id}">Bearbeiten</a>
      <button class="btn btn--danger" id="del">Löschen</button>
    </div>` : ""}`;

  const setServings = (n) => {
    servings = Math.max(1, Math.min(100, n));
    document.getElementById("srv").textContent = servings;
    document.getElementById("srv-l").textContent = servings === 1 ? "Person" : "Personen";
    const list = document.getElementById("bon");
    if (list) {
      list.innerHTML = bonHtml();
      document.getElementById("for").textContent = `für ${servings} ${servings === 1 ? "Person" : "Personen"}`;
    }
  };
  if (r.servings) {
    document.getElementById("less").onclick = () => setServings(servings - 1);
    document.getElementById("more").onclick = () => setServings(servings + 1);
  }
  if (!state.user) return;
  document.getElementById("cart")?.addEventListener("click", (e) => addToCart(r.id, servings, e.currentTarget));
  if (r.ingredients.length) showRecipeOffers(r, (found) => {
    offers = found;
    const list = document.getElementById("bon");
    if (list) list.innerHTML = bonHtml();
  });
  document.getElementById("del").onclick = async () => {
    if (!confirm(`„${r.title}“ wirklich löschen?`)) return;
    try {
      await api(`/recipes/${r.id}`, "DELETE");
      await loadRecipes();
      location.replace("#/");  // ersetzt den Verlaufseintrag: "Zurück" führt nicht ins gelöschte Rezept
    } catch (err) { noteError(err); }
  };
}

/** Angebote zum Rezept holen und unter die Zutaten schreiben; onFound bekommt sie je Zutat. */
async function showRecipeOffers(r, onFound) {
  const box = () => location.hash === `#/r/${r.id}` ? document.getElementById("deals") : null;  // inzwischen weggeklickt?
  let res;
  try {
    res = await api(`/recipes/${r.id}/angebote`);
  } catch (err) {
    if (box()) box().innerHTML = `<p class="deals-wait">Angebote gerade nicht abrufbar: ${esc(err.message)}</p>`;
    return;
  }
  if (!box()) return;
  onFound(res.items.map((it) => it.offers));
  const hits = res.items.filter((it) => it.offers.length);
  const extra = [res.error, globusNote(res)].filter(Boolean).map((t) => `<p class="deals-wait">${esc(t)}</p>`).join("");
  box().innerHTML = hits.length ? `
    <h3 class="box-h">Gerade im Angebot</h3>
    ${hits.map((it) => `<div class="deal-group"><b class="dg-n">${esc(it.name)}</b><ul class="deal-list">${it.offers.map(dealHtml).join("")}</ul></div>`).join("")}
    ${extra}` : `<p class="deals-wait">Keine Zutat gerade im Angebot.</p>${extra}`;
}

// --- Ansicht: Einkaufszettel --------------------------------------------------------
// Der Server rechnet Löffel in TL zusammen; ab 3 TL liest sich EL besser
const shopAmount = (i) => i.unit === "TL" && i.amount >= 3 ? fmtAmount(i.amount / 3, "EL") : fmtAmount(i.amount, i.unit);

function lineHtml(i) {
  const toggle = state.user ? ` tabindex="0" role="checkbox" aria-checked="${i.checked}"` : "";
  return `
    <li class="${i.checked ? "checked" : ""}" data-id="${i.id}"${toggle}>
      <span class="box" aria-hidden="true"></span>
      <span class="what"><b>${esc(i.name)}${i.checked ? "" : dealTag(state.shopOffers[i.id])}</b>${i.sources.length ? `<small>für ${esc(i.sources.join(", "))}</small>` : ""}</span>
      <span class="qty">${esc(shopAmount(i))}</span>
      ${state.user ? `<button class="x" data-del="${i.id}" title="Streichen" aria-label="Streichen">×</button>` : ""}
    </li>`;
}

function showShopping() {
  const open = state.shopping.filter((i) => !i.checked), done = state.shopping.filter((i) => i.checked);
  const lists = state.user ? "lines" : "lines static";
  // Rezepte, aus denen etwas auf dem Zettel steht (zum schnellen Nachschauen)
  const titles = [...new Set(state.shopping.flatMap((i) => i.sources))];
  const from = titles.map((t) => state.recipes.find((r) => r.title === t)).filter(Boolean);
  view.innerHTML = `
    <section class="shop">
      <div class="page-h"><h1>Einkaufen!</h1><span class="burst burst--big"><b>${open.length}</b>offen</span></div>
      <div class="shop-list">
        ${state.user ? "" : `<p class="readonly">Abhaken und ändern geht nach dem <a href="/login">Anmelden</a>, leeren auch so.</p>`}
        ${state.shopping.length ? `<div class="koma ledger">
          ${open.length ? `<ul class="${lists}">${open.map(lineHtml).join("")}</ul>` : `<p class="hint">Alles im Korb.</p>`}
          ${done.length ? `<h2 class="done-k">Schon im Korb</h2><ul class="${lists}">${done.map(lineHtml).join("")}</ul>` : ""}
        </div>` : `<div class="empty"><span class="sfx">シーン…</span><span class="empty-h">Zettel ist leer</span><p>${state.user
          ? "Beim Rezept auf den Wagen tippen, dann landen die Zutaten hier." : "Gerade steht nichts drauf."}</p></div>`}
      </div>
      <aside class="till">
        ${state.user ? `<form class="till-sec addform" id="add">
          <span class="till-k">Was noch?</span>
          <input class="input" type="text" id="item" placeholder="z. B. Butter, 1 l Milch" autocomplete="off" aria-label="Was noch?">
          <button class="btn btn--red btn--wide" type="submit">Drauf damit</button>
        </form>` : ""}
        ${state.user ? shopRouteHtml(open) : ""}
        ${from.length ? `<div class="till-sec"><span class="till-k">Für diese Rezepte</span>
          <div class="from">${from.map((r) => `<a href="#/r/${r.id}"><span class="thumb">${picHtml(r)}</span><span>${esc(r.title)}</span></a>`).join("")}</div>
        </div>` : ""}
        ${state.shopping.length ? `<div class="till-sec">
          <div class="btnrow">
            ${done.length ? `<button class="btn btn--light" id="clear-done">Erledigte weg</button>` : ""}
            <button class="btn btn--danger" id="clear-all">Alles leeren</button>
          </div>
          <p class="till-hint">Leert sich von selbst, wenn 48 Stunden lang nichts dazukommt oder abgehakt wird.</p>
        </div>` : ""}
      </aside>
    </section>`;

  // Leeren dürfen auch Gäste (wenn der Zettel öffentlich ist), alles andere nur Angemeldete
  const clear = async (onlyChecked) => {
    try { setShopping(await api("/shopping/clear", "POST", { only_checked: onlyChecked })); showShopping(); }
    catch (err) { noteError(err); }
  };
  document.getElementById("clear-done")?.addEventListener("click", () => clear(true));
  document.getElementById("clear-all")?.addEventListener("click", () => { if (confirm("Den ganzen Zettel leeren?")) clear(false); });

  if (!state.user) return;
  document.getElementById("add").onsubmit = async (e) => {
    e.preventDefault();
    const item = parseLine(document.getElementById("item").value);
    if (!item) return;
    try { setShopping(await api("/shopping", "POST", item)); showShopping(); document.getElementById("item").focus(); }
    catch (err) { noteError(err); }
  };
  const act = async (li, remove) => {
    const id = Number(li.dataset.id);
    try {
      if (remove) setShopping(await api(`/shopping/${id}`, "DELETE"));
      else setShopping(await api(`/shopping/${id}`, "PATCH", { checked: !li.classList.contains("checked") }));
      showShopping();
      // Fokus bleibt beim selben Eintrag, damit man mit der Tastatur weitermachen kann
      view.querySelector(`.lines li[data-id="${id}"]`)?.focus();
    } catch (err) { noteError(err); }
  };
  view.querySelectorAll(".lines li").forEach((li) => {
    li.addEventListener("click", (e) => act(li, Boolean(e.target.closest("[data-del]"))));
    li.addEventListener("keydown", (e) => {
      if (e.target !== li || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      act(li, false);
    });
  });
  refreshShopOffers(open);
}

/** Angebote neu holen, wenn etwas Neues auf dem Zettel steht (nicht bei jedem Abhaken) */
async function refreshShopOffers(open) {
  const key = open.map((i) => i.id).join(",");
  if (!open.length || state.shopOffersFor === key) return;
  state.shopOffersFor = key;
  const wait = document.getElementById("shop-deals-wait");
  if (wait) wait.hidden = false;
  try {
    const map = await loadShopOffers();
    if (wait && Object.keys(map).length) wait.hidden = true;
    else if (wait) wait.textContent = "Nichts davon gerade im Angebot.";
    const changed = JSON.stringify(map) !== JSON.stringify(state.shopOffers);
    state.shopOffers = map;
    if (changed && location.hash.replace(/^#\/?/, "").startsWith("einkauf")) showShopping();
  } catch (err) {
    state.shopOffersFor = "";  // beim nächsten Mal nochmal probieren
    if (wait) wait.textContent = "Angebote gerade nicht abrufbar.";
  }
}

/** Seitenleiste: welche Zutaten wo gerade günstig sind, nach Markt sortiert */
function shopRouteHtml(open) {
  const byShop = {};
  for (const i of open) {
    const offers = state.shopOffers[i.id];
    if (!offers?.length) continue;
    const best = offers[0];
    (byShop[shopName(best.retailer)] ??= []).push({ item: i, offer: best });
  }
  const shops = Object.entries(byShop).sort((a, b) => b[1].length - a[1].length);
  const wait = `<p class="till-hint" id="shop-deals-wait" hidden>Suche Angebote …</p>`;
  if (!shops.length) {
    const loaded = state.shopOffersFor === open.map((i) => i.id).join(",");
    return open.length ? `<div class="till-sec"><span class="till-k">Gerade im Angebot</span>
      <p class="till-hint" id="shop-deals-wait">${loaded ? "Nichts davon gerade im Angebot." : "Suche Angebote …"}</p></div>` : "";
  }
  return `<div class="till-sec"><span class="till-k">Gerade im Angebot</span>${wait}
    ${shops.map(([shop, list]) => `<div class="route"><b class="route-shop">${esc(shop)}</b>
      <ul class="deal-list">${list.map(({ item, offer }) => `<li><span class="d-what">${esc(item.name)}<small>${esc(offer.title)} · ${esc(dealWhen(offer))}</small></span>
        <span class="d-price">${esc(euro(offer.price))}</span></li>`).join("")}</ul></div>`).join("")}
  </div>`;
}

// --- Ansicht: neues Rezept / bearbeiten -----------------------------------------------
function editorHtml(r, notice) {
  return `
    ${notice ? `<p class="draft">${esc(notice)}</p>` : ""}
    <form class="editor" id="editor">
      <div class="ed-main">
        <label class="field"><span class="flabel">Titel</span>
          <input class="input" type="text" id="title" value="${esc(r.title)}" required></label>
        <div class="cols">
          <label class="field"><span class="flabel">Kategorie</span>
            <select class="input" id="category"><option value="">– keine –</option>
              ${state.categories.map((c) => `<option${c === r.category ? " selected" : ""}>${esc(c)}</option>`).join("")}</select></label>
          <label class="field"><span class="flabel">Personen</span>
            <input class="input" type="number" id="servings" min="1" max="100" value="${esc(r.servings ?? "")}"></label>
          <label class="field"><span class="flabel">Minuten</span>
            <input class="input" type="number" id="minutes" min="1" value="${esc(r.minutes ?? "")}"></label>
        </div>
        <label class="field"><span class="flabel">Zutaten <small>· eine pro Zeile, z. B. „250 g Linsen“</small></span>
          <textarea class="input" id="ings" rows="9">${esc(r.ingredients.map(toLine).join("\n"))}</textarea></label>
        <label class="field"><span class="flabel">Zubereitung <small>· ein Schritt pro Zeile</small></span>
          <textarea class="input" id="steps" rows="9">${esc(r.steps.join("\n"))}</textarea></label>
      </div>
      <div class="ed-side">
        <div class="field"><span class="flabel">Foto <small>· eigenes oder von Unsplash${r.id ? "" : ", sonst wird beim Speichern eins gesucht"}</small></span>
          <div class="picker">
            <div class="pic" id="pick-current" title="Eigenes Foto hier reinziehen"></div>
            <div class="picker-row">
              <button class="btn btn--red" type="button" id="pick-own">Eigenes Foto</button>
              <input type="file" id="pick-file" accept="image/*" hidden>
              <span class="hint on-desk">oder reinziehen / Strg+V</span>
            </div>
            <div class="picker-row">
              <input class="input" type="text" id="pick-q" value="${esc(r.image_query || r.title)}" placeholder="Suchbegriff" aria-label="Foto suchen">
              <button class="btn btn--light" type="button" id="pick-search">Suchen</button>
            </div>
            <p class="hint" id="pick-msg"></p>
            <div class="choices" id="pick-choices"></div>
            <div class="picker-row">
              <button class="btn btn--light" type="button" id="pick-more" hidden>Mehr zeigen</button>
              <button class="btn btn--light" type="button" id="pick-none">Kein Foto</button>
            </div>
          </div>
        </div>
        <label class="field"><span class="flabel">Notiz</span>
          <textarea class="input" id="notes" rows="3">${esc(r.notes)}</textarea></label>
        <label class="field"><span class="flabel">Quelle</span>
          <input class="input" type="text" id="source" value="${esc(r.source)}" placeholder="Mamas Heft, chefkoch.de …"></label>
      </div>
      <div class="ed-foot">
        <p class="error" id="err" style="flex-basis:100%"></p>
        <button class="btn btn--big" type="submit">Speichern</button>
        <a class="btn btn--light btn--big" href="${r.id ? `#/r/${r.id}` : "#/"}">Abbrechen</a>
      </div>
    </form>`;
}

function bindEditor(r) {
  let image = r.image || null, page = 1, lastQuery = "", noImage = false;
  const imageQuery = r.image_query || "";
  const current = document.getElementById("pick-current"), msg = document.getElementById("pick-msg");
  const choices = document.getElementById("pick-choices"), more = document.getElementById("pick-more");
  const showCurrent = () => {
    current.innerHTML = image ? `<img src="${esc(image.small)}" alt="">`
      : `<span class="ph"><span><b>?</b>${r.id ? "kein Foto" : "wird gesucht"}</span></span>`;
  };
  showCurrent();

  const search = async (next) => {
    // "Mehr zeigen" blättert im zuletzt gesuchten Begriff weiter; leeres Feld → Titel nehmen
    const q = next ? lastQuery : (document.getElementById("pick-q").value.trim() || document.getElementById("title").value.trim());
    if (!q) { msg.textContent = "Erst einen Suchbegriff oder Titel eingeben."; return; }
    page = next ? page + 1 : 1;
    lastQuery = q;
    msg.textContent = "Suche …";
    try {
      const en = imageQuery && q === imageQuery ? "1" : "0";
      const found = await api(`/images?q=${encodeURIComponent(q)}&page=${page}&en=${en}`);
      msg.textContent = found.length ? "Antippen zum Auswählen." : "Nichts gefunden, anderer Begriff?";
      choices.innerHTML = found.map((f, i) => `<button type="button" data-i="${i}" title="Foto: ${esc(f.by)}"><img src="${esc(f.small)}" alt=""></button>`).join("");
      choices.onclick = (e) => {
        const b = e.target.closest("[data-i]");
        if (!b) return;
        image = found[Number(b.dataset.i)];
        noImage = false;
        showCurrent();
        msg.textContent = `Foto von ${image.by} gewählt.`;
      };
      more.hidden = found.length < 8;
    } catch (err) { msg.textContent = err.message; }
  };
  document.getElementById("pick-search").onclick = () => search(false);
  document.getElementById("pick-q").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); search(false); } };
  more.onclick = () => search(true);
  // Eigenes Foto: im Browser verkleinern (groß fürs Rezept, klein für die Liste), dann hochladen
  const own = document.getElementById("pick-own"), file = document.getElementById("pick-file");
  const upload = async (f) => {
    if (!f || !f.type.startsWith("image/") || own.disabled) return;
    own.disabled = true;
    msg.textContent = "Lade Foto hoch …";
    try {
      const [large, small] = await Promise.all([shrink(f, 1600), shrink(f, 600)]);
      image = await api("/fotos", "POST", { large, small });
      noImage = false;
      showCurrent();
      choices.innerHTML = "";
      more.hidden = true;
      msg.textContent = "Eigenes Foto drin. Wird mit „Speichern“ übernommen.";
    } catch (err) {
      msg.textContent = err.message === "Bild nicht lesbar"
        ? `„${f.name}“ kann der Browser nicht lesen (z. B. HEIC). Bitte als JPG oder Screenshot.` : err.message;
    } finally { own.disabled = false; file.value = ""; }
  };
  own.onclick = () => file.click();
  file.onchange = () => upload(file.files[0]);
  current.ondragover = (e) => { e.preventDefault(); current.classList.add("over"); };
  current.ondragleave = () => current.classList.remove("over");
  current.ondrop = (e) => { e.preventDefault(); current.classList.remove("over"); upload(e.dataTransfer.files[0]); };
  document.onpaste = (e) => {
    if (!document.getElementById("pick-current")) return;
    const item = [...e.clipboardData.items].find((i) => i.type.startsWith("image/"));
    if (item) { e.preventDefault(); upload(item.getAsFile()); }
  };

  document.getElementById("pick-none").onclick = () => {
    image = null;
    noImage = true;  // sonst sucht der Server bei neuen Rezepten doch wieder eins
    current.innerHTML = `<span class="ph"><span><b>–</b>ohne Foto</span></span>`;
    msg.textContent = "Ohne Foto.";
  };

  document.getElementById("editor").onsubmit = async (e) => {
    e.preventDefault();
    const val = (k) => document.getElementById(k).value;
    const data = {
      title: val("title"), servings: val("servings"), minutes: val("minutes"), category: val("category"),
      ingredients: val("ings").split("\n").map(parseLine).filter(Boolean),
      steps: val("steps").split("\n").map((s) => s.trim().replace(/^\d+[.)]\s*/, "")).filter(Boolean),
      notes: val("notes"), source: val("source"), image, image_query: imageQuery, no_image: noImage,
    };
    const btn = e.submitter;
    if (btn) btn.disabled = true;
    try {
      const saved = await api(r.id ? `/recipes/${r.id}` : "/recipes", r.id ? "PUT" : "POST", data);
      if (!r.id) setDraft(null);
      await loadRecipes();
      location.replace(`#/r/${saved.id}`);  // "Zurück" führt dann nicht wieder ins Formular
    } catch (err) {
      document.getElementById("err").textContent = err.message;
      if (btn) btn.disabled = false;
    }
  };
}

function showEdit(id) {
  const r = state.recipes.find((x) => x.id === id);
  if (!r) { view.innerHTML = `<div class="empty"><span class="empty-h">Nicht da</span><p>Dieses Rezept gibt es nicht.</p><a class="btn" href="#/">Zu den Rezepten</a></div>`; return; }
  view.innerHTML = `<div class="page-h"><h1>Bearbeiten</h1><p>${esc(r.title)}</p></div>` + editorHtml(r);
  bindEditor(r);
}

const EMPTY = { title: "", category: "", servings: null, minutes: null, ingredients: [], steps: [], notes: "", source: "", image: null };
const WAYS = [
  ["", "pen", "Selbst schreiben", "Von Hand eintippen"],
  ["bild", "image", "Aus Bild", "Screenshot oder Foto"],
  ["text", "text", "Aus Text", "Von einer Webseite"],
  ["vorschlag", "bulb", "Vorschlagen", "Idee von der KI"],
];

function providerSelect() {
  const p = state.ai.providers;
  if (p.length < 2) return "";
  const def = state.ai.default || p[0].id;
  return `<label class="field" style="margin:0;min-width:180px"><span class="flabel">KI</span><select class="input" id="provider">${p.map((x) => `<option value="${x.id}"${x.id === def ? " selected" : ""}>${esc(x.label)}</option>`).join("")}</select></label>`;
}

function showNew(way) {
  const head = `<div class="page-h"><h1>Neues Rezept</h1><p>Wie soll's reinkommen?</p></div>
    <div class="ways">${WAYS.map(([k, icon, label, sub]) => `<a class="way${k === way ? " on" : ""}" href="#/neu${k ? "/" + k : ""}">${ICONS[icon]}<b>${label}</b><small>${sub}</small></a>`).join("")}</div>`;

  if (!way || state.draft) {
    view.innerHTML = head + (state.draft
      ? `<p class="draft">KI-Entwurf – bitte kurz prüfen, dann speichern. <button class="btn btn--light" type="button" id="discard">Verwerfen</button></p>` : "")
      + editorHtml(state.draft || EMPTY);
    bindEditor(state.draft || EMPTY);
    document.getElementById("discard")?.addEventListener("click", () => {
      if (!confirm("Den KI-Entwurf wirklich verwerfen?")) return;
      setDraft(null);
      showNew(way);
    });
    return;
  }
  if (!state.ai.providers.length) {
    view.innerHTML = head + `<div class="empty"><span class="empty-h">Kein KI-Key</span><p>Dafür fehlt noch ein API-Key.</p><a class="btn" href="#/einstellungen">Zu den Einstellungen</a></div>`;
    return;
  }

  const forms = {
    bild: `
      <div class="drop" id="drop" role="button" tabindex="0"><span><b>Bild hier rein</b><br>
        <span class="on-desk">reinziehen, mit Strg+V einfügen oder klicken</span><span class="on-touch">antippen und Foto oder Screenshot wählen</span></span></div>
      <input type="file" id="file" accept="image/*" multiple hidden>
      <div class="thumbs" id="thumbs"></div>
      <label class="field"><span class="flabel">Hinweis <small>· optional, z. B. „nur das rechte Rezept“</small></span>
        <input class="input" type="text" id="hint"></label>`,
    text: `
      <label class="field"><span class="flabel">Rezepttext <small>· z. B. von einer Webseite kopiert</small></span>
        <textarea class="input" id="text" rows="12"></textarea></label>`,
    vorschlag: `
      <label class="field"><span class="flabel">Worauf hast du Lust?</span>
        <input class="input" type="text" id="wish" placeholder="z. B. etwas mit Kürbis, vegetarisch, schnell" autocomplete="off"></label>`,
  };
  view.innerHTML = head + `<div class="panel">${forms[way]}
    <p class="error" id="err"></p>
    <div class="btnrow" style="align-items:flex-end">${providerSelect()}<button class="btn btn--big" id="go">${way === "vorschlag" ? "Vorschlagen" : "Auslesen"}</button></div>
    <p class="hint" id="wait" style="margin-top:14px"></p></div>`;

  if (way === "bild") bindImages();
  if (way === "vorschlag") document.getElementById("wish").onkeydown = (e) => { if (e.key === "Enter") runAI(way); };
  document.getElementById("go").onclick = () => runAI(way);
}

function bindImages() {
  state.images = [];
  const drop = document.getElementById("drop"), file = document.getElementById("file");
  const thumbs = document.getElementById("thumbs");
  const render = () => {
    thumbs.innerHTML = state.images.map((src, i) =>
      `<button type="button" data-rm="${i}" title="Bild entfernen" aria-label="Bild ${i + 1} entfernen"><img src="${src}" alt=""></button>`).join("");
  };
  thumbs.onclick = (e) => {
    const b = e.target.closest("[data-rm]");
    if (!b) return;
    state.images.splice(Number(b.dataset.rm), 1);
    render();
  };
  const add = async (files) => {
    let skipped = 0;
    for (const f of files) {
      if (!f || !f.type.startsWith("image/")) continue;
      if (state.images.length >= 4) { skipped++; continue; }
      try { state.images.push(await shrink(f)); }
      catch { note(`„${f.name}“ kann der Browser nicht lesen (z. B. HEIC). Bitte als JPG oder Screenshot.`, true); }
    }
    if (skipped) note("Höchstens 4 Bilder auf einmal.", true);
    render();
  };
  drop.onclick = () => file.click();
  drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); file.click(); } };
  file.onchange = () => add(file.files);
  drop.ondragover = (e) => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = (e) => { e.preventDefault(); drop.classList.remove("over"); add(e.dataTransfer.files); };
  document.onpaste = (e) => {
    if (!document.getElementById("drop")) return;
    const files = [...e.clipboardData.items].filter((i) => i.type.startsWith("image/")).map((i) => i.getAsFile());
    if (files.length) add(files);
  };
}

/** Bilder vor dem Hochladen verkleinern (spart Zeit und KI-Kosten). */
function shrink(file, max = 1600) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const scale = Math.min(1, max / Math.max(img.width, img.height));
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(img.width * scale); canvas.height = Math.round(img.height * scale);
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = "#fff";  // durchsichtige PNGs würden als JPEG sonst schwarz
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(img.src);
      resolve(canvas.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(img.src); reject(new Error("Bild nicht lesbar")); };
    img.src = URL.createObjectURL(file);
  });
}

async function runAI(way) {
  const err = document.getElementById("err"), go = document.getElementById("go"), wait = document.getElementById("wait");
  if (go.disabled) return;
  const provider = document.getElementById("provider")?.value || "";
  let payload;
  if (way === "bild") {
    if (!state.images.length) { err.textContent = "Erst ein Bild einfügen."; return; }
    payload = { images: state.images, hint: document.getElementById("hint").value, provider };
  } else if (way === "text") payload = { text: document.getElementById("text").value, provider };
  else payload = { wish: document.getElementById("wish").value, provider };

  const label = go.textContent;
  err.textContent = "";
  go.disabled = true;
  go.textContent = way === "vorschlag" ? "Überlegt …" : "Liest …";
  wait.textContent = "Dauert meist 10 bis 30 Sekunden.";
  const here = location.hash;
  try {
    const draft = await api(`/ai/${way}`, "POST", payload);
    draft.source ||= way === "vorschlag" ? "Vorgeschlagen" : "";
    setDraft(draft);
    // Wer inzwischen woanders ist, bekommt nicht plötzlich den Editor vor die Nase – der Entwurf wartet
    if (location.hash === here) showNew(way);
    else note("Der KI-Entwurf ist fertig – unter „+ Neu“.");
  } catch (e) {
    if (location.hash !== here) { noteError(e); return; }
    err.textContent = e.message;
    go.disabled = false;
    go.textContent = label;
    wait.textContent = "";
  }
}

/** KI-Entwurf merken (auch über Neuladen hinweg), bis er gespeichert oder verworfen wird. */
function setDraft(draft) {
  state.draft = draft;
  try {
    if (draft) sessionStorage.setItem("draft", JSON.stringify(draft));
    else sessionStorage.removeItem("draft");
  } catch { /* privates Fenster: dann nur im Speicher */ }
}

function loadDraft() {
  try { state.draft = JSON.parse(sessionStorage.getItem("draft") || "null"); } catch { state.draft = null; }
}

// --- Ansicht: Einstellungen (Pinnwand) ------------------------------------------------
function keyField(id, label, hint, source) {
  const from = source === "env" ? " · aus der .env" : "";
  return `<label class="field"><span class="flabel">${label}<small>${from}</small></span>
    <input class="input" type="password" id="${id}" autocomplete="off" spellcheck="false"
      placeholder="${hint ? `…${esc(hint)} gespeichert` : "noch keiner"}"></label>`;
}

function providerHtml(p) {
  return `
    <div class="provider">
      <h4>${esc(p.label)}</h4>
      <div class="cols">
        ${keyField(`key-${p.id}`, "API-Key", p.key_hint, p.key_source)}
        <label class="field"><span class="flabel">Modell</span>
          <input class="input" type="text" id="model-${p.id}" value="${esc(p.model)}" spellcheck="false"></label>
      </div>
      <div class="btnrow">
        <button class="btn btn--light" data-test="${p.id}">Testen</button>
        ${p.key_source === "seite" ? `<button class="btn btn--light" data-remove="${p.id}">Key entfernen</button>` : ""}
      </div>
      <p class="hint" id="res-${p.id}" style="margin:10px 0 0"></p>
    </div>`;
}

async function showSettings() {
  const s = await api("/settings");
  const px = s.unsplash;
  view.innerHTML = `
    <div class="page-h"><h1>Einstellungen</h1></div>
    <div class="manga-page">
      <section class="koma">
        <h3>Öffentlich</h3>
        <p class="hint">Was Besucher ohne Anmeldung sehen dürfen. Ändern können sie nichts, KI und Einstellungen bleiben immer privat.</p>
        <label class="switchrow"><span class="switch"><input type="checkbox" data-public="public"${s.public ? " checked" : ""}><span></span></span>
          Rezepte zeigen</label>
        <label class="switchrow"><span class="switch"><input type="checkbox" data-public="public_shopping"${s.public_shopping ? " checked" : ""}><span></span></span>
          Einkaufszettel zeigen</label>
      </section>

      <section class="koma">
        <h3>Bilder</h3>
        <p class="hint">Fotos kommen von Unsplash. Den Key gibt's kostenlos auf unsplash.com/developers:
          „New Application“ anlegen und den <b>Access Key</b> hier eintragen.</p>
        ${keyField("unsplash-key", "Unsplash-Key", px.key_hint, px.key_source)}
        <div class="btnrow" style="margin-top:0">
          <button class="btn btn--light" data-test="unsplash">Speichern &amp; testen</button>
          ${px.missing ? `<button class="btn btn--light" id="fill">${px.missing} ohne Foto füllen</button>` : ""}
        </div>
        <p class="hint" id="res-unsplash" style="margin:10px 0 0"></p>
      </section>

      <section class="koma">
        <h3>KI</h3>
        <p class="hint">Fürs Vorschlagen und Auslesen. Nur Anbieter mit Key tauchen beim Anlegen auf.</p>
        ${s.providers.map(providerHtml).join("")}
        <div class="cols" style="margin-top:14px">
          <label class="field"><span class="flabel">Vorausgewählt</span>
            <select class="input" id="ai-default">${s.providers.map((p) => `<option value="${p.id}"${p.id === s.ai_default ? " selected" : ""}>${esc(p.label)}</option>`).join("")}</select></label>
          <label class="field"><span class="flabel">Max. pro Stunde</span>
            <input class="input" type="number" id="ai-limit" min="1" max="10000" value="${s.ai_limit}"></label>
        </div>
        <div class="btnrow" style="margin-top:4px"><button class="btn" id="save-ai">Speichern</button></div>
        <p class="hint" id="ai-msg" style="margin:10px 0 0"></p>
      </section>

      <section class="koma">
        <h3>Passwort</h3>
        <p class="hint">Mindestens 8 Zeichen. Danach müssen sich alle anderen Geräte neu anmelden.</p>
        <form id="pw">
          <label class="field"><span class="flabel">Bisheriges</span><input class="input" type="password" id="pw-old" autocomplete="current-password" required></label>
          <label class="field"><span class="flabel">Neues</span><input class="input" type="password" id="pw-new" autocomplete="new-password" minlength="8" required></label>
          <label class="field"><span class="flabel">Nochmal</span><input class="input" type="password" id="pw-again" autocomplete="new-password" required></label>
          <div class="btnrow" style="margin-top:4px"><button class="btn" type="submit">Ändern</button></div>
          <p class="hint" id="pw-msg" style="margin:10px 0 0"></p>
        </form>
      </section>

      <section class="koma">
        <h3>Daten</h3>
        <p class="hint">${state.recipes.length} ${state.recipes.length === 1 ? "Rezept" : "Rezepte"} gespeichert.</p>
        <div class="btnrow" style="margin-top:0">
          <a class="btn btn--light" href="/api/export" download>Alle herunterladen</a>
        </div>
        <form method="post" action="/logout" class="btnrow"><button class="btn btn--light" type="submit">Abmelden</button></form>
      </section>
    </div>`;

  view.querySelectorAll("[data-public]").forEach((input) => input.onchange = async () => {
    const what = input.dataset.public === "public" ? "Rezepte" : "Einkaufszettel";
    try {
      await api("/settings", "PUT", { [input.dataset.public]: input.checked });
      note(input.checked ? `${what}: jetzt ohne Anmeldung sichtbar.` : `${what}: nur noch nach Anmeldung.`);
    } catch (err) {
      input.checked = !input.checked;  // Schalter zeigt wieder den echten Stand
      noteError(err);
    }
  });

  const saveAI = async (extra = {}) => {
    const providers = {};
    for (const p of s.providers) {
      providers[p.id] = { key: document.getElementById(`key-${p.id}`).value, model: document.getElementById(`model-${p.id}`).value };
    }
    for (const [id, v] of Object.entries(extra)) Object.assign(providers[id], v);
    await api("/settings", "PUT", {
      providers, unsplash_key: document.getElementById("unsplash-key").value,
      ai_default: document.getElementById("ai-default").value, ai_limit: document.getElementById("ai-limit").value,
    });
    state.ai = await api("/ai");
  };
  document.getElementById("save-ai").onclick = async () => {
    const msg = document.getElementById("ai-msg");
    msg.textContent = "";
    try { await saveAI(); await showSettings(); note("Gespeichert."); }
    catch (err) { msg.textContent = err.message; }
  };
  view.querySelectorAll("[data-test]").forEach((b) => b.onclick = async () => {
    const id = b.dataset.test, res = document.getElementById(`res-${id}`);
    res.textContent = "Frage nach …";
    try {
      await saveAI();
      const r = await api("/settings/test", "POST", { provider: id });
      res.textContent = `Klappt! ${id === "unsplash" ? r.answer : `Antwort: „${r.answer}“`}`;
    } catch (err) { res.textContent = err.message; }
  });
  view.querySelectorAll("[data-remove]").forEach((b) => b.onclick = async () => {
    if (!confirm("Key von der Seite entfernen? Ein Key aus der .env gilt dann wieder.")) return;
    try {
      await saveAI({ [b.dataset.remove]: { key: "", remove_key: true } });
      showSettings();
    } catch (err) { noteError(err); }
  });
  document.getElementById("fill")?.addEventListener("click", async (e) => {
    const res = document.getElementById("res-unsplash"), btn = e.currentTarget;  // nach await ist currentTarget weg
    btn.disabled = true;
    res.textContent = "Suche Fotos …";
    try {
      const r = await api("/images/fill", "POST", {});
      await loadRecipes();
      note(`${plural(r.found, "Foto", "Fotos")} gefunden.${r.error ? ` Abgebrochen: ${r.error}` : ""}`, Boolean(r.error));
      showSettings();
    } catch (err) { res.textContent = err.message; btn.disabled = false; }
  });

  document.getElementById("pw").onsubmit = async (e) => {
    e.preventDefault();
    const msg = document.getElementById("pw-msg"), val = (id) => document.getElementById(id).value;
    if (val("pw-new") !== val("pw-again")) { msg.textContent = "Die beiden neuen Passwörter stimmen nicht überein."; return; }
    try {
      await api("/settings/password", "POST", { current: val("pw-old"), new: val("pw-new") });
      e.target.reset();
      msg.textContent = "Geändert.";
    } catch (err) { msg.textContent = err.message; }
  };
}

// --- Navigation -------------------------------------------------------------------
function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/");
  const page = parts[0] || "rezepte";
  document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("on",
    a.dataset.nav === (page === "r" || page === "bearbeiten" ? "rezepte" : page)));
  document.onpaste = null;
  window.scrollTo(0, 0);
  renderCartbar();

  if (!state.user) {
    // Besucher: nur Rezepte und/oder Zettel ansehen, je nach Freigabe
    if (state.publicShopping && (page === "einkauf" || !state.publicRecipes)) showShopping();
    else if (page === "r") showRecipe(Number(parts[1]));
    else showList();
    return;
  }
  if (page === "r") showRecipe(Number(parts[1]));
  else if (page === "einstellungen") showSettings().catch((err) => { view.innerHTML = `<p class="error">${esc(err.message)}</p>`; });
  else if (page === "bearbeiten") showEdit(Number(parts[1]));
  else if (page === "einkauf") showShopping();
  else if (page === "neu") showNew(parts[1] || "");
  else showList();
}

view.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-cart]");
  if (btn) addToCart(Number(btn.dataset.cart), null, btn);
});

// Neue Abfrage, wenn man zur Seite zurückkommt – der Zettel ist für alle derselbe.
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible") return;
  const tasks = [];
  if (state.user || state.publicRecipes) tasks.push(loadRecipes());
  if (state.user || state.publicShopping) tasks.push(loadShopping());
  if (!tasks.length) return;
  try { await Promise.all(tasks); } catch { return; }  // z. B. offline im Laden: alten Stand behalten
  if (view.querySelector(".shop")) showShopping();
});

window.addEventListener("hashchange", route);

(async () => {
  let me;
  try { me = await api("/me"); } catch (err) {
    view.innerHTML = `<div class="empty"><span class="empty-h">Kein Kontakt</span><p>${esc(err.message)}</p><a class="btn" href="/">Nochmal versuchen</a></div>`;
    return;
  }
  loadDraft();
  state.user = me.user;
  state.publicRecipes = me.public;
  state.publicShopping = me.public_shopping;
  state.partner = me.partner || "";
  state.categories = me.categories || [];
  const show = (sel, on) => document.querySelectorAll(sel).forEach((a) => { a.hidden = !on; });
  show("[data-private]", state.user);
  show("[data-guest]", !state.user);
  show("[data-recipes]", state.user || state.publicRecipes);
  show("[data-shop]", state.user || state.publicShopping);
  if (state.user) {
    const [, , aiInfo] = await Promise.all([loadRecipes(), loadShopping(), api("/ai")]);
    state.ai = aiInfo;
  } else {
    await Promise.all([state.publicRecipes && loadRecipes(), state.publicShopping && loadShopping()]);
  }
  route();
})();
