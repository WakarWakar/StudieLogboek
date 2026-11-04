/* Studielogboek background (event page).
 *
 * While Study Mode is on, tracks the active tab of the focused Firefox window and
 * records one event per visit with the time it was actually in front of you.
 * Events are queued in storage.local and sent to the companion app on
 * http://127.0.0.1:5176. When the companion is not running they stay queued.
 */
"use strict";

const COMPANION = "http://127.0.0.1:5176";
const MIN_VISIT_SECONDS = 10;
const MIN_VIDEO_SECONDS = 10;
const MAX_QUEUE = 20000;
const STATUS_MAX_AGE_MS = 10000;

const TRACKING_PARAMS = [
  /^utm_/i, /^fbclid$/i, /^gclid$/i, /^dclid$/i, /^msclkid$/i, /^mc_[ce]id$/i, /^igshid$/i,
  /^_hs(enc|mi)$/i, /^yclid$/i, /^ref_src$/i, /^spm$/i, /^si$/i, /^feature$/i, /^pp$/i, /^ab_channel$/i,
  /^__twitter_impression$/i, /^oly_(anon|enc)_id$/i, /^vero_/i, /^wt_?mc/i, /^cmpid$/i,
];

const AI_SITES = {
  "chatgpt.com": "ChatGPT",
  "chat.openai.com": "ChatGPT",
  "claude.ai": "Claude",
  "gemini.google.com": "Gemini",
  "copilot.microsoft.com": "Microsoft Copilot",
  "www.perplexity.ai": "Perplexity",
  "perplexity.ai": "Perplexity",
  "chat.mistral.ai": "Le Chat (Mistral)",
};

const SITE_NAMES = {
  "www.youtube.com": "YouTube", "youtube.com": "YouTube", "m.youtube.com": "YouTube",
  "github.com": "GitHub", "stackoverflow.com": "Stack Overflow", "medium.com": "Medium",
  "learn.microsoft.com": "Microsoft Learn", "developer.mozilla.org": "MDN Web Docs",
  "docs.python.org": "Python Documentation", "scholar.google.com": "Google Scholar",
};

// ---------------------------------------------------------------- utilities

let chain = Promise.resolve();
/** Serialise all state changes: event pages get many events at once. */
function serial(fn) {
  chain = chain.then(fn).catch((e) => console.error("[studielogboek]", e));
  return chain;
}

async function getLocal(keys) { return browser.storage.local.get(keys); }
async function setLocal(obj) { return browser.storage.local.set(obj); }
async function getCurrent() { return (await browser.storage.session.get("current")).current || null; }
async function setCurrent(cur) { return browser.storage.session.set({ current: cur }); }

function hostOf(url) { try { return new URL(url).hostname.toLowerCase(); } catch { return ""; } }

function siteName(host) {
  if (SITE_NAMES[host]) return SITE_NAMES[host];
  if (host.endsWith(".wikipedia.org")) return "Wikipedia";
  return host.replace(/^www\./, "");
}

function cleanUrl(raw) {
  let u;
  try { u = new URL(raw); } catch { return raw; }
  u.hash = "";
  const host = u.hostname.toLowerCase();
  if ((host.endsWith("youtube.com")) && u.pathname === "/watch") {
    const keep = new URLSearchParams();
    for (const k of ["v", "list"]) if (u.searchParams.has(k)) keep.set(k, u.searchParams.get(k));
    u.search = keep.toString();
    return u.toString();
  }
  if (/(^|\.)google\.[a-z.]+$/.test(host) && u.pathname === "/search") {
    const keep = new URLSearchParams();
    for (const k of ["q", "tbm", "udm"]) if (u.searchParams.has(k)) keep.set(k, u.searchParams.get(k));
    u.search = keep.toString();
    return u.toString();
  }
  for (const key of [...u.searchParams.keys()]) {
    if (TRACKING_PARAMS.some((re) => re.test(key))) u.searchParams.delete(key);
  }
  return u.toString();
}

