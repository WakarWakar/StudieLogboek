/* Reads citation details (author, date, site name) from a page's <meta> tags, so the
 * Word export can build an APA reference. It never reads the page text itself. */
(async () => {
  "use strict";
  if (window.top !== window) return;
  const { studyMode } = await browser.storage.local.get("studyMode");
  if (!studyMode) return;

  const pick = (...sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      const v = el && (el.getAttribute("content") || el.getAttribute("datetime") || el.textContent);
      if (v && v.trim()) return v.trim();
    }
    return null;
  };
  const all = (sel) => [...document.querySelectorAll(sel)].map((e) => (e.getAttribute("content") || "").trim()).filter(Boolean);

  let authors = all('meta[name="citation_author"], meta[name="dc.creator"], meta[name="DC.creator"]');
  if (!authors.length) {
    const a = pick('meta[name="author"]', 'meta[property="article:author"]', 'meta[name="parsely-author"]');
    if (a && !/^https?:/.test(a)) authors = [a];
  }
  // JSON-LD often carries author and date on news and blog sites
  let ld = {};
  try {
    for (const s of document.querySelectorAll('script[type="application/ld+json"]')) {
      const data = JSON.parse(s.textContent);
      const items = Array.isArray(data) ? data : data["@graph"] || [data];
      const art = items.find((i) => i && /Article|BlogPosting|Report|WebPage|ScholarlyArticle/.test(String(i["@type"])));
      if (art) { ld = art; break; }
    }
  } catch { /* ignore broken JSON-LD */ }
  if (!authors.length && ld.author) {
    const list = Array.isArray(ld.author) ? ld.author : [ld.author];
    authors = list.map((a) => (typeof a === "string" ? a : a && a.name)).filter(Boolean);
  }

  const meta = {
    siteName: pick('meta[property="og:site_name"]', 'meta[name="application-name"]') ||
      (ld.publisher && ld.publisher.name) || null,
    authors: authors.slice(0, 20),
    published: pick('meta[name="citation_publication_date"]', 'meta[name="citation_date"]',
      'meta[property="article:published_time"]', 'meta[name="dc.date"]', 'meta[name="DC.date"]',
      'meta[name="date"]', 'meta[itemprop="datePublished"]', "time[datetime]") || ld.datePublished || null,
    citationTitle: pick('meta[name="citation_title"]'),
    journal: pick('meta[name="citation_journal_title"]', 'meta[name="citation_conference_title"]'),
    doi: pick('meta[name="citation_doi"]'),
  };
  for (const k of Object.keys(meta)) if (meta[k] == null || (Array.isArray(meta[k]) && !meta[k].length)) delete meta[k];
  if (Object.keys(meta).length) browser.runtime.sendMessage({ type: "meta", url: location.href, meta }).catch(() => {});
})();
