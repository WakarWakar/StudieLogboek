// Runs background.js against a fake `browser` API and a fake clock.
//   node --test extension/test
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const SOURCE = fs.readFileSync(path.join(here, "..", "background.js"), "utf8");

function event() {
  const fns = [];
  return { addListener: (f) => fns.push(f), fire: (...a) => fns.forEach((f) => f(...a)) };
}

function storageArea() {
  const data = {};
  return {
    data,
    async get(keys) {
      if (keys == null) return structuredClone(data);
      const list = typeof keys === "string" ? [keys] : keys;
      const out = {};
      for (const k of list) if (k in data) out[k] = structuredClone(data[k]);
      return out;
    },
    async set(obj) { Object.assign(data, structuredClone(obj)); },
  };
}

function setup({ online = true, studyMode = true } = {}) {
  let now = 1_800_000_000_000;
  const tabs = new Map();
  const win = { id: 1, focused: true };
  const companion = { online, studyMode, events: new Map(), active: [] };

  const browser = {
    storage: { local: storageArea(), session: storageArea(), onChanged: event() },
    tabs: {
      get: async (id) => ({ ...tabs.get(id) }),
      query: async () => [...tabs.values()].filter((t) => t.active).map((t) => ({ ...t })),
      onActivated: event(), onUpdated: event(), onRemoved: event(),
    },
    windows: {
      WINDOW_ID_NONE: -1,
      get: async () => ({ ...win }),
      getLastFocused: async (opts) => ({ ...win, tabs: opts && opts.populate ? [...tabs.values()] : undefined }),
      onFocusChanged: event(),
    },
    idle: { setDetectionInterval() {}, onStateChanged: event() },
    runtime: { onMessage: event(), onStartup: event(), onInstalled: event() },
    alarms: { create() {}, onAlarm: event() },
    action: { setIcon: async () => {}, setBadgeText: async () => {}, setBadgeBackgroundColor: async () => {}, setTitle: async () => {} },
    permissions: { contains: async () => true },
  };

  async function fetch(url, opts = {}) {
    if (!companion.online) throw new Error("offline");
    const p = new URL(url).pathname;
    const body = opts.body ? JSON.parse(opts.body) : {};
    let res = {};
    if (p === "/status") res = { studyMode: companion.studyMode, excludedDomains: ["facebook.com"] };
    if (p === "/studymode") companion.studyMode = body.on;
    if (p === "/active") companion.active.push(body.url);
    if (p === "/events") for (const e of body.events) companion.events.set(e.key, e);
    return { ok: true, status: 200, json: async () => res };
  }

  class FakeDate extends Date {
    constructor(...a) { super(...(a.length ? a : [now])); }
    static now() { return now; }
  }

  const ctx = vm.createContext({ browser, fetch, Date: FakeDate, console, URL, URLSearchParams, setTimeout, structuredClone });
  vm.runInContext(SOURCE, ctx);

  const settle = async () => {
    for (let i = 0; i < 5; i++) {
      await vm.runInContext("chain", ctx);
      await new Promise((r) => setTimeout(r, 0));
    }
  };
  const api = {
    browser, companion, win, settle,
    advance: async (sec) => { now += sec * 1000; },
    tick: async () => { browser.alarms.onAlarm.fire({ name: "tick" }); await settle(); },
    open: async (id, url, title = url) => {
      for (const t of tabs.values()) t.active = false;
      tabs.set(id, { id, windowId: 1, url, title, active: true, incognito: false, audible: false });
      browser.tabs.onActivated.fire({ tabId: id, windowId: 1 });
      await settle();
    },
    navigate: async (id, url, title = url) => {
      const t = tabs.get(id);
      t.url = url; t.title = title;
      browser.tabs.onUpdated.fire(id, { url }, { ...t });
      await settle();
    },
    retitle: async (id, title) => {
      const t = tabs.get(id);
      t.title = title;
      browser.tabs.onUpdated.fire(id, { title }, { ...t });
      await settle();
    },
    blur: async () => { win.focused = false; browser.windows.onFocusChanged.fire(-1); await settle(); },
    focus: async () => { win.focused = true; browser.windows.onFocusChanged.fire(1); await settle(); },
    events: () => [...companion.events.values()].sort((a, b) => a.start - b.start),
  };
  return api;
}

