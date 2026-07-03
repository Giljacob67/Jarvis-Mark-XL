/* JARVIS PWA service worker — cache-first for static shell, network for tudo
   que é dinâmico (API, WebSocket upgades nem passam por aqui). */
const CACHE = "jarvis-v1";
const SHELL = ["/static/icon-192.png", "/static/icon-512.png", "/static/icon-180.png", "/manifest.json"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Só cache para o shell estático; página e APIs sempre da rede
  // (o app é inútil offline — o valor é abrir instantâneo e ser instalável).
  if (url.pathname.startsWith("/static/") || url.pathname === "/manifest.json") {
    e.respondWith(
      caches.match(e.request).then((hit) => hit || fetch(e.request).then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return resp;
      }))
    );
  }
});