function isExcluded(url, excluded) {
  const host = hostOf(url);
  return (excluded || []).some((p) => p && host.includes(p.toLowerCase()));
}

/** Decide what a URL is. Returns null for things we never record. */
function classify(rawUrl, title) {
  let u;
  try { u = new URL(rawUrl); } catch { return null; }
  if (u.protocol === "file:") {
    if (/\.pdf$/i.test(u.pathname)) {
      const name = decodeURIComponent(u.pathname.split("/").pop());
      return { kind: "pdf", identity: rawUrl, url: rawUrl, title: title || name, site: "Lokaal bestand" };
    }
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  if (u.origin === COMPANION) return null;
  const host = u.hostname.toLowerCase();
  const p = u.searchParams;

  // AI chats: only the service and the time spent, never the conversation
  if (AI_SITES[host]) {
    return { kind: "ai", identity: "ai:" + host, url: u.origin + "/", title: AI_SITES[host], site: AI_SITES[host] };
  }
  const isGoogle = /(^|\.)google\.[a-z.]+$/.test(host);
  if (isGoogle && u.pathname === "/search" && p.get("udm") === "50") {
    return { kind: "ai", identity: "ai:google-ai-mode", url: u.origin + "/search?udm=50", title: "Google AI Mode", site: "Google AI Mode" };
  }

  // Searches
  let engine = null, query = null;
  if (host.startsWith("scholar.google.") && u.pathname.startsWith("/scholar") && p.get("q")) { engine = "Google Scholar"; query = p.get("q"); }
  else if (isGoogle && u.pathname === "/search" && p.get("q")) { engine = "Google"; query = p.get("q"); }
  else if (host.endsWith("bing.com") && u.pathname === "/search" && p.get("q")) { engine = "Bing"; query = p.get("q"); }
  else if (host.endsWith("duckduckgo.com") && p.get("q")) { engine = "DuckDuckGo"; query = p.get("q"); }
  else if (host.endsWith("wikipedia.org") && p.get("search")) { engine = "Wikipedia"; query = p.get("search"); }
  else if (host.endsWith("youtube.com") && u.pathname === "/results" && p.get("search_query")) { engine = "YouTube"; query = p.get("search_query"); }
  if (engine) {
    const url = cleanUrl(rawUrl);
    // identity on engine + query: engines rewrite their own URL after loading (DuckDuckGo adds &ia=web)
    return { kind: "search", identity: `search:${engine}:${query}`, url, title: query, site: engine, meta: { engine, query } };
  }

  // YouTube watch pages are recorded by content/youtube.js (watched seconds), not as pages
  if (host.endsWith("youtube.com") && u.pathname === "/watch") {
    return { kind: "videopage", identity: cleanUrl(rawUrl), url: cleanUrl(rawUrl), title, site: "YouTube" };
  }

  const url = cleanUrl(rawUrl);
  const kind = /\.pdf$/i.test(u.pathname) ? "pdf" : "page";
  return { kind, identity: url, url, title: title || url, site: siteName(host) };
}

// ---------------------------------------------------------------- the event queue

async function enqueue(ev) {
  const { queue = [] } = await getLocal("queue");
  const i = ev.key ? queue.findIndex((q) => q.key === ev.key) : -1;
  if (i >= 0) queue[i] = ev; else queue.push(ev);
  while (queue.length > MAX_QUEUE) queue.shift();
  await setLocal({ queue });
}

let flushing = false;
async function flush() {
  if (flushing) return;
  flushing = true;
  try {
    for (;;) {
      const { queue = [] } = await getLocal("queue");
      if (!queue.length) return;
      const batch = queue.slice(0, 200);
      const res = await fetch(COMPANION + "/events", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ events: batch }),
      });
      if (!res.ok) throw new Error("companion " + res.status);
      // Remove what we sent, but keep anything updated in the meantime.
      const sent = new Map(batch.map((e) => [e.key, JSON.stringify(e)]));
      const { queue: now = [] } = await getLocal("queue");
      await setLocal({ queue: now.filter((e) => sent.get(e.key) !== JSON.stringify(e)) });
      await setLocal({ online: true, lastSync: Date.now() });
    }
  } catch {
    await setLocal({ online: false });
  } finally {
    flushing = false;
  }
}

