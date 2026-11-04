"use strict";
const $ = (id) => document.getElementById(id);
const ALL = { origins: ["<all_urls>"] };

async function render() {
  const st = await browser.storage.local.get(["studyMode", "online", "queue", "excluded"]);
  $("mode").checked = !!st.studyMode;
  $("modeText").textContent = st.studyMode ? "Studiemodus AAN" : "Studiemodus uit";

  const conn = $("conn");
  conn.textContent = st.online ? "verbonden" : "app niet actief";
  conn.className = "pill " + (st.online ? "ok" : "bad");
  const n = (st.queue || []).length;
  $("queue").textContent = n ? `${n} item(s) wachten tot de Studielogboek-app draait.` : "";

  $("perm").hidden = await browser.permissions.contains(ALL);

  const [tab] = await browser.tabs.query({ active: true, currentWindow: true });
  let host = "";
  try { host = new URL(tab.url).hostname; } catch { /* about: pages */ }
  $("pageTitle").textContent = tab.title || tab.url;
  const excluded = (st.excluded || []).some((p) => p && host.includes(p.toLowerCase()));
  const trackable = /^https?:|^file:/.test(tab.url || "");
  $("pageState").textContent = !trackable ? "Deze pagina wordt niet bijgehouden."
    : excluded ? "Deze site is uitgesloten en wordt nooit opgeslagen."
    : st.studyMode ? "Wordt bijgehouden." : "Zet studiemodus aan om bij te houden.";
  $("exclude").hidden = !host || excluded;
  $("exclude").dataset.host = host;
}

$("mode").addEventListener("change", async (e) => {
  await browser.runtime.sendMessage({ type: "popup:setStudyMode", on: e.target.checked });
  render();
});
$("grant").addEventListener("click", async () => { await browser.permissions.request(ALL); render(); });
$("exclude").addEventListener("click", async (e) => {
  await browser.runtime.sendMessage({ type: "popup:exclude", host: e.target.dataset.host });
  render();
});

render();
browser.runtime.sendMessage({ type: "popup:sync" }).then(render).catch(() => {});
