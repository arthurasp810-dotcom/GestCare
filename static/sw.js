/* Service Worker — cache para funcionamento offline */
const CACHE = 'casa-idosos-v2';
const ASSETS = [
  '/',
  '/agenda',
  '/static/css/style.css',
  '/static/js/app.js',
  '/static/manifest.json',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/apple-touch-icon.png',
  '/static/icons/favicon-32.png'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => c.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(
        keys.filter(k => k !== CACHE).map(k => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

// Network first — se cair a rede, usa o cache
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});

// ── NOTIFICAÇÕES PUSH (lembrete de antibiótico, etc.) ───────
self.addEventListener('push', e => {
  let dados = { title: 'GestCare', body: 'Você tem uma nova notificação.' };
  try {
    if (e.data) dados = e.data.json();
  } catch (err) {
    if (e.data) dados.body = e.data.text();
  }
  const opcoes = {
    body: dados.body || '',
    tag: dados.url || 'gestcare-notificacao',
    renotify: true,
    data: { url: dados.url || '/' }
  };
  e.waitUntil(self.registration.showNotification(dados.title || 'GestCare', opcoes));
});

// Clique na notificação abre (ou foca) a página do paciente
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(janelas => {
      for (const cliente of janelas) {
        if (cliente.url.includes(url) && 'focus' in cliente) return cliente.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});