// ---------------------------------------------------------------- study mode + companion sync

async function syncStatus(force = false) {
  const { lastStatus = 0, pendingMode = null, studyMode = false } = await getLocal(["lastStatus", "pendingMode", "studyMode"]);
  if (!force && Date.now() - lastStatus < STATUS_MAX_AGE_MS) return;
  await setLocal({ lastStatus: Date.now() });
  try {
    if (pendingMode !== null) {
      await fetch(COMPANION + "/studymode", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on: pendingMode }),
      });
      await setLocal({ pendingMode: null });
    }
    const res = await fetch(COMPANION + "/status");
    const st = await res.json();
    // Sites excluded from the popup while the app was offline are pushed now.
    const { localExcluded = [] } = await getLocal("localExcluded");
    const remote = st.excludedDomains || [];
    for (const host of localExcluded.filter((h) => !remote.includes(h))) {
      await fetch(COMPANION + "/exclude", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: host }),
      });
    }
    await setLocal({ online: true, excluded: [...new Set([...remote, ...localExcluded])] });
    if (st.studyMode !== studyMode) await applyStudyMode(st.studyMode);
  } catch {
    await setLocal({ online: false });
  }
}

async function applyStudyMode(on) {
  await setLocal({ studyMode: on });
  await updateBadge(on);
  if (on) await resumeFromActiveTab();
  else await finishCurrent();
}

/** Called by the popup. Works offline too: the change is pushed once the companion is reachable. */
async function setStudyModeFromPopup(on) {
  await setLocal({ pendingMode: on });
  await applyStudyMode(on);
  await syncStatus(true);
  await flush();
}

async function updateBadge(on) {
  const s = on ? "on" : "off";
  await browser.action.setIcon({ path: { 16: `icons/${s}-16.png`, 32: `icons/${s}-32.png` } });
  await browser.action.setBadgeText({ text: on ? "AAN" : "" });
  await browser.action.setBadgeBackgroundColor({ color: "#2ea043" });
  await browser.action.setTitle({ title: on ? "Studielogboek - studiemodus AAN" : "Studielogboek - studiemodus uit" });
}

// ---------------------------------------------------------------- visit tracking

function secondsOf(cur, now = Date.now()) {
  return (cur.activeMs + (cur.runningSince ? now - cur.runningSince : 0)) / 1000;
}

function toEvent(cur, now = Date.now()) {
  return {
    key: cur.key, kind: cur.kind, start: cur.start / 1000, end: now / 1000,
    seconds: Math.round(secondsOf(cur, now)), title: cur.title, url: cur.url, site: cur.site, meta: cur.meta || {},
  };
}

async function finishCurrent() {
  const cur = await getCurrent();
  if (!cur) return;
  await setCurrent(null);
  if (cur.kind === "videopage") return;
  const ev = toEvent(cur);
  if (cur.kind === "search" || ev.seconds >= MIN_VISIT_SECONDS || cur.checkpointed) {
    await enqueue(ev);
    flush();
  }
}

async function metaFor(url) {
  const { metaCache = {} } = await browser.storage.session.get("metaCache");
  return metaCache[url] || null;
}

