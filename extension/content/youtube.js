/* Counts how many seconds of a YouTube video you actually watched (ads not counted)
 * and reports title, channel, length and upload date. */
(() => {
  "use strict";
  let studyMode = false;
  let cur = null; // { key, videoId, start, watched, sentAt }

  browser.storage.local.get("studyMode").then((r) => { studyMode = !!r.studyMode; });
  browser.storage.onChanged.addListener((ch, area) => {
    if (area === "local" && ch.studyMode) {
      studyMode = !!ch.studyMode.newValue;
      if (!studyMode) { send(); cur = null; }
    }
  });

  const videoIdFromUrl = () => (location.pathname === "/watch" ? new URLSearchParams(location.search).get("v") : null);

  function details(videoId) {
    const d = { title: null, channel: null, duration: null, published: null };
    try {
      const player = document.getElementById("movie_player");
      const w = player && player.wrappedJSObject; // Firefox: call the page's player API
      const resp = w && typeof w.getPlayerResponse === "function" ? w.getPlayerResponse() : null;
      if (resp && resp.videoDetails && resp.videoDetails.videoId === videoId) {
        d.title = String(resp.videoDetails.title || "") || null;
        d.channel = String(resp.videoDetails.author || "") || null;
        d.duration = Number(resp.videoDetails.lengthSeconds) || null;
        const mf = resp.microformat && resp.microformat.playerMicroformatRenderer;
        if (mf) d.published = String(mf.publishDate || mf.uploadDate || "") || null;
      }
    } catch { /* fall back to the page */ }
    if (!d.title) {
      const h = document.querySelector("ytd-watch-metadata h1 yt-formatted-string, h1.ytd-watch-metadata");
      d.title = (h && h.textContent.trim()) || document.title.replace(/ - YouTube$/, "");
    }
    if (!d.channel) {
      const c = document.querySelector("ytd-watch-metadata #owner #channel-name a, #owner #channel-name a");
      d.channel = c ? c.textContent.trim() : null;
    }
    if (!d.duration) {
      const v = document.querySelector("video");
      if (v && isFinite(v.duration)) d.duration = Math.round(v.duration);
    }
    return d;
  }

  function send() {
    if (!cur || cur.watched < 1) return;
    browser.runtime.sendMessage({
      type: "video", key: cur.key, videoId: cur.videoId, start: cur.start, watched: cur.watched, ...details(cur.videoId),
    }).catch(() => {});
    cur.sentAt = Date.now();
  }

  setInterval(() => {
    const id = videoIdFromUrl();
    if (!studyMode) return;
    if (!cur || cur.videoId !== id) {
      send();
      cur = id ? { key: `yt-${id}-${Date.now()}`, videoId: id, start: Date.now(), watched: 0, sentAt: Date.now() } : null;
    }
    if (!cur) return;
    const video = document.querySelector("#movie_player video, video");
    const player = document.getElementById("movie_player");
    const ad = player && player.classList.contains("ad-showing");
    if (video && !video.paused && !video.ended && video.readyState > 2 && !ad) cur.watched += 1;
    if (Date.now() - cur.sentAt > 15000) send();
  }, 1000);

  window.addEventListener("pagehide", send);
})();
