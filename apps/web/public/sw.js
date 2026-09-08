/* NETRA Camera service worker.
 *
 *  Deliberately minimal. Its job is to make the capture page installable
 *  and to survive a brief network blip while walking between cells — not
 *  to cache the console. A surveillance console must never serve stale
 *  alerts or stale frames from a cache, so every /api request goes to the
 *  network and is never stored.
 */
const CACHE = "netra-shell-v1";
const SHELL = ["/capture", "/manifest.webmanifest", "/icon.svg"];

self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // A failed precache must not block installation; the page still
      // works online, and an uninstallable PWA is the worse outcome.
      .then((c) => Promise.allSettled(SHELL.map((u) => c.add(u))))
      .then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.pathname.startsWith("/api")) return;
  if (url.origin !== self.location.origin) return;

  // Network first: the phone should always prefer the real page, and
  // fall back to the cached shell only when the tunnel drops.
  e.respondWith(
    fetch(req)
      .then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {});
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/capture"))));
});
