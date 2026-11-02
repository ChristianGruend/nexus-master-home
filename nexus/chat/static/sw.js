/* NEXUS Mobile PWA service worker - phase6 / mcall7 / m2 / irobot8 (Redesign 2026-10-21) */
const CACHE = "nexus-m-v14-avatars4";
const SHELL = [
  "/m2",
  "/static/css/m2.css?v=m2d",
  "/static/js/m2.js?v=m2d",
  "/static/assets/m2/nexus_face.webp",
  "/static/assets/m2/hermes.webp",
  "/static/assets/m2/aether.webp",
  "/static/css/irobot-m2.css?v=ir8",
  "/static/css/irobot-m.css?v=ir8",
  "/static/js/irobot.js?v=ir8",
  "/static/js/irobot-m2.js?v=ir8",
  "/static/js/irobot-m.js?v=ir8",
  "/static/assets/irobot/hermes.webp",
  "/static/assets/irobot/aether.webp",
  "/static/assets/irobot/earth_bm.webp",
  "/static/assets/irobot/earth_bm_static.webp",
  "/m",
  "/m.html",
  "/static/css/mobile.css?v=mcall7",
  "/static/js/mobile.js?v=mcall7",
  "/static/js/nexus_os_status_m.js?v=os3",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
  "/static/icons/apple-touch-icon.png",
  "/static/assets/icons/icon-192.png",
  "/static/assets/icons/icon-512.png",
  "/static/assets/icons/apple-touch-icon.png",
  "/static/css/nexus-live-avatar.css?v=av4",
  "/static/js/nexus-live-avatar.js?v=av4",
  "/static/js/nexus-live-hook.js?v=av4",
  "/static/assets/avatar/aether.json",
  "/static/assets/avatar/aether.webp",
  "/static/assets/avatar/aether_card.webp",
  "/static/assets/avatar/gpt.json",
  "/static/assets/avatar/gpt.webp",
  "/static/assets/avatar/gpt_card.webp",
  "/static/assets/avatar/hermes.json",
  "/static/assets/avatar/hermes.webp",
  "/static/assets/avatar/hermes_card.webp",
  "/static/assets/avatar/nexus.json",
  "/static/assets/avatar/nexus.webp",
  "/static/assets/avatar/nexus_card.webp",
];

function offlineApiResponse() {
  return new Response(
    JSON.stringify({
      success: false,
      ok: false,
      status: "offline",
      error: "offline",
      offline: true,
      online_only: false,
      message: "NEXUS lokal nicht erreichbar (kein LAN/Host). Shell bleibt offline verfuegbar.",
      nexus_offline: true,
    }),
    {
      status: 503,
      headers: {
        "Content-Type": "application/json; charset=utf-8",
        "X-Nexus-Offline": "1",
        "Cache-Control": "no-store",
      },
    }
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      Promise.all(
        SHELL.map((u) =>
          cache.add(u).catch((err) => {
            console.warn("[NEXUS SW] skip cache", u, err);
          })
        )
      )
    ).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // Never cache the SW script itself via this handler pathing
  if (url.pathname === "/sw.js" || url.pathname === "/static/sw.js") {
    event.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }

  // APIs: network-only + structured offline JSON (no stale API cache)
  if (url.pathname.startsWith("/api/")) {
    event.respondWith(fetch(req).catch(() => offlineApiResponse()));
    return;
  }

  // HTML / navigation: NETWORK-FIRST (avoids stale /m after deploys)
  const isNav =
    req.mode === "navigate" ||
    url.pathname === "/m" ||
    url.pathname === "/m/" ||
    url.pathname === "/m.html" ||
    url.pathname === "/m2" ||
    url.pathname === "/m2/" ||
    url.pathname === "/" ||
    url.pathname === "/index.html";
  if (isNav) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => {
              c.put(req, copy);
              // normalize shell key
              if (url.pathname === "/m2" || url.pathname === "/m2/" || url.pathname === "/m2.html") {
                c.put("/m2", res.clone()).catch(() => {});
              }
              if (url.pathname === "/m" || url.pathname === "/m/" || url.pathname === "/m.html") {
                c.put("/m", res.clone()).catch(() => {});
              }
            }).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(req).then((c) => c || (url.pathname.startsWith("/m2") || url.pathname === "/" ? caches.match("/m2") : null)).then((c) => c || caches.match("/m") || caches.match("/m.html")))
    );
    return;
  }

  // JS/CSS (esp. ?v=): network-first, fallback cache
  if (url.searchParams.has("v") || url.pathname.endsWith(".js") || url.pathname.endsWith(".css")) {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => caches.match(req))
    );
    return;
  }

  // Icons/manifest: cache-first
  event.respondWith(
    caches.match(req).then((cached) => {
      const net = fetch(req)
        .then((res) => {
          if (res && res.ok) {
            const copy = res.clone();
            caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
          }
          return res;
        })
        .catch(() => cached);
      return cached || net;
    })
  );
});

self.addEventListener("message", (event) => {
  if (!event.data) return;
  if (event.data.type === "SKIP_WAITING") self.skipWaiting();
  if (event.data.type === "GET_VERSION" && event.ports && event.ports[0]) {
    event.ports[0].postMessage({ cache: CACHE, phase: 6, shell: "m2b+mcall7" });
  }
});
