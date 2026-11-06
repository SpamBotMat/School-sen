/* School Sensor — service worker: оболочка приложения работает без сети.
   Данные (/api/) сюда не попадают: их кэширует само приложение в localStorage. */
'use strict';
var CACHE = 'ss-__V__';
var SHELL = ['/app/', '/app/app.css?v=__V__', '/app/app.js?v=__V__', '/app/manrope.woff2', '/brand/mark.svg?v=__V__'];

self.addEventListener('install', function (e) {
  e.waitUntil(caches.open(CACHE).then(function (c) {
    return Promise.all(SHELL.map(function (u) {
      return c.add(new Request(u, { cache: 'reload' })).catch(function () { /* докачаем позже */ });
    }));
  }).then(function () { return self.skipWaiting(); }));
});

self.addEventListener('activate', function (e) {
  e.waitUntil(caches.keys().then(function (keys) {
    return Promise.all(keys.filter(function (k) { return k.indexOf('ss-') === 0 && k !== CACHE; })
      .map(function (k) { return caches.delete(k); }));
  }).then(function () { return self.clients.claim(); }));
});

function timeout(ms, p) {
  return new Promise(function (resolve, reject) {
    var t = setTimeout(function () { reject(new Error('timeout')); }, ms);
    p.then(function (r) { clearTimeout(t); resolve(r); }, function (err) { clearTimeout(t); reject(err); });
  });
}

self.addEventListener('fetch', function (e) {
  var req = e.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  var p = url.pathname;
  if (p.indexOf('/api/') === 0 || p.indexOf('/admin') === 0 || p.indexOf('/tg/') === 0) return;

  if (req.mode === 'navigate' && p.indexOf('/app') === 0) {
    // Сначала сеть (свежая версия), без сети — сохранённая оболочка.
    e.respondWith(timeout(4000, fetch(req)).then(function (r) {
      if (r && r.ok) {
        var copy = r.clone();
        caches.open(CACHE).then(function (c) { c.put('/app/', copy); });
      }
      return r;
    }).catch(function () {
      return caches.match('/app/', { ignoreSearch: true }).then(function (m) {
        return m || new Response('<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">' +
          '<body style="font:16px system-ui;padding:32px">Нет сети 📡 Открой дневник, когда появится интернет.</body>',
          { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
      });
    }));
    return;
  }

  if (p.indexOf('/app/') === 0 || p.indexOf('/brand/') === 0) {
    // Статика с версией в адресе не меняется — сначала кэш.
    e.respondWith(caches.match(req).then(function (m) {
      if (m) return m;
      return fetch(req).then(function (r) {
        if (r && r.ok && r.type === 'basic') {
          var copy = r.clone();
          caches.open(CACHE).then(function (c) { c.put(req, copy); });
        }
        return r;
      });
    }));
  }
});