async function startVisit(tab, focused) {
  const { studyMode, excluded } = await getLocal(["studyMode", "excluded"]);
  if (!studyMode || !tab || tab.incognito || !tab.url) return;
  const c = classify(tab.url, tab.title);
  if (!c || isExcluded(tab.url, excluded)) return;
  const now = Date.now();
  const cached = await metaFor(c.url);
  const cur = {
    key: `ff-${now}-${tab.id}`, tabId: tab.id, windowId: tab.windowId,
    identity: c.identity, kind: c.kind, url: c.url, title: c.title, site: (cached && cached.siteName) || c.site,
    meta: { ...(c.meta || {}), ...(cached || {}) },
    start: now, activeMs: 0, runningSince: focused ? now : null, checkpointed: false,
  };
  if (c.kind === "ai") cur.meta = {}; // never keep page details of AI chats
  await setCurrent(cur);
  // Tell the companion which page is in front, so a quick note can be attached to it.
  fetch(COMPANION + "/active", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: c.url, title: c.title }),
  }).catch(() => {});
}

/** The tab in front changed (or its URL did). */
async function switchTo(tab, focused = true) {
  const cur = await getCurrent();
  if (cur && tab && cur.tabId === tab.id) {
    const c = classify(tab.url, tab.title);
    if (c && c.identity === cur.identity) {
      if (c.kind !== "ai" && tab.title && cur.kind !== "search") cur.title = tab.title;
      if (focused && !cur.runningSince) cur.runningSince = Date.now();
      await setCurrent(cur);
      return;
    }
  }
  await finishCurrent();
  await startVisit(tab, focused);
}

async function pause() {
  const cur = await getCurrent();
  if (cur && cur.runningSince) {
    cur.activeMs += Date.now() - cur.runningSince;
    cur.runningSince = null;
    await setCurrent(cur);
  }
}

async function resume() {
  const cur = await getCurrent();
  if (cur && !cur.runningSince) {
    cur.runningSince = Date.now();
    await setCurrent(cur);
  }
}

async function resumeFromActiveTab() {
  const win = await browser.windows.getLastFocused({ populate: true }).catch(() => null);
  if (!win) return;
  const tab = (win.tabs || []).find((t) => t.active);
  await switchTo(tab, !!win.focused);
}

/** Every 30 s: save progress of the running visit so a crash or shutdown loses little. */
async function checkpoint() {
  const cur = await getCurrent();
  if (!cur || cur.kind === "videopage") return;
  if (secondsOf(cur) >= MIN_VISIT_SECONDS) {
    cur.checkpointed = true;
    await setCurrent(cur);
    await enqueue(toEvent(cur));
  }
}

// ---------------------------------------------------------------- browser events

browser.tabs.onActivated.addListener(({ tabId }) => serial(async () => {
  await syncStatus();
  const tab = await browser.tabs.get(tabId).catch(() => null);
  const win = tab ? await browser.windows.get(tab.windowId).catch(() => null) : null;
  await switchTo(tab, !!(win && win.focused));
}));

browser.tabs.onUpdated.addListener((tabId, info, tab) => {
  if (!tab.active || !(info.url || info.title || info.status === "complete")) return;
  serial(async () => {
    // Only the front tab of the last focused window counts. If Firefox itself is in the
    // background the visit still starts, but its clock runs only once Firefox gets focus.
    const last = await browser.windows.getLastFocused().catch(() => null);
    if (!last || last.id !== tab.windowId) return;
    await switchTo(tab, !!last.focused);
  });
}, { properties: ["url", "title", "status"] });

browser.tabs.onRemoved.addListener((tabId) => serial(async () => {
  const cur = await getCurrent();
  if (cur && cur.tabId === tabId) await finishCurrent();
}));

browser.windows.onFocusChanged.addListener((windowId) => serial(async () => {
  if (windowId === browser.windows.WINDOW_ID_NONE) return pause();
  await syncStatus();
  const [tab] = await browser.tabs.query({ active: true, windowId });
  await switchTo(tab, true);
}));

