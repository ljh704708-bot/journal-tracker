// Journal Tracker — client-side logic for index.html and journal.html.
// All user-added subscriptions live in localStorage; defaults come from journals.json.

const DEFAULTS_URL = "journals.json";
const STORAGE_ADDED = "jt:added";
const STORAGE_LAST_VISIT = "jt:lastVisit:";
const ARTICLES_PER_JOURNAL = 15;
const ABSTRACT_MAX_CHARS = 600;

// Letter-mark cover palette
const COVER_COLORS = [
  "#ef4444", "#f97316", "#d97706", "#ca8a04",
  "#65a30d", "#16a34a", "#0d9488", "#0891b2",
  "#2563eb", "#4f46e5", "#7c3aed", "#a21caf",
  "#be185d", "#e11d48",
];
const STOP_WORDS = new Set(["of", "and", "the", "&", "in", "on", "for", "a", "an"]);

// ---------- Storage ----------

function loadAdded() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_ADDED) || "[]");
  } catch {
    return [];
  }
}

function saveAdded(journal) {
  const added = loadAdded();
  if (added.some((j) => j.issn === journal.issn)) return false;
  added.push({ ...journal, addedAt: new Date().toISOString() });
  localStorage.setItem(STORAGE_ADDED, JSON.stringify(added));
  return true;
}

function removeAdded(issn) {
  const added = loadAdded().filter((j) => j.issn !== issn);
  localStorage.setItem(STORAGE_ADDED, JSON.stringify(added));
}

function getLastVisit(issn) {
  return localStorage.getItem(STORAGE_LAST_VISIT + issn);
}

function setLastVisit(issn) {
  localStorage.setItem(STORAGE_LAST_VISIT + issn, new Date().toISOString());
}

async function loadJournals() {
  let defaults = [];
  try {
    defaults = await fetch(DEFAULTS_URL).then((r) => r.json());
  } catch (e) {
    console.warn("Could not load defaults:", e);
  }
  const added = loadAdded();
  return [
    ...defaults.map((j) => ({ ...j, source: "default" })),
    ...added.map((j) => ({ ...j, source: "added" })),
  ];
}

// ---------- Letter-mark cover ----------

function hashColor(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = ((h * 31 + s.charCodeAt(i)) | 0);
  return COVER_COLORS[Math.abs(h) % COVER_COLORS.length];
}

function initials(name) {
  const words = name.split(/\s+/).filter((w) => w && !STOP_WORDS.has(w.toLowerCase()));
  return words.map((w) => w[0]).join("").toUpperCase().slice(0, 3);
}

// ---------- Crossref / OpenAlex ----------

async function fetchArticles(issn, rows = ARTICLES_PER_JOURNAL) {
  const params = new URLSearchParams({
    sort: "published",
    order: "desc",
    rows: String(rows),
    select: "title,author,DOI,URL,published-online,published-print,issued,volume,issue,abstract",
  });
  const url = `https://api.crossref.org/journals/${encodeURIComponent(issn)}/works?${params}`;
  const data = await fetch(url).then((r) => r.json());
  return data.message?.items || [];
}

async function searchJournals(query) {
  const params = new URLSearchParams({ query, rows: "10" });
  const url = `https://api.crossref.org/journals?${params}`;
  const data = await fetch(url).then((r) => r.json());
  return data.message?.items || [];
}

async function fetchAbstracts(dois) {
  const out = {};
  if (!dois.length) return out;
  const fullDois = dois.map((d) => "https://doi.org/" + d.toLowerCase());
  for (let i = 0; i < fullDois.length; i += 25) {
    const chunk = fullDois.slice(i, i + 25);
    const params = new URLSearchParams({
      filter: "doi:" + chunk.join("|"),
      "per-page": "25",
      select: "doi,abstract_inverted_index",
    });
    const url = `https://api.openalex.org/works?${params}`;
    try {
      const data = await fetch(url).then((r) => r.json());
      for (const w of data.results || []) {
        const doi = (w.doi || "").replace("https://doi.org/", "").toLowerCase();
        if (doi && w.abstract_inverted_index) {
          out[doi] = decodeIdx(w.abstract_inverted_index);
        }
      }
    } catch (e) {
      console.warn("OpenAlex chunk failed:", e);
    }
  }
  return out;
}

function decodeIdx(idx) {
  const positions = [];
  for (const [word, posList] of Object.entries(idx)) {
    for (const p of posList) positions.push([p, word]);
  }
  positions.sort((a, b) => a[0] - b[0]);
  return positions.map(([, w]) => w).join(" ");
}