test("records pages, PDFs and AI chats with active time; skips excluded sites", async () => {
  const b = setup();
  await b.settle();
  await b.open(1, "https://en.wikipedia.org/wiki/Zero_trust_architecture?utm_source=x&fbclid=y", "Zero trust architecture - Wikipedia");
  await b.advance(60);
  await b.navigate(1, "https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.800-207.pdf", "NIST.SP.800-207.pdf");
  await b.advance(5);
  await b.retitle(1, "Zero Trust Architecture - NIST.SP.800-207.pdf"); // pdf.js sets the real title later
  await b.advance(15);
  await b.blur();          // switched to Word for 30 s: must not count
  await b.advance(30);
  await b.focus();
  await b.advance(20);
  await b.navigate(1, "https://chatgpt.com/c/abc-123", "My secret chat title");
  await b.advance(20);
  await b.navigate(1, "https://www.facebook.com/", "Facebook");
  await b.advance(20);
  await b.navigate(1, "about:blank", "");
  await b.tick();

  const ev = b.events();
  assert.deepEqual(ev.map((e) => e.kind), ["page", "pdf", "ai"]);
  assert.equal(ev[0].url, "https://en.wikipedia.org/wiki/Zero_trust_architecture");
  assert.equal(ev[0].seconds, 60);
  assert.equal(ev[1].seconds, 40, "only focused time counts");
  assert.equal(ev[1].title, "Zero Trust Architecture - NIST.SP.800-207.pdf");
  assert.equal(ev[2].url, "https://chatgpt.com/", "AI chats keep only the site");
  assert.equal(ev[2].title, "ChatGPT", "never the chat title");
  assert.ok(!ev.some((e) => e.url.includes("facebook")));
  assert.ok(!b.companion.active.some((u) => u.includes("facebook")));
});

test("one search event even when the engine rewrites its URL", async () => {
  const b = setup();
  await b.settle();
  await b.open(1, "https://duckduckgo.com/?q=zero+trust+segmentation");
  await b.advance(3);
  await b.navigate(1, "https://duckduckgo.com/?q=zero+trust+segmentation&ia=web");
  await b.advance(20);
  await b.navigate(1, "https://www.google.com/search?q=illumio+rest+api&sca_esv=1&ei=2");
  await b.advance(4);
  await b.navigate(1, "about:blank");
  await b.tick();
  const s = b.events().filter((e) => e.kind === "search");
  assert.equal(s.length, 2);
  assert.equal(s[0].meta.query, "zero trust segmentation");
  assert.equal(s[0].seconds, 23);
  assert.equal(s[1].url, "https://www.google.com/search?q=illumio+rest+api");
});

test("a visit started while Firefox is in the background only counts once focused", async () => {
  const b = setup();
  await b.settle();
  await b.blur();
  await b.open(1, "https://learn.microsoft.com/en-us/windows/win32/fwp/windows-filtering-platform-start-page", "WFP");
  await b.advance(50);
  await b.focus();
  await b.advance(25);
  await b.navigate(1, "about:blank");
  await b.tick();
  const [e] = b.events();
  assert.equal(e.seconds, 25);
});

test("short visits are dropped, long visits are checkpointed while still open", async () => {
  const b = setup();
  await b.settle();
  await b.open(1, "https://example.org/a", "A");
  await b.advance(4);
  await b.navigate(1, "https://example.org/b", "B");
  await b.advance(40);
  await b.tick(); // 30 s alarm while still reading B
  assert.deepEqual(b.events().map((e) => [e.title, e.seconds]), [["B", 40]]);
  await b.advance(20);
  await b.navigate(1, "about:blank");
  await b.tick();
  assert.deepEqual(b.events().map((e) => [e.title, e.seconds]), [["B", 60]]);
});

test("events are queued while the companion is offline and sent later", async () => {
  const b = setup({ online: false });
  await b.browser.storage.local.set({ studyMode: true }); // was switched on earlier
  await b.settle();
  await b.open(1, "https://example.org/offline", "Offline page");
  await b.advance(30);
  await b.navigate(1, "about:blank");
  await b.tick();
  assert.equal(b.events().length, 0);
  assert.equal((await b.browser.storage.local.get("queue")).queue.length, 1);
  b.companion.online = true;
  await b.tick();
  assert.equal(b.events().length, 1);
  assert.equal((await b.browser.storage.local.get("queue")).queue.length, 0);
});

test("YouTube watch pages are recorded from the player, not as pages", async () => {
  const b = setup();
  await b.settle();
  await b.open(5, "https://www.youtube.com/watch?v=BHC7z6YQH94&si=track&pp=x", "Video - YouTube");
  await b.advance(60);
  b.browser.runtime.onMessage.fire(
    { type: "video", key: "yt-1", videoId: "BHC7z6YQH94", start: Date.now(), watched: 48, title: "Illumio", channel: "Tech Field Day", duration: 2290 },
    { tab: { id: 5, incognito: false } },
  );
  await b.navigate(5, "about:blank");
  await b.tick();
  const ev = b.events();
  assert.deepEqual(ev.map((e) => e.kind), ["video"]);
  assert.equal(ev[0].seconds, 48);
  assert.equal(ev[0].meta.channel, "Tech Field Day");
});

test("turning study mode off from the tray stops recording", async () => {
  const b = setup();
  await b.settle();
  await b.open(1, "https://example.org/one", "One");
  await b.advance(30);
  b.companion.studyMode = false;
  await b.tick(); // poll notices the tray change
  await b.advance(5);
  await b.navigate(1, "https://example.org/two", "Two");
  await b.advance(60);
  await b.navigate(1, "about:blank");
  await b.tick();
  assert.deepEqual(b.events().map((e) => e.title), ["One"]);
});