browser.idle.setDetectionInterval(120);
browser.idle.onStateChanged.addListener((state) => serial(async () => {
  if (state === "active") {
    const win = await browser.windows.getLastFocused().catch(() => null);
    if (win && win.focused) await resume();
    return;
  }
  const cur = await getCurrent();
  if (state === "idle" && cur) {
    // Watching a video or listening to a lecture without touching the mouse still counts.
    const tab = await browser.tabs.get(cur.tabId).catch(() => null);
    if (tab && tab.audible) return;
  }
  await pause();
}));

browser.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === "popup:setStudyMode") return serial(() => setStudyModeFromPopup(!!msg.on));
  if (msg && msg.type === "popup:sync") return serial(async () => { await syncStatus(true); await flush(); });
  if (msg && msg.type === "popup:exclude") return serial(() => excludeHost(msg.host));
  if (!sender.tab || sender.tab.incognito) return;
  if (msg.type === "meta") return serial(() => onMeta(msg, sender.tab));
  if (msg.type === "video") return serial(() => onVideo(msg));
});

async function excludeHost(host) {
  if (!host) return;
  const { excluded = [], localExcluded = [] } = await getLocal(["excluded", "localExcluded"]);
  if (!excluded.includes(host)) excluded.push(host);
  if (!localExcluded.includes(host)) localExcluded.push(host);
  await setLocal({ excluded, localExcluded });
  const cur = await getCurrent();
  if (cur && isExcluded(cur.url, excluded)) await setCurrent(null);
  // also drop anything from this host that is still waiting in the queue
  const { queue = [] } = await getLocal("queue");
  await setLocal({ queue: queue.filter((e) => !isExcluded(e.url, excluded)) });
  fetch(COMPANION + "/exclude", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ domain: host }),
  }).catch(() => {});
}

async function onMeta(msg, tab) {
  const { studyMode } = await getLocal("studyMode");
  if (!studyMode) return;
  const url = cleanUrl(msg.url);
  const { metaCache = {} } = await browser.storage.session.get("metaCache");
  metaCache[url] = msg.meta;
  const keys = Object.keys(metaCache);
  if (keys.length > 60) delete metaCache[keys[0]];
  await browser.storage.session.set({ metaCache });
  const cur = await getCurrent();
  if (cur && cur.tabId === tab.id && cur.url === url && cur.kind !== "ai" && cur.kind !== "search") {
    cur.meta = { ...cur.meta, ...msg.meta };
    if (msg.meta.siteName) cur.site = msg.meta.siteName;
    await setCurrent(cur);
  }
}

async function onVideo(v) {
  const { studyMode, excluded } = await getLocal(["studyMode", "excluded"]);
  const url = `https://www.youtube.com/watch?v=${v.videoId}`;
  if (!studyMode || isExcluded(url, excluded) || v.watched < MIN_VIDEO_SECONDS) return;
  await enqueue({
    key: v.key, kind: "video", start: v.start / 1000, end: Date.now() / 1000, seconds: Math.round(v.watched),
    title: v.title, url, site: "YouTube",
    meta: { channel: v.channel, duration: v.duration, published: v.published, videoId: v.videoId },
  });
  flush();
}

// ---------------------------------------------------------------- startup

browser.alarms.create("tick", { periodInMinutes: 0.5 });
browser.alarms.onAlarm.addListener((a) => {
  if (a.name !== "tick") return;
  serial(async () => { await syncStatus(true); await checkpoint(); await flush(); });
});

async function init() {
  const { studyMode = false, excluded } = await getLocal(["studyMode", "excluded"]);
  if (!excluded) await setLocal({ excluded: [] });
  await updateBadge(studyMode);
  await syncStatus(true);
  const { studyMode: on } = await getLocal("studyMode");
  if (on && !(await getCurrent())) await resumeFromActiveTab();
  await flush();
}
browser.runtime.onStartup.addListener(() => serial(init));
browser.runtime.onInstalled.addListener(() => serial(init));
serial(init);