// ---------- Helpers ----------

function articleDate(item) {
  for (const k of ["published-online", "published-print", "issued"]) {
    const dp = item[k]?.["date-parts"]?.[0];
    if (dp && dp.length) {
      try {
        return new Date(dp[0], (dp[1] || 1) - 1, dp[2] || 1);
      } catch {
        return null;
      }
    }
  }
  return null;
}

function formatAuthors(authors) {
  if (!authors?.length) return "";
  const names = authors.slice(0, 3)
    .map((a) => `${a.given || ""} ${a.family || ""}`.trim())
    .filter(Boolean);
  return names.join(", ") + (authors.length > 3 ? " et al." : "");
}

function cleanText(s) {
  if (!s) return "";
  return s.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/^Abstract[:\s]+/i, "").trim();
}

function truncate(s, n) {
  if (s.length <= n) return s;
  let cut = s.slice(0, n);
  const last = cut.lastIndexOf(" ");
  if (last > n * 0.7) cut = cut.slice(0, last);
  return cut.replace(/[,.;: ]+$/, "") + "…";
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "dataset") Object.assign(node.dataset, v);
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (k === "html") node.innerHTML = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) {
    if (c == null || c === false) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// ---------- Index page ----------

async function initIndex() {
  const grid = document.getElementById("grid");
  const empty = document.getElementById("empty-state");
  const journals = await loadJournals();

  if (!journals.length) {
    empty.hidden = false;
  } else {
    journals.forEach((j) => grid.appendChild(renderTile(j)));
    journals.forEach((j) => updateNewBadge(j));
  }

  document.getElementById("add-btn").addEventListener("click", openAddModal);
  document.getElementById("close-modal").addEventListener("click", closeAddModal);
  document.getElementById("add-modal").addEventListener("click", (e) => {
    if (e.target.classList.contains("modal-backdrop")) closeAddModal();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAddModal();
  });
}

function renderTile(j) {
  const cover = el("div", { class: "cover", style: `background:${hashColor(j.name)}` },
    el("span", { class: "cover-letters" }, initials(j.name) || "?"));
  const name = el("div", { class: "tile-name" }, j.name);
  const badge = el("div", {
    class: "tile-badge", hidden: true, dataset: { issn: j.issn },
  });
  return el("a", {
    class: "tile",
    href: `journal.html?issn=${encodeURIComponent(j.issn)}`,
    dataset: { issn: j.issn },
  }, cover, name, badge);
}

async function updateNewBadge(j) {
  const lastVisit = getLastVisit(j.issn);
  if (!lastVisit) return;
  try {
    const items = await fetchArticles(j.issn, 5);
    if (!items.length) return;
    const newest = articleDate(items[0]);
    if (newest && newest > new Date(lastVisit)) {
      const badge = document.querySelector(`.tile-badge[data-issn="${CSS.escape(j.issn)}"]`);
      if (badge) {
        badge.textContent = "NEW";
        badge.hidden = false;
      }
    }
  } catch {}
}

// ---------- Add modal ----------

function openAddModal() {
  const modal = document.getElementById("add-modal");
  const input = document.getElementById("search-input");
  const results = document.getElementById("search-results");
  modal.hidden = false;
  input.value = "";
  results.innerHTML = "";

  let timer;
  input.oninput = () => {
    clearTimeout(timer);
    const q = input.value.trim();
    timer = setTimeout(() => doSearch(q), 300);
  };
  setTimeout(() => input.focus(), 50);
}

function closeAddModal() {
  document.getElementById("add-modal").hidden = true;
}

async function doSearch(query) {
  const results = document.getElementById("search-results");
  if (!query) { results.innerHTML = ""; return; }

  results.innerHTML = `<p class="search-state">Searching…</p>`;
  let items;
  try {
    items = await searchJournals(query);
  } catch (e) {
    results.innerHTML = `<p class="search-state error">Search failed: ${e.message}</p>`;
    return;
  }
  if (!items.length) {
    results.innerHTML = `<p class="search-state">No journals found.</p>`;
    return;
  }

  const tracked = new Set((await loadJournals()).map((j) => j.issn));
  results.innerHTML = "";
  for (const item of items) {
    const issn = item.ISSN?.[0];
    if (!issn) continue;
    const alreadyTracked = tracked.has(issn);
    const btn = el("button", {
      class: "primary-btn small",
      disabled: alreadyTracked,
      onclick: () => addAndClose(item),
    }, alreadyTracked ? "Added" : "+ Add");

    results.appendChild(el("div", { class: "search-result" },
      el("div", { class: "search-info" },
        el("div", { class: "search-title" }, item.title),
        el("div", { class: "search-meta" },
          `ISSN ${issn}` + (item.publisher ? ` · ${item.publisher}` : ""))),
      btn));
  }
}

function addAndClose(item) {
  const issn = item.ISSN?.[0];
  if (!issn) return;
  saveAdded({ issn, name: item.title, homepage: "" });
  closeAddModal();
  location.reload();
}

// ---------- Journal page ----------

async function initJournal() {
  const issn = new URLSearchParams(location.search).get("issn");
  const nameEl = document.getElementById("journal-name");
  const homepageEl = document.getElementById("journal-homepage");
  const list = document.getElementById("articles");
  const loading = document.getElementById("loading");
  const removeBtn = document.getElementById("remove-btn");

  if (!issn) {
    nameEl.textContent = "No journal selected";
    loading.hidden = true;
    return;
  }

  const journals = await loadJournals();
  const journal = journals.find((j) => j.issn === issn);
  nameEl.textContent = journal ? journal.name : `ISSN ${issn}`;
  document.title = (journal?.name || issn) + " — Journal Tracker";
  if (journal?.homepage) {
    homepageEl.href = journal.homepage;
  } else {
    homepageEl.parentElement.style.display = "none";
  }

  if (journal?.source === "added") {
    removeBtn.hidden = false;
    removeBtn.onclick = () => {
      if (confirm(`Remove "${journal.name}" from your subscriptions?`)) {
        removeAdded(issn);
        location.href = "index.html";
      }
    };
  }

  let items = [];
  try {
    items = await fetchArticles(issn);
  } catch (e) {
    loading.textContent = `Couldn't load articles: ${e.message}`;
    return;
  }
  loading.hidden = true;

  if (!items.length) {
    list.appendChild(el("p", { class: "empty-state" }, "No articles found for this journal yet."));
    setLastVisit(issn);
    return;
  }

  for (const item of items) {
    list.appendChild(renderArticleCard(item));
  }

  // Background: enrich with abstracts
  const dois = items.map((i) => i.DOI).filter(Boolean);
  fetchAbstracts(dois).then((map) => {
    for (const [doi, text] of Object.entries(map)) {
      const card = list.querySelector(`.card[data-doi="${CSS.escape(doi)}"]`);
      if (!card) continue;
      const abs = card.querySelector(".abstract");
      abs.textContent = truncate(text, ABSTRACT_MAX_CHARS);
      abs.hidden = false;
    }
  });

  // Mark this journal as visited (slight delay so the user has a chance to see NEW state if any)
  setTimeout(() => setLastVisit(issn), 2000);
}

function renderArticleCard(item) {
  const title = cleanText((item.title || [""])[0]) || "(untitled)";
  const url = item.URL || (item.DOI ? `https://doi.org/${item.DOI}` : "#");
  const doi = (item.DOI || "").toLowerCase();
  const date = articleDate(item);
  const dateStr = date ? date.toISOString().slice(0, 10) : "—";

  let badge;
  if (item.volume && item.issue) {
    badge = el("span", { class: "badge issue" }, `Vol ${item.volume}, No ${item.issue}`);
  } else if (item.volume) {
    badge = el("span", { class: "badge issue" }, `Vol ${item.volume}`);
  } else {
    badge = el("span", { class: "badge online-first" }, "Online first");
  }

  // Crossref sometimes provides abstract directly (Springer); use it immediately if present.
  const xrefAbs = cleanText(item.abstract);
  const abstractEl = el("p", {
    class: "abstract",
    hidden: !xrefAbs,
  }, xrefAbs ? truncate(xrefAbs, ABSTRACT_MAX_CHARS) : "");

  return el("article", { class: "card", dataset: { doi } },
    el("a", { class: "title", href: url, target: "_blank", rel: "noopener" }, title),
    el("div", { class: "meta" },
      el("span", { class: "authors" }, formatAuthors(item.author)),
      el("span", { class: "meta-right" },
        el("span", { class: "date" }, dateStr),
        badge)),
    abstractEl);
}

// ---------- Boot ----------

if (document.body.classList.contains("page-index")) {
  initIndex();
} else if (document.body.classList.contains("page-journal")) {
  initJournal();
}
