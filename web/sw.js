const CACHE='ai-news-settings-v4';

self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then(cache => cache.addAll(['./','./index.html','./manifest.webmanifest']))
  );
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // The settings page previously called broken/stale workflow files.
  // Route every manual-news dispatch to the long-standing, valid workflow.
  if (
    event.request.method === 'POST' &&
    url.hostname === 'api.github.com' &&
    /\/repos\/aar9937\/ai-news-push\/actions\/workflows\/(daily_news\.yml|manual_news\.yml|manual_news_v2\.yml|news_now\.yml)\/dispatches$/.test(url.pathname)
  ) {
    const fixedUrl = 'https://api.github.com/repos/aar9937/ai-news-push/actions/workflows/market_watch.yml/dispatches';
    event.respondWith((async () => {
      const body = await event.request.clone().arrayBuffer();
      const headers = new Headers(event.request.headers);
      return fetch(fixedUrl, {
        method: 'POST',
        headers,
        body,
        mode: 'cors',
        credentials: 'omit',
        cache: 'no-store',
        redirect: 'follow'
      });
    })());
    return;
  }

  // Network first so app updates are not stuck behind an old cache.
  event.respondWith((async () => {
    try {
      const response = await fetch(event.request);
      if (event.request.method === 'GET' && response && response.ok && url.origin === self.location.origin) {
        const copy = response.clone();
        caches.open(CACHE).then(cache => cache.put(event.request, copy));
      }
      return response;
    } catch (err) {
      const cached = await caches.match(event.request);
      if (cached) return cached;
      throw err;
    }
  })());
});
