/* WebGrab 的 Service Worker：只缓存静态外壳，绝不碰 /api/*
   规则：
   - 非 GET、跨源、/api/*、带 Range 的请求（视频拖动进度）一律直连
   - 页面导航：先网络，失败回落缓存的外壳
   - 静态资源（/static、/icons、/demo）：先缓存，后台悄悄更新
*/
const VERSION = 'webgrab-v1';
const SHELL = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/static/style.css',
  '/static/app.js',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/favicon.ico'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(VERSION)
      .then((cache) => Promise.all(SHELL.map((url) => cache.add(url).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== VERSION).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function isStatic(pathname) {
  return pathname.startsWith('/static/') || pathname.startsWith('/icons/') || pathname.startsWith('/demo/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  let url;
  try {
    url = new URL(request.url);
  } catch (err) {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;
  if (request.headers.has('range')) return;

  // 页面导航：网络优先，离线时用缓存外壳
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((response) => {
          if (response && response.ok) {
            const copy = response.clone();
            caches.open(VERSION).then((cache) => cache.put('/index.html', copy)).catch(() => {});
          }
          return response;
        })
        .catch(() => caches.match(request).then((hit) => hit || caches.match('/index.html')))
    );
    return;
  }

  if (!isStatic(url.pathname) && url.pathname !== '/manifest.webmanifest' && url.pathname !== '/favicon.ico') {
    return;
  }

  // 静态资源：缓存优先 + 后台刷新
  event.respondWith(
    caches.open(VERSION).then(async (cache) => {
      const hit = await cache.match(request);
      const network = fetch(request)
        .then((response) => {
          if (response && response.ok && response.status === 200) {
            cache.put(request, response.clone()).catch(() => {});
          }
          return response;
        })
        .catch(() => null);
      return hit || network || Response.error();
    })
  );
});
