/* School Sensor · Mini App. Чистый JS (ES2017): без фреймворков, без ?. и ??, чтобы шло на старых телефонах. */
(function () {
  'use strict';
  var D = document, W = window;
  var TG = null, INIT = '', DEV = '', START = '', UID = '';
  var DEF_THEME = { palette: 'telegram', mode: 'auto', accent: '', radius: 16, font: 100, lite: false, nav: 'dock' };
  var PAL = [
    ['telegram', 'Телеграм', '#eff1f5', '#ffffff', '#2f80ed'], ['ink', 'Чернила', '#f6efe7', '#1d2550', '#f5a587'],
    ['mint', 'Мята', '#eaf6f0', '#ffffff', '#1faa6b'], ['lime', 'Лайм', '#f2f4ea', '#1a1f10', '#b5e61d'],
    ['brutal', 'Брутал', '#fff3d6', '#ffffff', '#ff6b35'], ['coffee', 'Кофе', '#f2ebe3', '#fffaf4', '#e07a3f'],
    ['night', 'Ночь', '#0a0b0e', '#14161b', '#ffb224'], ['pastel', 'Пастель', '#f6f2fb', '#ffffff', '#8b7bf2'],
    ['glass', 'Стекло', '#c9c2f5', 'rgba(255,255,255,.6)', '#6a5cff']
  ];
  var ACCENTS = ['#2f80ed', '#1faa6b', '#e07a3f', '#e5484d', '#8b7bf2', '#ffb224', '#1d2550', '#ff6b35', '#14b8a6'];
  var TABS = ['diary', 'marks', 'home', 'schedule', 'more'];
  var S = {
    me: null, theme: Object.assign({}, DEF_THEME), screen: 'home', params: {}, stack: [], sheet: null,
    data: {}, at: {}, fresh: {}, loading: {}, err: {}, offline: false,
    week: { diary: '', schedule: '' }, day: {}, open: {}, det: {}, detLoading: {}, calc: {}, calcSubj: null,
    filter: 'all', syncing: false
  };
  var $app, $dock, $sheet, $pane, $toast;

  // ───────────────────────── утилиты ─────────────────────────
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function ic(name, cls) { return '<svg class="i' + (cls ? ' ' + cls : '') + '"><use href="#i-' + name + '"/></svg>'; }
  function plural(n, a, b, c) {
    var m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return a;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
    return c;
  }
  function days(n) { return n + ' ' + plural(n, 'день', 'дня', 'дней'); }
  var MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var WD = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];
  function pdate(iso) { var p = String(iso).slice(0, 10).split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function dshort(iso) { if (!iso) return ''; var d = pdate(iso); return d.getDate() + ' ' + MONTHS[d.getMonth()]; }
  function dwd(iso) { var d = pdate(iso); return WD[d.getDay()] + ', ' + d.getDate() + ' ' + MONTHS[d.getMonth()]; }
  function ago(iso) {
    if (!iso) return 'ещё не обновлялось';
    var t = new Date(iso).getTime(), s = (Date.now() - t) / 1000;
    if (isNaN(t)) return '';
    if (s < 90) return 'только что';
    if (s < 3600) return Math.round(s / 60) + ' мин назад';
    if (s < 86400) return Math.round(s / 3600) + ' ч назад';
    var d = new Date(t);
    return d.getDate() + ' ' + MONTHS[d.getMonth()] + ' в ' + ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
  }
  function cap(s) { s = String(s || ''); return s.charAt(0).toUpperCase() + s.slice(1); }
  function mval(m) { var v = parseInt(m, 10); return v >= 1 && v <= 5 ? v : null; }
  function mk(m, cls) { var v = mval(m); return '<span class="mk ' + (v ? 'm' + Math.max(v, 2) : '') + (cls ? ' ' + cls : '') + '">' + esc(m) + '</span>'; }
  function avgColor(a) {
    if (a == null) return 'var(--mx)';
    return a >= 4.5 ? 'var(--m5)' : a >= 3.5 ? 'var(--m4)' : a >= 2.5 ? 'var(--m3)' : 'var(--m2)';
  }
  function hexRgb(hex) {
    var m = /^#?([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex || '').trim());
    return m ? [parseInt(m[1], 16), parseInt(m[2], 16), parseInt(m[3], 16)] : null;
  }
  function lum(hex) { var c = hexRgb(hex); return c ? (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) / 255 : 0.5; }
  function rgba(hex, a) { var c = hexRgb(hex); return c ? 'rgba(' + c.join(',') + ',' + a + ')' : hex; }
  function lsGet(k) { try { var v = W.localStorage.getItem(k); return v ? JSON.parse(v) : null; } catch (e) { return null; } }
  function lsSet(k, v) {
    try { W.localStorage.setItem(k, JSON.stringify(v)); } catch (e) {
      try { Object.keys(W.localStorage).forEach(function (x) { if (x.indexOf('ss:c:') === 0) W.localStorage.removeItem(x); }); } catch (e2) { /* ничего */ }
    }
  }
  function haptic(kind) {
    try {
      if (!TG || !TG.HapticFeedback) return;
      if (kind === 'ok') TG.HapticFeedback.notificationOccurred('success');
      else if (kind === 'sel') TG.HapticFeedback.selectionChanged();
      else TG.HapticFeedback.impactOccurred('light');
    } catch (e) { /* старый клиент */ }
  }
  var toastTimer = 0;
  function toast(msg) {
    $toast.textContent = msg;
    $toast.className = 'show';
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { $toast.className = ''; }, 2600);
  }
  function confirmBox(text, cb) {
    if (TG && TG.showConfirm && TG.isVersionAtLeast && TG.isVersionAtLeast('6.2')) {
      TG.showConfirm(text, function (ok) { if (ok) cb(); });
    } else if (W.confirm(text)) cb();
  }

  // ───────────────────────── Telegram ─────────────────────────
  function hashParams() {
    var h = String(location.hash || '').replace(/^#/, ''), q = h.indexOf('?'), out = {};
    if (q >= 0) h = h.slice(q + 1);
    h.split('&').forEach(function (kv) {
      var i = kv.indexOf('=');
      if (i > 0) { try { out[decodeURIComponent(kv.slice(0, i))] = decodeURIComponent(kv.slice(i + 1)); } catch (e) { /* мусор */ } }
    });
    return out;
  }
  function query(name) { var m = new RegExp('[?&]' + name + '=([^&#]*)').exec(location.search); return m ? decodeURIComponent(m[1]) : ''; }
  function themeParams() {
    if (TG && TG.themeParams && Object.keys(TG.themeParams).length) return TG.themeParams;
    try { return JSON.parse(hashParams().tgWebAppThemeParams || '{}'); } catch (e) { return {}; }
  }
  function tgScheme() {
    if (TG && TG.colorScheme) return TG.colorScheme;
    var tp = themeParams();
    if (tp.bg_color) return lum(tp.bg_color) < 0.5 ? 'dark' : 'light';
    return (W.matchMedia && W.matchMedia('(prefers-color-scheme: dark)').matches) ? 'dark' : 'light';
  }
  function initTelegram() {
    TG = (W.Telegram && W.Telegram.WebApp) || null;
    var hp = hashParams(), saved = '';
    try { saved = W.sessionStorage.getItem('ss:init') || ''; } catch (e) { /* приватный режим */ }
    INIT = (TG && TG.initData) || hp.tgWebAppData || saved || '';
    try { if (INIT) W.sessionStorage.setItem('ss:init', INIT); } catch (e) { /* ничего */ }
    DEV = query('dev_user');
    START = query('s') || (TG && TG.initDataUnsafe && TG.initDataUnsafe.start_param) || '';
    try {
      var u = JSON.parse(new URLSearchParams(INIT).get('user') || '{}');
      UID = u && u.id ? String(u.id) : '';
    } catch (e) { UID = ''; }
    if (!UID && DEV) UID = 'dev' + DEV;
    if (!TG) return;
    try {
      TG.ready();
      TG.expand();
      if (TG.disableVerticalSwipes) TG.disableVerticalSwipes();
      if (TG.onEvent) TG.onEvent('themeChanged', applyTheme);
      if (TG.BackButton && TG.isVersionAtLeast && TG.isVersionAtLeast('6.1')) TG.BackButton.onClick(back);
    } catch (e) { /* старый клиент */ }
  }
  function loadSdk(cb) {
    if (W.Telegram && W.Telegram.WebApp) { cb(); return; }
    var done = false, s = D.createElement('script');
    function fin() { if (!done) { done = true; cb(); } }
    s.src = 'https://telegram.org/js/telegram-web-app.js?59';
    s.onload = fin; s.onerror = fin;
    setTimeout(fin, 2500);           // telegram.org недоступен — работаем на параметрах из адреса
    D.head.appendChild(s);
  }

  // ───────────────────────── тема ─────────────────────────
  function applyTheme() {
    var th = S.theme, root = D.documentElement, st = root.style;
    var mode = th.mode === 'auto' ? (th.palette === 'night' ? 'dark' : tgScheme()) : th.mode;
    var cls = ['pal-' + th.palette];
    if (mode === 'dark') cls.push('dark');
    if (th.lite) cls.push('lite');
    if (th.nav === 'pill') cls.push('nav-pill');
    root.className = cls.join(' ');
    ['--bg', '--surface', '--surface2', '--text', '--muted', '--accent', '--accent-ink', '--accent-soft', '--line'].forEach(function (p) { st.removeProperty(p); });
    if (th.palette === 'telegram') {
      var tp = themeParams();
      if (tp.bg_color && tgScheme() === mode) {
        var accent = tp.button_color || '#2f80ed';
        st.setProperty('--bg', tp.secondary_bg_color || tp.bg_color);
        st.setProperty('--surface', tp.section_bg_color || tp.bg_color);
        st.setProperty('--surface2', tp.secondary_bg_color || tp.bg_color);
        if (tp.text_color) { st.setProperty('--text', tp.text_color); st.setProperty('--line', rgba(tp.text_color, 0.08)); }
        if (tp.hint_color) st.setProperty('--muted', tp.hint_color);
        st.setProperty('--accent', accent);
        st.setProperty('--accent-ink', tp.button_text_color || '#fff');
        st.setProperty('--accent-soft', rgba(accent, 0.14));
      }
    }
    if (th.accent && hexRgb(th.accent)) {
      st.setProperty('--accent', th.accent);
      st.setProperty('--accent-ink', lum(th.accent) > 0.6 ? '#111' : '#fff');
      st.setProperty('--accent-soft', rgba(th.accent, 0.14));
    }
    st.setProperty('--r', (+th.radius || 0) + 'px');
    st.setProperty('--fs', String((+th.font || 100) / 100));
    if (TG && TG.setHeaderColor && TG.isVersionAtLeast && TG.isVersionAtLeast('6.9')) {
      try {
        var bg = W.getComputedStyle(root).getPropertyValue('--bg').trim();
        if (/^#[0-9a-f]{6}$/i.test(bg)) {
          TG.setHeaderColor(bg); TG.setBackgroundColor(bg);
          if (TG.setBottomBarColor) TG.setBottomBarColor(bg);
        }
      } catch (e) { /* не страшно */ }
    }
  }

  // ───────────────────────── сеть ─────────────────────────
  function api(path, body) {
    var url = '/api/' + path;
    if (DEV) url += (url.indexOf('?') < 0 ? '?' : '&') + 'dev_user=' + encodeURIComponent(DEV);
    var opt = { method: body ? 'POST' : 'GET', headers: { 'X-Telegram-Init-Data': INIT }, credentials: 'omit', cache: 'no-store' };
    if (body) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok && r.status !== 202) {
          var e = new Error(typeof j.detail === 'string' ? j.detail : 'Ошибка ' + r.status);
          e.status = r.status;
          throw e;
        }
        j._status = r.status;
        return j;
      });
    });
  }
  function ckey(path) { return 'ss:c:' + UID + ':' + path; }
  function need(path, maxAge) {
    if (S.data[path] === undefined && UID) {
      var c = lsGet(ckey(path));
      if (c) { S.data[path] = c.d; S.at[path] = c.t; }
    }
    var stale = !S.fresh[path] || Date.now() - (S.at[path] || 0) > (maxAge || 60000);
    if (stale && !S.loading[path]) {
      S.loading[path] = true;
      api(path).then(function (j) {
        delete j._status;
        var before = JSON.stringify(S.data[path]), now = JSON.stringify(j);
        S.data[path] = j; S.at[path] = Date.now(); S.fresh[path] = true; S.err[path] = null;
        if (UID) lsSet(ckey(path), { t: Date.now(), d: j });
        setOffline(false);
        if (now !== before) render();
      }).catch(function (e) {
        S.err[path] = e;
        S.fresh[path] = true; S.at[path] = Date.now() - (maxAge || 60000) + 15000;   // повтор через ~15 c
        if (!e.status) setOffline(true);
        if (S.data[path] === undefined || e.status) render();
      }).then(function () { S.loading[path] = false; });
    }
    return S.data[path];
  }
  function invalidate(prefix) {
    Object.keys(S.fresh).forEach(function (k) { if (!prefix || k.indexOf(prefix) === 0) S.fresh[k] = false; });
  }
  function setOffline(v) {
    if (S.offline === v) return;
    S.offline = v;
    render();
  }

  // ───────────────────────── навигация ─────────────────────────
  function go(name, params) {
    if (TABS.indexOf(name) >= 0) S.stack = [];
    else S.stack.push({ screen: S.screen, params: S.params, y: W.pageYOffset });
    S.screen = name; S.params = params || {};
    render();
    W.scrollTo(0, 0);
    haptic('sel');
  }
  function back() {
    if (S.sheet) { closeSheet(); return; }
    var prev = S.stack.pop();
    if (!prev) { if (S.screen !== 'home') go('home'); return; }
    S.screen = prev.screen; S.params = prev.params;
    render();
    W.scrollTo(0, prev.y || 0);
  }
  function updateBack() {
    if (!TG || !TG.BackButton || !TG.isVersionAtLeast || !TG.isVersionAtLeast('6.1')) return;
    try { if (S.stack.length || S.sheet) TG.BackButton.show(); else TG.BackButton.hide(); } catch (e) { /* ничего */ }
  }
  function buildDock() {
    var items = [['diary', 'book', 'Дневник'], ['marks', 'star', 'Оценки'], ['home', 'home', 'Главная'], ['schedule', 'clock', 'Уроки'], ['more', 'grid', 'Ещё']];
    $dock.innerHTML = items.map(function (x) {
      if (x[0] === 'home') return '<button class="fab" data-a="tab" data-v="home" aria-label="Главная"><span>' + ic('home') + '</span><small>' + x[2] + '</small></button>';
      return '<button data-a="tab" data-v="' + x[0] + '">' + ic(x[1]) + '<span>' + x[2] + '</span></button>';
    }).join('');
  }
  function tabOf(screen) {
    if (TABS.indexOf(screen) >= 0) return screen;
    if (screen === 'calc') return 'marks';
    return 'more';
  }
  function markDock() {
    var t = tabOf(S.screen);
    Array.prototype.forEach.call($dock.children, function (b) { b.classList.toggle('on', b.getAttribute('data-v') === t); });
  }

  // ───────────────────────── общие блоки ─────────────────────────
  function top(title, right) {
    return '<div class="top"><h1>' + esc(title) + '</h1>' + (right || '') + '</div>';
  }
  function syncBtn() {
    return '<button class="icon-btn' + (S.syncing ? ' spin' : '') + '" data-a="sync" aria-label="Обновить">' + ic('refresh') + '</button>';
  }
  function skel(n) { var h = ''; for (var i = 0; i < n; i++) h += '<div class="skel" style="height:' + (i ? 92 : 150) + 'px"></div>'; return h; }
  function errorCard(e) {
    return '<div class="card empty"><span class="big">😕</span>' + esc(e && e.message ? e.message : 'Не получилось загрузить') +
      '<div style="margin-top:12px"><button class="btn soft" data-a="reload">' + ic('refresh', 's') + ' Повторить</button></div></div>';
  }
  function banners() {
    var h = '', me = S.me, u = me && me.user;
    if (S.offline) h += '<div class="banner warn">📴 Нет связи — показываю сохранённое</div>';
    if (me && me.service && me.service.maintenance) h += '<div class="banner warn">🛠 Идут техработы — данные могут не обновляться</div>';
    else if (me && me.service && me.service.paused) h += '<div class="banner">⏸ Сайт школы на паузе — бережём его, скоро продолжу сам</div>';
    if (u && u.status === 'bad_password') h += '<div class="banner red">🔑 Пароль от NetSchool не подходит — обнови его в чате: /login</div>';
    if (u && u.paused_until) h += '<div class="banner">⏸ Пауза до ' + esc(u.paused_until.slice(11, 16)) + ' — не захожу в NetSchool</div>';
    return h;
  }
  function ring(avg, size, sw, label) {
    var r = (size - sw) / 2, c = 2 * Math.PI * r, p = avg == null ? 0 : Math.max(0.02, Math.min(1, avg / 5)), m = size / 2;
    return '<div class="ring' + (size < 60 ? ' mini' : '') + '" style="width:' + size + 'px;height:' + size + 'px">' +
      '<svg width="' + size + '" height="' + size + '"><circle class="bgc" cx="' + m + '" cy="' + m + '" r="' + r + '" stroke-width="' + sw + '"/>' +
      '<circle cx="' + m + '" cy="' + m + '" r="' + r + '" stroke="' + avgColor(avg) + '" stroke-width="' + sw + '" stroke-linecap="round" stroke-dasharray="' +
      (c * p).toFixed(1) + ' ' + c.toFixed(1) + '"/></svg><div class="v"><div><b class="num">' + (avg == null ? '—' : avg.toFixed(2)) + '</b>' +
      (label ? '<small>' + esc(label) + '</small>' : '') + '</div></div></div>';
  }
  function face(mood, size) {
    var col = { great: 'var(--m5)', good: 'var(--m4)', ok: 'var(--m3)', bad: 'var(--m2)' }[mood] || 'var(--mx)';
    var mouth = { great: 'M20 37c4 9 20 9 24 0', good: 'M22 39c4 5 16 5 20 0', ok: 'M23 41h18', bad: 'M22 45c4-6 16-6 20 0' }[mood] || 'M25 41h14';
    var eyes = mood === 'great' ? '<path d="M18 28c2-4 6-4 8 0M38 28c2-4 6-4 8 0"/>' :
      '<circle cx="22" cy="27" r="2.8" fill="currentColor" stroke="none"/><circle cx="42" cy="27" r="2.8" fill="currentColor" stroke="none"/>';
    return '<svg class="face" width="' + size + '" height="' + size + '" viewBox="0 0 64 64" style="color:var(--text)">' +
      '<circle cx="32" cy="32" r="28" fill="' + col + '" opacity=".2"/><circle cx="32" cy="32" r="28" fill="none" stroke="' + col + '" stroke-width="3"/>' +
      '<g stroke="currentColor" stroke-width="3.4" fill="none" stroke-linecap="round">' + eyes + '<path d="' + mouth + '"/></g></svg>';
  }
  function lessonsList(ls) {
    if (ls === null || ls === undefined) return '<div class="muted small">Расписание ещё не подтянулось ⏳</div>';
    if (!ls.length) return '<div class="muted">Уроков нет 🎉</div>';
    return '<ul class="lessons">' + ls.map(function (l) {
      return '<li class="' + esc(l.state) + '"><span class="n">' + esc(l.n || '•') + '</span><span class="t num">' + esc(l.start || '') +
        '</span><span class="s ell">' + esc(l.s) + '</span>' + (l.room ? '<span class="room">' + esc(l.room) + '</span>' : '') + '</li>';
    }).join('') + '</ul>';
  }
  function typeBadge(x) {
    if (x.hw) return '';
    return '<span class="badge acc">' + esc(x.type_name || x.type) + '</span>';
  }
  function hwRow(x, opts) {
    opts = opts || {};
    var open = S.open[x.key], det = S.det[x.key];
    var h = '<div class="hw' + (x.done ? ' done' : '') + '">';
    h += '<button class="chk" data-a="done" data-v="' + esc(x.key) + '" aria-label="Сделано">' + ic('check') + '</button>';
    h += '<div class="grow"><div class="row between"><span class="sub ell">' + esc(x.subject) + '</span>' + (x.mark ? mk(x.mark) : '') + '</div>';
    h += '<div class="ttl">' + esc(x.title || 'без темы') + '</div><div class="meta">' + typeBadge(x);
    if (opts.date) h += '<span class="badge">' + esc(dwd(x.date)) + '</span>';
    if (x.overdue) h += '<span class="badge red">долг</span>';
    if (x.files) h += '<span class="badge">' + ic('clip', 's') + ' ' + x.files + '</span>';
    if (x.can_details) h += '<button class="badge" data-a="det" data-v="' + esc(x.key) + '">' + (open ? 'Скрыть' : 'Подробнее') + '</button>';
    h += '</div>';
    if (open) {
      h += '<div class="det">';
      if (S.detLoading[x.key]) h += '<span class="muted">Загружаю с сайта школы…</span>';
      else if (!det) h += '<span class="muted">Подробностей нет</span>';
      else {
        if (det.teacher) h += '<p class="small muted">' + esc(det.teacher) + '</p>';
        h += det.notes ? '<p>' + esc(det.notes) + '</p>' : '<p class="muted">Учитель ничего не добавил</p>';
        (det.files || []).forEach(function (f) {
          h += '<button class="file" data-a="file" data-v="' + esc(x.key) + '|' + esc(f.id) + '">' + ic('clip', 's') + '<span class="grow ell">' + esc(f.name) + '</span><span class="tiny muted">в чат</span></button>';
        });
      }
      h += '</div>';
    }
    return h + '</div></div>';
  }

  // ───────────────────────── «не подключён» ─────────────────────────
  function gate() {
    var me = S.me, u = me && me.user, bot = me && me.bot;
    var btn = bot ? '<div style="margin-top:16px"><button class="btn" data-a="open-bot">' + ic('chat', 's') + ' Открыть чат с ботом</button></div>' : '';
    if (!u) {
      return '<div class="card empty" style="margin-top:30px"><img src="/brand/mark.svg" alt="" style="width:64px;height:64px;border-radius:20px"><h2 style="justify-content:center;margin-top:12px">Привет! 👋</h2>' +
        '<p>Сначала открой чат с ботом и нажми <b>«Старт»</b> — там подключим дневник за минуту.</p>' + btn + '</div>';
    }
    return '<div class="card empty" style="margin-top:30px"><span class="big">🔑</span><h2 style="justify-content:center">Подключи NetSchool</h2>' +
      '<p>Логин и пароль вводятся только в чате с ботом (сообщение с паролем сразу удаляется). Напиши там <b>/login</b>.</p>' + btn + '</div>';
  }
  function needConnected() { var u = S.me && S.me.user; return !!(u && u.connected); }

  // ───────────────────────── Главная ─────────────────────────
  function scrHome() {
    if (!needConnected()) return gate();
    var d = need('dashboard');
    var h = top('School Sensor', syncBtn()) + banners();
    if (!d) return h + (S.err.dashboard ? errorCard(S.err.dashboard) : skel(4));
    var w = d.weather, sc = d.score;
    h += '<div class="card hero"><i class="blob b1"></i><i class="blob b2"></i><h1>' + esc(d.greeting) + '</h1><div class="date">' +
      esc(d.date.title) + (d.date.off ? ' · ' + esc(d.date.off) : '') + '</div>';
    if (w) {
      var t = (w.tmin != null ? (w.tmin > 0 ? '+' : '') + w.tmin + '…' : '') + (w.tmax != null ? (w.tmax > 0 ? '+' : '') + w.tmax + '°' : '');
      h += '<div class="wchip"><span class="e">' + esc(w.emoji || '🌡') + '</span><span>' + esc(t) + ' · ' + esc(w.desc || '') +
        (w.precip_prob >= 40 ? ' · ☔ ' + w.precip_prob + '%' : '') + '</span></div>';
      if (w.advice && w.advice.length) h += '<p class="advice">' + esc(w.advice[0]) + '</p>';
    }
    h += '</div>';
    if (d.birthday && d.birthday.self) h += '<div class="card"><h2>🎉 ' + esc(d.birthday.self) + '</h2></div>';
    // оценки + настроение
    h += '<div class="card tap" data-a="tab" data-v="marks"><div class="score">' + ring(sc.avg, 104, 11, 'средний') +
      '<div class="grow"><div class="mood">' + face(sc.mood, 46) + '<p>' + esc(sc.mood_text || (sc.avg == null ? 'Оценок пока нет — чистый лист ✍️' : '')) + '</p></div>' +
      '<div class="dist">' + ['5', '4', '3', '2'].map(function (k) { return '<span style="color:var(--m' + k + ')">' + (sc.counts[k] || 0) + '<i>«' + k + '»</i></span>'; }).join('') +
      '</div></div></div></div>';
    // уроки
    var f = d.focus, ttl = f.is_today ? 'Сегодня' : cap(f.label);
    h += '<div class="card"><h2>' + ic('clock', 's') + ' ' + esc(ttl) + (f.lessons && f.lessons.length ? ' · ' + f.lessons.length + ' ' + plural(f.lessons.length, 'урок', 'урока', 'уроков') : '') +
      '<button class="more" data-a="tab" data-v="schedule">неделя</button></h2>' + (f.off ? '<div class="muted">Выходной — ' + esc(f.off) + ' 😎</div>' : lessonsList(f.lessons)) + '</div>';
    // ДЗ
    var hw = d.hw, pct = hw.total ? Math.round(hw.done / hw.total * 100) : 0;
    h += '<div class="card"><h2>' + ic('book', 's') + ' ДЗ на ' + esc(dwd(hw.date)) + '<button class="more" data-a="diary-day" data-v="' + esc(hw.date) + '">все</button></h2>';
    if (!hw.items.length) h += '<div class="muted">Ничего не задано — кайф 😌</div>';
    else {
      if (hw.total) h += '<div class="row between small"><span class="muted b">Сделано ' + hw.done + ' из ' + hw.total + '</span><span class="b">' + pct + '%</span></div><div class="progress"><i style="width:' + pct + '%"></i></div>';
      h += hw.items.map(function (x) { return hwRow(x); }).join('');
    }
    h += '</div>';
    if (d.debts_total) {
      h += '<div class="card"><h2>' + ic('warn', 's') + ' Долги · ' + d.debts_total + '</h2>' + d.debts.slice(0, 5).map(function (x) { return hwRow(x, { date: true }); }).join('') + '</div>';
    }
    if (d.recent && d.recent.length) {
      h += '<div class="card"><h2>' + ic('star', 's') + ' Свежие оценки<button class="more" data-a="tab" data-v="marks">все</button></h2><div class="recent">' +
        d.recent.map(function (x) { return '<div class="rm">' + mk(x.m) + '<span>' + esc(x.s) + '</span><span>' + esc(dshort(x.d)) + '</span></div>'; }).join('') + '</div></div>';
    }
    var row = '';
    if (d.vacation) {
      var v = d.vacation;
      row += '<div class="card tap" data-a="go" data-v="calendar"><div class="vac"><div class="n num">' + (v.now ? '🏖' : v.days) + '</div><div class="grow"><b>' +
        (v.now ? cap(v.title) + '!' : plural(v.days, 'день', 'дня', 'дней') + ' до каникул') + '</b><div class="small muted">' +
        (v.now ? 'до ' + esc(dshort(v.end)) : esc(cap(v.title))) + '</div></div></div></div>';
    }
    if (d.news && d.news.latest) {
      row += '<div class="card tap" data-a="go" data-v="news"><h2 style="margin-bottom:4px">' + ic('mega', 's') + ' Объявления</h2><div class="small ell b">' + esc(d.news.latest.title) +
        '</div><div class="tiny muted">' + esc(dshort(d.news.latest.date)) + '</div></div>';
    }
    if (row) h += '<div class="grid2">' + row + '</div>';
    if (d.birthday && d.birthday.today && d.birthday.today.length) {
      h += '<div class="card tap" data-a="go" data-v="birthdays">🎂 Сегодня день рождения: <b>' + esc(d.birthday.today.join(', ')) + '</b></div>';
    }
    h += '<p class="small muted" style="text-align:center;margin-top:18px">Обновлено ' + esc(ago(d.sync.last_ok)) +
      ' · <a href="#" data-a="sync">обновить</a></p>';
    return h;
  }

  // ───────────────────────── Дневник ─────────────────────────
  function weekBar(kind, d) {
    return '<div class="week"><button class="icon-btn" data-a="week" data-v="' + kind + '|' + esc(d.prev || '') + '"' + (d.prev ? '' : ' disabled style="opacity:.35"') + '>' + ic('left') +
      '</button><div class="t">' + esc(d.title) + (d.week === d.current ? '<div class="tiny muted">эта неделя</div>' : '') +
      '</div><button class="icon-btn" data-a="week" data-v="' + kind + '|' + esc(d.next || '') + '"' + (d.next ? '' : ' disabled style="opacity:.35"') + '>' + ic('right') + '</button></div>';
  }
  function scrDiary() {
    if (!needConnected()) return gate();
    var path = 'diary' + (S.week.diary ? '?week=' + S.week.diary : '');
    var d = need(path);
    var h = top('Дневник', syncBtn()) + banners();
    if (!d) return h + (S.err[path] ? errorCard(S.err[path]) : skel(3));
    h += weekBar('diary', d);
    if (!d.loaded) return h + '<div class="card empty"><span class="big">🗓</span>Эту неделю я ещё не загрузил</div>';
    var sel = S.day[d.week];
    if (!sel) {
      var today = (S.me && S.me.today) || '';
      var hit = d.days.filter(function (x) { return x.date === today; })[0] || d.days.filter(function (x) { return x.items.length; })[0] || d.days[0];
      sel = hit.date;
    }
    h += '<div class="days' + (d.days.length > 6 ? ' d7' : '') + '">' + d.days.map(function (x) {
      return '<button class="day' + (x.date === sel ? ' on' : '') + (x.today ? ' today' : '') + (x.off ? ' off' : '') + '" data-a="day" data-v="' + d.week + '|' + x.date + '"><small>' +
        esc(x.short) + '</small><b class="num">' + x.day + '</b>' + (x.items.length ? '<i></i>' : '') + '</button>';
    }).join('') + '</div>';
    if (d.carried && d.carried.length) {
      h += '<div class="card"><h2>' + ic('warn', 's') + ' Долги с прошлых недель</h2>' + d.carried.map(function (x) { return hwRow(x, { date: true }); }).join('') + '</div>';
    }
    var day = d.days.filter(function (x) { return x.date === sel; })[0];
    if (day) {
      var ls = day.lessons;
      h += '<div class="card"><h2>' + esc(day.title) + '</h2>';
      if (day.off) h += '<div class="muted" style="margin-bottom:8px">Выходной — ' + esc(day.off) + ' 😎</div>';
      else if (ls && ls.length) h += '<div class="small muted" style="margin:-6px 0 8px">' + ls.length + ' ' + plural(ls.length, 'урок', 'урока', 'уроков') + ' · ' + esc(ls[0].start || '') + '–' + esc(ls[ls.length - 1].end || '') + '</div>';
      h += day.items.length ? day.items.map(function (x) { return hwRow(x); }).join('') : '<div class="empty"><span class="big">🎈</span>Заданий нет</div>';
      h += '</div>';
    }
    return h;
  }

  // ───────────────────────── Оценки и калькулятор ─────────────────────────
  var EPS = 1e-9;
  function sum(a) { var s = 0; for (var i = 0; i < a.length; i++) s += a[i]; return s; }
  function round2(x) { var y = x * 100, f = Math.floor(y); if (Math.abs(y - f - 0.5) < 1e-7) return (f % 2 === 0 ? f : f + 1) / 100; return Math.round(y) / 100; }
  function needed(vals, target) {
    var s = sum(vals), c = vals.length;
    if (c && s / c + EPS >= target) return 0;
    if (5 <= target - EPS) return null;
    var n = Math.max(Math.ceil((target * c - s) / (5 - target) - EPS), c ? 0 : 1);
    return n <= 60 ? n : null;
  }
  function predict(vals) {
    var rule = (S.me && S.me.rule) || { sure: 0.62, maybe: 0.5 };
    if (!vals.length) return { avg: null, grade: null, status: 'none' };
    var avg = sum(vals) / vals.length, a = round2(avg), base = Math.floor(a + EPS), frac = a - base, grade, status, up = null;
    if (base >= 5) return { avg: avg, grade: 5, status: 'top' };
    if (frac + EPS >= rule.sure) { grade = base + 1; status = 'sure'; }
    else if (frac + EPS >= rule.maybe) { grade = base; status = 'maybe'; up = base + 1; }
    else { grade = Math.max(base, 1); status = 'floor'; }
    if (grade >= 5) return { avg: avg, grade: 5, status: status };
    var nxt = grade + 1;
    return { avg: avg, grade: grade, status: status, up: up, need5: needed(vals, nxt - 1 + rule.sure), need5_maybe: status !== 'maybe' ? needed(vals, nxt - 1 + rule.maybe) : 0 };
  }
  function predText(p) {
    if (p.status === 'none') return 'оценок пока нет';
    if (p.status === 'top') return '→ 5 💯';
    if (p.status === 'sure') return '→ ' + p.grade + ' точно';
    if (p.status === 'maybe') return '→ ' + p.grade + ', можно вытянуть на ' + p.up;
    return '→ ' + p.grade;
  }
  function needText(p) {
    if (p.status === 'none' || p.status === 'top' || p.grade == null || p.grade >= 5) return '';
    var nxt = p.grade + 1;
    if (p.need5 == null) return 'до «' + nxt + '» пятёрками уже не дотянуть';
    if (p.need5 === 0) return '';
    return 'до «' + nxt + '» точно: ещё ' + p.need5 + ' ' + plural(p.need5, 'пятёрка', 'пятёрки', 'пятёрок');
  }
  function scrMarks() {
    if (!needConnected()) return gate();
    var d = need('marks');
    var h = top('Оценки', '<button class="icon-btn" data-a="go" data-v="calc" aria-label="Калькулятор">' + ic('calc') + '</button>') + banners();
    if (!d) return h + (S.err.marks ? errorCard(S.err.marks) : skel(4));
    if (!d.ready) return h + '<div class="card empty"><span class="big">⏳</span>Оценки ещё загружаются — загляни через пару минут</div>';
    var dist = d.dist, total = (dist['5'] || 0) + (dist['4'] || 0) + (dist['3'] || 0) + (dist['2'] || 0);
    h += '<div class="card"><div class="score">' + ring(d.overall, 110, 12, d.period || 'средний') + '<div class="grow"><b class="xb">' + esc(d.period || 'Четверть') + '</b>' +
      '<div class="small muted">' + total + ' ' + plural(total, 'оценка', 'оценки', 'оценок') + ' · ' + d.subjects.filter(function (s) { return s.values.length; }).length + ' предм.</div>' +
      '<div class="dist">' + ['5', '4', '3', '2'].map(function (k) { return '<span style="color:var(--m' + k + ')">' + (dist[k] || 0) + '<i>«' + k + '»</i></span>'; }).join('') + '</div>' +
      '<div class="tiny muted" style="margin-top:8px">от x.' + Math.round(d.rule.sure * 100) + ' — точно вверх, от x.' + Math.round(d.rule.maybe * 100) + ' — можно вытянуть</div></div></div></div>';
    var F = [['all', 'Все'], ['maybe', 'Можно вытянуть'], ['risk', 'Есть риск'], ['empty', 'Без оценок']];
    h += '<div class="chips" style="margin-top:14px">' + F.map(function (f) { return '<button class="chip' + (S.filter === f[0] ? ' on' : '') + '" data-a="filter" data-v="' + f[0] + '">' + f[1] + '</button>'; }).join('') + '</div>';
    var list = d.subjects.map(function (s, i) { return [s, i]; }).filter(function (p) {
      var s = p[0], pr = s.pred;
      if (S.filter === 'maybe') return pr.status === 'maybe';
      if (S.filter === 'risk') return pr.grade != null && pr.grade <= 3;
      if (S.filter === 'empty') return !s.values.length;
      return true;
    });
    h += '<div class="card" style="margin-top:6px">' + (list.length ? list.map(function (p) {
      var s = p[0], pr = s.pred;
      return '<div class="subj tap" data-a="subj" data-v="' + p[1] + '">' + ring(pr.avg, 48, 5) + '<div class="grow"><div class="name ell">' + esc(s.name) + '</div>' +
        '<div class="pred ' + esc(pr.status) + '">' + esc(s.text) + (s.need && pr.status !== 'sure' ? ' · ' + esc(s.need) : '') + '</div>' +
        (s.marks.length ? '<div class="marks">' + s.marks.slice(-10).map(function (m) { return mk(m.m); }).join('') + '</div>' : '') + '</div>' + ic('right', 's') + '</div>';
    }).join('') : '<div class="empty">Тут пусто 👌</div>') + '</div>';
    h += totalsCard(d.totals);
    return h;
  }
  function totalsCard(t) {
    if (!t || !t.subjects || !t.subjects.length) return '';
    var periods = (t.periods || []).filter(function (p) { return t.subjects.some(function (s) { return s.periods && s.periods[p]; }); });
    var hasYear = t.subjects.some(function (s) { return s.year; });
    if (!periods.length && !hasYear) return '';
    var head = '<tr><th>Предмет</th>' + periods.map(function (p) { return '<th>' + esc(p.replace(' четверть', ' ч.')) + '</th>'; }).join('') + (hasYear ? '<th>Год</th>' : '') + '</tr>';
    var rows = t.subjects.map(function (s) {
      return '<tr><td class="ell" style="max-width:150px">' + esc(s.name) + '</td>' + periods.map(function (p) { var m = s.periods[p]; return '<td>' + (m ? mk(m) : '<span class="muted">—</span>') + '</td>'; }).join('') +
        (hasYear ? '<td>' + (s.year ? mk(s.year) : '<span class="muted">—</span>') + '</td>' : '') + '</tr>';
    }).join('');
    return '<div class="sec">🏁 Итоговые</div><div class="card"><table class="table">' + head + rows + '</table></div>';
  }
  function calcBlock(name, base) {
    var extra = S.calc[name] || [], vals = base.concat(extra), p = predict(vals), p0 = predict(base);
    var h = '<div class="field"><label>Что если получу…</label><div class="calc-btns">' + [5, 4, 3, 2].map(function (v) {
      return '<button style="background:var(--m' + v + ')" data-a="calc-add" data-v="' + v + '">+' + v + '</button>';
    }).join('') + '</div></div>';
    if (extra.length) {
      h += '<div class="marks" style="margin-top:12px">' + extra.map(function (v, i) { return '<button data-a="calc-rm" data-v="' + i + '">' + mk(String(v), 'ghost') + '</button>'; }).join('') +
        '<button class="badge" data-a="calc-reset">сбросить</button></div>';
    }
    h += '<div class="card" style="margin-top:12px;background:var(--surface2);box-shadow:none"><div class="row"><div class="big-avg num" style="color:' + avgColor(p.avg) + '">' +
      (p.avg == null ? '—' : round2(p.avg).toFixed(2)) + '</div><div class="grow"><b>' + esc(predText(p)) + '</b>' +
      (extra.length && p0.avg != null ? '<div class="small muted">было ' + round2(p0.avg).toFixed(2) + '</div>' : '') +
      (needText(p) ? '<div class="small">' + esc(needText(p)) + '</div>' : '') + '</div></div></div>';
    return h;
  }
  function subjectSheet(idx) {
    var d = S.data.marks, s = d && d.subjects[idx];
    if (!s) return '';
    S.calcSubj = s.name;
    var h = '<div class="grab"></div><div class="row between"><h2 style="margin:0;font-size:1.25rem" class="xb">' + esc(s.name) + '</h2><button class="icon-btn" data-a="sheet-close">' + ic('x') + '</button></div>';
    h += '<div class="small muted" style="margin:4px 0 12px">' + s.values.length + ' ' + plural(s.values.length, 'оценка', 'оценки', 'оценок') +
      (s.absent ? ' · пропусков ' + s.absent : '') + (s.late ? ' · опозданий ' + s.late : '') + (s.debts ? ' · долгов ' + s.debts : '') + '</div>';
    if (s.marks.length) {
      h += '<div class="recent" style="margin-bottom:6px">' + s.marks.slice().reverse().map(function (m) { return '<div class="rm">' + mk(m.m) + '<span>' + esc(dshort(m.d)) + '</span></div>'; }).join('') + '</div>';
    }
    h += calcBlock(s.name, s.values);
    return h;
  }
  function scrCalc() {
    if (!needConnected()) return gate();
    var d = need('marks');
    var h = top('Калькулятор');
    if (!d) return h + skel(2);
    if (!d.ready) return h + '<div class="card empty">Оценки ещё загружаются ⏳</div>';
    var sel = S.params.subj != null ? S.params.subj : (S.calcPick != null ? S.calcPick : -1);
    h += '<p class="muted small" style="margin:-6px 4px 12px">Выбери предмет и добавляй воображаемые оценки — посчитаю средний и что выйдет в четверти.</p>';
    h += '<div class="chips">' + '<button class="chip' + (sel === -1 ? ' on' : '') + '" data-a="calc-pick" data-v="-1">✍️ Свои оценки</button>' +
      d.subjects.map(function (s, i) { return '<button class="chip' + (sel === i ? ' on' : '') + '" data-a="calc-pick" data-v="' + i + '">' + esc(s.name) + '</button>'; }).join('') + '</div>';
    if (sel === -1) {
      S.calcSubj = '__own';
      h += '<div class="card" style="margin-top:8px"><h2>Свои оценки</h2><div class="small muted">Нажимай цифры — например, чтобы прикинуть оценки по любому предмету.</div>' + calcBlock('__own', []) + '</div>';
    } else {
      var s = d.subjects[sel];
      S.calcSubj = s.name;
      h += '<div class="card" style="margin-top:8px"><h2>' + esc(s.name) + '</h2>' + (s.marks.length ? '<div class="marks">' + s.marks.map(function (m) { return mk(m.m); }).join('') + '</div>' : '<div class="muted small">Оценок пока нет</div>') +
        calcBlock(s.name, s.values) + '</div>';
    }
    return h;
  }

  // ───────────────────────── Расписание ─────────────────────────
  function scrSchedule() {
    if (!needConnected()) return gate();
    var path = 'schedule' + (S.week.schedule ? '?week=' + S.week.schedule : '');
    var d = need(path);
    var h = top('Уроки', syncBtn()) + banners();
    if (!d) return h + (S.err[path] ? errorCard(S.err[path]) : skel(3));
    var i = d.weeks.indexOf(d.week);
    var prev = i > 0 ? d.weeks[i - 1] : (i < 0 && d.weeks.length && d.weeks[0] < d.week ? d.weeks[d.weeks.length - 1] : '');
    var next = i >= 0 && i < d.weeks.length - 1 ? d.weeks[i + 1] : '';
    h += weekBar('schedule', { prev: prev, next: next, title: d.title, week: d.week, current: d.current });
    if (!d.loaded) return h + '<div class="card empty"><span class="big">🗓</span>Расписание этой недели ещё не загружено</div>';
    d.days.forEach(function (x) {
      h += '<div class="card"' + (x.today ? ' style="outline:2px solid var(--accent);outline-offset:-2px"' : '') + '><h2>' + esc(x.title) + (x.today ? ' <span class="badge acc">сегодня</span>' : '') +
        '</h2>' + (x.off ? '<div class="muted">Выходной — ' + esc(x.off) + '</div>' : lessonsList(x.lessons)) + '</div>';
    });
    return h;
  }

  // ───────────────────────── Ещё ─────────────────────────
  function scrMore() {
    var me = S.me, u = me && me.user;
    var h = top('Ещё');
    if (u) {
      var init = (u.name || '?').charAt(0).toUpperCase();
      h += '<div class="card profile"><div class="ava">' + esc(init) + '</div><div class="grow"><b class="xb" style="font-size:1.1rem">' + esc(u.name) + '</b>' +
        '<div class="small muted">' + (u.class ? 'Класс ' + esc(u.class) + ' · ' : '') + (u.connected ? 'NetSchool подключён' : 'NetSchool не подключён') + '</div>' +
        (u.connected ? '<div class="tiny muted">Обновлено ' + esc(ago(u.last_ok)) + '</div>' : '') + '</div></div>';
    }
    var T = [['look', 'palette', 'Оформление', 'темы и вид'], ['notify', 'bell', 'Уведомления', 'сводки и тишина'],
      ['calendar', 'beach', 'Каникулы', 'и праздники'], ['birthdays', 'gift', 'Именинники', 'этого месяца'],
      ['news', 'mega', 'Объявления', 'школы'], ['archive', 'box', 'Архив', 'картинки в чат'],
      ['calc', 'calc', 'Калькулятор', 'что выйдет в четверти'], ['feedback', 'msg', 'Написать', 'администратору']];
    h += '<div class="tiles" style="margin-top:12px">' + T.map(function (t) {
      return '<button class="tile" data-a="go" data-v="' + t[0] + '"><div class="ic">' + ic(t[1]) + '</div><b>' + t[2] + '</b><small>' + t[3] + '</small></button>';
    }).join('') + '</div>';
    h += '<div class="list" style="margin-top:12px">' +
      '<button class="it" data-a="go" data-v="privacy">' + ic('shield') + '<div class="grow"><b>Как хранятся данные</b><small>пароль, шифрование, удаление</small></div>' + ic('right', 's') + '</button>' +
      (me && me.bot ? '<button class="it" data-a="open-bot">' + ic('chat') + '<div class="grow"><b>Открыть чат с ботом</b></div>' + ic('right', 's') + '</button>' : '') +
      (u && u.connected ? '<button class="it danger" data-a="logout">' + ic('logout') + '<div class="grow"><b>Отключить NetSchool</b><small>сотру вход и дневник, настройки останутся</small></div></button>' : '') +
      (u ? '<button class="it danger" data-a="delete">' + ic('trash') + '<div class="grow"><b>Удалить все мои данные</b></div></button>' : '') + '</div>';
    h += '<p class="tiny muted" style="text-align:center;margin-top:16px">School Sensor ' + esc(me ? me.version : '') + ' · неофициальный помощник для дневника</p>';
    return h;
  }
  function scrLook() {
    var th = S.theme;
    var h = top('Оформление');
    h += '<div class="card"><h2>Палитра</h2><div class="pals">' + PAL.map(function (p) {
      return '<button class="pal' + (th.palette === p[0] ? ' on' : '') + '" data-a="th-pal" data-v="' + p[0] + '"><div class="pv" style="background:' + p[2] + '"><b style="background:' + p[4] + '"></b><i style="background:' + p[3] + ';' +
        (p[0] === 'brutal' ? 'border:2px solid #111;box-shadow:2px 2px 0 #111' : '') + '"></i></div>' + p[1] + '</button>';
    }).join('') + '</div></div>';
    var M = [['auto', 'Авто'], ['light', 'Светлая'], ['dark', 'Тёмная']];
    h += '<div class="card"><h2>Режим</h2><div class="seg">' + M.map(function (m) { return '<button class="' + (th.mode === m[0] ? 'on' : '') + '" data-a="th-mode" data-v="' + m[0] + '">' + m[1] + '</button>'; }).join('') + '</div>';
    h += '<div class="field"><label>Акцент</label><div class="swatches"><button class="' + (!th.accent ? 'on' : '') + '" data-a="th-accent" data-v="" title="Из палитры" style="background:conic-gradient(#2f80ed,#1faa6b,#ffb224,#e5484d,#8b7bf2,#2f80ed)"></button>' +
      ACCENTS.map(function (c) { return '<button class="' + (th.accent === c ? 'on' : '') + '" data-a="th-accent" data-v="' + c + '" style="background:' + c + '"></button>'; }).join('') +
      '<input type="color" data-a="th-color" value="' + esc(th.accent || '#2f80ed') + '" aria-label="Свой цвет"></div></div>';
    h += '<div class="field"><label>Скругление · <span class="num">' + th.radius + '</span></label><input type="range" min="0" max="28" step="2" value="' + th.radius + '" data-a="th-radius"></div>';
    h += '<div class="field"><label>Размер текста · <span class="num">' + th.font + '%</span></label><input type="range" min="85" max="130" step="5" value="' + th.font + '" data-a="th-font"></div>';
    h += '<div class="field"><label>Нижнее меню</label><div class="seg"><button class="' + (th.nav !== 'pill' ? 'on' : '') + '" data-a="th-nav" data-v="dock">Док с кнопкой</button><button class="' + (th.nav === 'pill' ? 'on' : '') + '" data-a="th-nav" data-v="pill">Плавающее</button></div></div></div>';
    h += '<div class="card"><div class="setrow"><div class="grow"><b>Лёгкий режим</b><small>без размытия, теней и анимаций — для слабых телефонов</small></div>' +
      '<label class="sw"><input type="checkbox" data-a="th-lite"' + (th.lite ? ' checked' : '') + '><span></span></label></div></div>';
    h += '<button class="btn soft" style="margin-top:12px" data-a="th-reset">Вернуть как было</button>';
    return h;
  }
  var NOTIF = [['morning', 'Утренняя сводка', 'погода, уроки и что сдавать'], ['evening', 'Вечерняя сводка', 'ДЗ на завтра'],
    ['n_marks', 'Новые оценки', 'сразу, как появятся'], ['reactions', 'Реакции на оценки', 'пара слов к оценке'],
    ['hw_instant', 'Каждое новое ДЗ сразу', 'иначе — в вечерней сводке'], ['n_attendance', 'Пропуски и опоздания', ''],
    ['n_debts', 'Долги', 'просроченные задания'], ['n_news', 'Объявления школы', ''], ['n_totals', 'Итоговые оценки', ''],
    ['weather', 'Погода утром', ''], ['cheatsheet', 'Закреплённая «Шпаргалка»', 'обновляется сама'],
    ['weekly_archive', 'Архив по воскресеньям', 'картинки — открываются без интернета'], ['quiet', 'Тихие часы', 'ночью уведомления копятся до утра']];
  function timeSelect(key, from, to, step) {
    var cur = S.me.settings[key], opts = [], t = from;
    while (t <= to) { var hh = ('0' + Math.floor(t / 60)).slice(-2), mm = ('0' + t % 60).slice(-2), v = hh + ':' + mm; opts.push(v); t += step; }
    if (opts.indexOf(cur) < 0) opts.push(cur);
    opts.sort();
    return '<select data-a="set-time" data-v="' + key + '">' + opts.map(function (v) { return '<option' + (v === cur ? ' selected' : '') + '>' + v + '</option>'; }).join('') + '</select>';
  }
  function scrNotify() {
    var me = S.me;
    if (!me || !me.user) return gate();
    var st = me.settings;
    var h = top('Уведомления');
    h += '<div class="card">' + NOTIF.map(function (n) {
      var extra = '';
      if (n[0] === 'morning') extra = timeSelect('morning_time', 330, 600, 15);
      if (n[0] === 'evening') extra = timeSelect('evening_time', 1020, 1350, 30);
      return '<div class="setrow"><div class="grow"><b>' + n[1] + '</b>' + (n[2] ? '<small>' + n[2] + '</small>' : '') + '</div>' + extra +
        '<label class="sw"><input type="checkbox" data-a="set" data-v="' + n[0] + '"' + (st[n[0]] ? ' checked' : '') + '><span></span></label></div>';
    }).join('') + '</div>';
    if (st.quiet) {
      h += '<div class="card"><h2>Тихие часы</h2><div class="row"><span class="muted">с</span>' + timeSelect('quiet_from', 1200, 1410, 30) + '<span class="muted">до</span>' + timeSelect('quiet_to', 300, 540, 30) + '</div></div>';
    }
    h += '<div class="card"><h2>Как к тебе обращаться</h2><div class="row"><input class="inp" maxlength="24" id="dname" placeholder="' + esc(me.user.name) + '" value="' + esc(me.user.display_name || '') + '">' +
      '<button class="icon-btn" data-a="save-name" aria-label="Сохранить">' + ic('check') + '</button></div><div class="tiny muted" style="margin-top:6px">Пусто — имя из дневника</div></div>';
    return h;
  }
  function scrCalendar() {
    var d = need('calendar', 600000);
    var h = top('Каникулы');
    if (!d) return h + skel(2);
    if (!d.loaded) return h + '<div class="card empty"><span class="big">🗓</span>Календарь ещё не загружен</div>';
    if (d.current) h += '<div class="card hero"><i class="blob b1"></i><h1>🏖 ' + esc(cap(d.current.title)) + '!</h1><div class="date">до ' + esc(dshort(d.current.end)) + '</div></div>';
    else if (d.next) h += '<div class="card hero"><i class="blob b1"></i><i class="blob b2"></i><h1>' + days(d.next.days) + ' до каникул</h1><div class="date">' + esc(cap(d.next.title)) + ': ' + esc(dshort(d.next.start)) + ' — ' + esc(dshort(d.next.end)) + '</div></div>';
    h += '<div class="card"><h2>Каникулы года</h2>' + d.vacations.map(function (v) {
      return '<div class="kv"' + (v.past ? ' style="opacity:.45"' : '') + '><b>' + esc(cap(v.title)) + '</b><span class="muted">' + esc(dshort(v.start)) + (v.end !== v.start ? ' — ' + esc(dshort(v.end)) : '') + '</span></div>';
    }).join('') + '</div>';
    if (d.holidays.length) h += '<div class="card"><h2>Ближайшие выходные-праздники</h2>' + d.holidays.map(function (x) { return '<div class="kv"><b>' + esc(cap(x.title)) + '</b><span class="muted">' + esc(dwd(x.date)) + '</span></div>'; }).join('') + '</div>';
    return h;
  }
  function scrBirthdays() {
    if (!needConnected()) return gate();
    var d = need('birthdays', 600000);
    var h = top('Именинники');
    if (!d) return h + skel(2);
    if (!d.items.length) return h + '<div class="card empty"><span class="big">🎂</span>В этом месяце никого не вижу<br><span class="small">(сайт школы показывает не всех)</span></div>';
    h += '<div class="card">' + d.items.map(function (x) {
      return '<div class="kv"' + (x.past ? ' style="opacity:.5"' : '') + '><span><b>' + (x.today ? '🎉 ' : '') + esc(x.name) + '</b>' + (x['class'] ? ' <span class="muted small">' + esc(x['class']) + '</span>' : '') + '</span><span class="muted">' + esc(x.date ? dshort(x.date) : '') + '</span></div>';
    }).join('') + '</div>';
    return h;
  }
  function scrNews() {
    var d = need('news', 300000);
    var h = top('Объявления');
    if (!d) return h + skel(2);
    if (!d.items.length) return h + '<div class="card empty"><span class="big">📭</span>Объявлений пока нет</div>';
    h += '<div class="card">' + d.items.map(function (x) {
      return '<div class="news"><b>' + esc(x.title) + '</b><span class="tiny muted">' + esc(x.date ? dshort(x.date) : '') + (x.author ? ' · ' + esc(x.author) : '') + '</span><p>' + esc(x.text) + '</p>' +
        (x.files && x.files.length ? '<div class="tiny muted">' + ic('clip', 's') + ' ' + esc(x.files.join(', ')) + ' — на сайте школы</div>' : '') + '</div>';
    }).join('') + '</div>';
    return h;
  }
  function scrArchive() {
    if (!needConnected()) return gate();
    var K = [['marks', 'star', 'Оценки', 'все за четверть'], ['attendance', 'warn', 'Посещаемость', 'пропуски и опоздания'],
      ['hw', 'book', 'ДЗ на неделю', 'со сроками'], ['schedule', 'clock', 'Расписание', 'на неделю']];
    var h = top('Архив') + '<p class="muted small" style="margin:-6px 4px 12px">Пришлю картинки в чат — Telegram хранит их на телефоне, так что откроются даже без интернета 📴</p>';
    h += '<div class="tiles">' + K.map(function (k) { return '<button class="tile" data-a="archive" data-v="' + k[0] + '"><div class="ic">' + ic(k[1]) + '</div><b>' + k[2] + '</b><small>' + k[3] + '</small></button>'; }).join('') + '</div>';
    return h;
  }
  function scrFeedback() {
    var h = top('Написать');
    h += '<div class="card"><p class="small muted" style="margin-top:0">Вопрос, идея или что-то сломалось — администратор ответит в чате с ботом. Он увидит твоё имя в Telegram, но не пароль и не оценки.</p>' +
      '<textarea id="fbtext" maxlength="2000" placeholder="Например: «не пришла вечерняя сводка»"></textarea><div style="margin-top:12px"><button class="btn" data-a="fb-send">' + ic('send', 's') + ' Отправить</button></div></div>';
    return h;
  }
  function scrPrivacy() {
    return top('Данные') + '<div class="card prose">' +
      '<p>🔒 <b>Пароль</b> ты пишешь боту в чат — сообщение сразу удаляется. Хранится не пароль, а его отпечаток (md5 — так его считает сам сайт школы) и длина, в <b>зашифрованном</b> виде.</p>' +
      '<p>📚 Чтобы замечать новые оценки и ДЗ, хранится копия дневника, оценок за четверть и расписания.</p>' +
      '<p>👀 Администратор видит только служебное: имя в Telegram, логин NetSchool, ФИО и класс, статус. <b>Оценок, ДЗ и паролей ему не видно.</b></p>' +
      '<p>🧹 «Отключить NetSchool» стирает вход и дневник, «Удалить все мои данные» — вообще всё.</p>' +
      '<p class="muted small">Это неофициальный помощник, со школой он не связан.</p></div>';
  }

  // ───────────────────────── шит (выезжающая панель) ─────────────────────────
  function openSheet(kind, v) { S.sheet = { kind: kind, v: v }; renderSheet(); $sheet.className = 'open'; updateBack(); haptic(); }
  function closeSheet() { S.sheet = null; $sheet.className = ''; updateBack(); }
  function renderSheet() {
    if (!S.sheet) return;
    if (S.sheet.kind === 'subj') $pane.innerHTML = subjectSheet(+S.sheet.v);
  }

  // ───────────────────────── отрисовка ─────────────────────────
  var SCREENS = { home: scrHome, diary: scrDiary, marks: scrMarks, schedule: scrSchedule, more: scrMore, look: scrLook, notify: scrNotify,
    calendar: scrCalendar, birthdays: scrBirthdays, news: scrNews, archive: scrArchive, feedback: scrFeedback, privacy: scrPrivacy, calc: scrCalc };
  var renderQueued = false;
  function render() {
    if (renderQueued) return;
    renderQueued = true;
    (W.requestAnimationFrame || setTimeout)(function () {
      renderQueued = false;
      if (!S.me) return;
      var fn = SCREENS[S.screen] || scrHome, active = D.activeElement, keep = active && active.id;
      var val = keep ? active.value : null;
      $app.innerHTML = fn();
      if (keep) { var el = D.getElementById(keep); if (el && val != null) { el.value = val; el.focus(); } }
      $dock.classList.remove('hidden');
      markDock();
      if (S.sheet) renderSheet();
      updateBack();
    });
  }

  // ───────────────────────── действия ─────────────────────────
  var saveTimer = 0;
  function saveTheme() {
    lsSet('ss:theme', S.theme);
    applyTheme();
    render();
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () {
      if (!S.me || !S.me.user) return;
      api('settings', { theme: S.theme }).then(function (me) { S.me.theme = me.theme; }).catch(function (e) { toast(e.message); });
    }, 700);
  }
  function saveSettings(patch) {
    var old = Object.assign({}, S.me.settings);
    Object.assign(S.me.settings, patch);
    render();
    api('settings', { settings: patch }).then(function (me) { S.me.settings = me.settings; lsSet('ss:me:' + UID, S.me); haptic(); })
      .catch(function (e) { S.me.settings = old; render(); toast(e.message); });
  }
  function eachItem(key, fn) {
    Object.keys(S.data).forEach(function (p) {
      var d = S.data[p];
      if (!d) return;
      if (p === 'dashboard') { (d.hw.items || []).concat(d.debts || []).forEach(function (x) { if (x.key === key) fn(x, d); }); }
      if (p.indexOf('diary') === 0 && d.days) {
        d.days.forEach(function (day) { day.items.forEach(function (x) { if (x.key === key) fn(x, d); }); });
        (d.carried || []).forEach(function (x) { if (x.key === key) fn(x, d); });
      }
    });
  }
  function toggleDone(key) {
    var target = null;
    eachItem(key, function (x) { if (target === null) target = !x.done; });
    if (target === null) return;
    eachItem(key, function (x) { x.done = target; });
    var dash = S.data.dashboard;
    if (dash) dash.hw.done = dash.hw.items.filter(function (x) { return x.done && x.hw; }).length;
    haptic(target ? 'ok' : 'sel');
    render();
    api('hw/done', { key: key, done: target }).then(function () {
      Object.keys(S.data).forEach(function (p) { if (UID && S.data[p]) lsSet(ckey(p), { t: S.at[p] || Date.now(), d: S.data[p] }); });
    }).catch(function (e) {
      eachItem(key, function (x) { x.done = !target; });
      render();
      toast(e.status ? e.message : 'Нет связи — отметка не сохранилась');
    });
  }
  function loadDetails(key, retry) {
    S.detLoading[key] = true;
    render();
    api('hw/details?key=' + encodeURIComponent(key)).then(function (j) {
      if (j._status === 202 && !retry) { setTimeout(function () { loadDetails(key, true); }, 5000); return; }
      S.detLoading[key] = false;
      S.det[key] = j.details;
      eachItem(key, function (x) { x.has_details = true; x.files = (j.details && j.details.files) ? j.details.files.length : 0; });
      render();
    }).catch(function (e) { S.detLoading[key] = false; S.open[key] = false; render(); toast(e.message || 'Не получилось'); });
  }
  function doSync() {
    if (S.syncing) return;
    S.syncing = true;
    render();
    var before = S.me && S.me.user ? S.me.user.last_ok : null;
    api('sync', {}).then(function (j) {
      if (j.status === 'limit') { S.syncing = false; render(); toast('Обновлял совсем недавно — можно через ' + Math.max(1, Math.round(j.retry / 60)) + ' мин'); return; }
      if (j.status === 'paused') { S.syncing = false; render(); toast('Сайт школы сейчас на паузе ⏸'); return; }
      toast('Обновляю… новое пришлю и в чат');
      var tries = 0;
      (function poll() {
        tries++;
        api('me').then(function (me) {
          var u = me.user;
          if (u && u.last_ok !== before && !u.syncing) {
            S.me = me; S.syncing = false; invalidate(); render(); toast('Готово ✅'); haptic('ok'); return;
          }
          if (tries < 24) setTimeout(poll, 5000); else { S.syncing = false; render(); }
        }).catch(function () { S.syncing = false; render(); });
      })();
    }).catch(function (e) { S.syncing = false; render(); toast(e.message || 'Нет связи'); });
  }
  function openBot() {
    var bot = S.me && S.me.bot;
    if (!bot) return;
    var url = 'https://t.me/' + bot;
    try { if (TG && TG.openTelegramLink) { TG.openTelegramLink(url); if (TG.close) TG.close(); return; } } catch (e) { /* ниже */ }
    W.location.href = url;
  }
  var ACT = {
    tab: function (v) { go(v); },
    go: function (v) { go(v); },
    reload: function () { invalidate(); S.err = {}; render(); },
    sync: function (v, el, e) { if (e) e.preventDefault(); doSync(); },
    done: function (v) { toggleDone(v); },
    det: function (v) {
      S.open[v] = !S.open[v];
      if (S.open[v] && S.det[v] === undefined) loadDetails(v); else render();
    },
    file: function (v) {
      var i = v.lastIndexOf('|');
      toast('Отправляю файл в чат…');
      api('hw/file', { key: v.slice(0, i), id: +v.slice(i + 1) }).then(function (j) {
        toast(j.ok ? 'Файл в чате с ботом 📎' : (j.status === 'limit' ? 'Лимит файлов на сегодня 🙈' : 'Не получилось скачать с сайта школы'));
      }).catch(function (e) { toast(e.message); });
    },
    week: function (v) { var p = v.split('|'); if (!p[1]) return; S.week[p[0]] = p[1]; render(); W.scrollTo(0, 0); haptic('sel'); },
    day: function (v) { var p = v.split('|'); S.day[p[0]] = p[1]; render(); haptic('sel'); },
    'diary-day': function (v) {
      var d = pdate(v), wd = (d.getDay() + 6) % 7; d.setDate(d.getDate() - wd);
      var mon = d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2);
      S.week.diary = mon; S.day[mon] = v; go('diary');
    },
    filter: function (v) { S.filter = v; render(); haptic('sel'); },
    subj: function (v) { openSheet('subj', v); },
    'sheet-close': function () { closeSheet(); },
    'calc-add': function (v) {
      var k = S.calcSubj; if (!k) return;
      (S.calc[k] = S.calc[k] || []).push(+v);
      if (S.calc[k].length > 30) S.calc[k].shift();
      haptic(); render();
    },
    'calc-rm': function (v) { var k = S.calcSubj; if (S.calc[k]) { S.calc[k].splice(+v, 1); render(); } },
    'calc-reset': function () { S.calc[S.calcSubj] = []; render(); },
    'calc-pick': function (v) { S.calcPick = +v; S.params = {}; render(); haptic('sel'); },
    'th-pal': function (v) { S.theme.palette = v; if (v === 'brutal' && S.theme.radius > 12) S.theme.radius = 10; saveTheme(); haptic('sel'); },
    'th-mode': function (v) { S.theme.mode = v; saveTheme(); haptic('sel'); },
    'th-accent': function (v) { S.theme.accent = v; saveTheme(); haptic('sel'); },
    'th-nav': function (v) { S.theme.nav = v; saveTheme(); haptic('sel'); },
    'th-reset': function () { S.theme = Object.assign({}, DEF_THEME); saveTheme(); },
    'open-bot': openBot,
    archive: function (v) {
      api('archive', { kind: v }).then(function () { toast('Рисую — картинка придёт в чат 📦'); haptic('ok'); }).catch(function (e) { toast(e.message); });
    },
    'fb-send': function () {
      var el = D.getElementById('fbtext'), text = el ? el.value.trim() : '';
      if (text.length < 2) { toast('Напиши хоть пару слов 🙂'); return; }
      api('feedback', { text: text }).then(function () { el.value = ''; toast('Отправлено ✅ Ответ придёт в чат'); haptic('ok'); }).catch(function (e) { toast(e.message); });
    },
    'save-name': function () {
      var el = D.getElementById('dname'), name = el ? el.value.trim() : '';
      api('settings', { display_name: name }).then(function (me) { S.me = me; render(); toast('Сохранил 👌'); }).catch(function (e) { toast(e.message); });
    },
    logout: function () {
      confirmBox('Отключить NetSchool? Сотру вход и данные дневника. Настройки останутся.', function () {
        api('logout', { confirm: true }).then(function () { return api('me'); }).then(function (me) { S.me = me; S.data = {}; go('more'); toast('Отключено'); }).catch(function (e) { toast(e.message); });
      });
    },
    'delete': function () {
      confirmBox('Удалить ВСЕ твои данные? Это нельзя отменить.', function () {
        api('delete', { confirm: true }).then(function () {
          try { Object.keys(W.localStorage).forEach(function (k) { if (k.indexOf('ss:') === 0) W.localStorage.removeItem(k); }); } catch (e) { /* ничего */ }
          S.me = { user: null, bot: S.me.bot, settings: S.me.settings, theme: DEF_THEME, version: S.me.version }; S.data = {}; go('home');
        }).catch(function (e) { toast(e.message); });
      });
    }
  };
  var INPUT = {
    'th-radius': function (el) { S.theme.radius = +el.value; saveTheme(); },
    'th-font': function (el) { S.theme.font = +el.value; saveTheme(); },
    'th-color': function (el) { S.theme.accent = el.value; saveTheme(); },
    'th-lite': function (el) { S.theme.lite = el.checked; saveTheme(); },
    set: function (el) { var p = {}; p[el.getAttribute('data-v')] = el.checked; saveSettings(p); },
    'set-time': function (el) { var p = {}; p[el.getAttribute('data-v')] = el.value; saveSettings(p); }
  };

  function onClick(e) {
    var el = e.target.closest ? e.target.closest('[data-a]') : null;
    if (!el || el.tagName === 'INPUT' || el.tagName === 'SELECT') return;
    var a = el.getAttribute('data-a');
    if (ACT[a]) ACT[a](el.getAttribute('data-v') || '', el, e);
  }
  function onChange(e) {
    var el = e.target, a = el.getAttribute && el.getAttribute('data-a');
    if (a && INPUT[a]) INPUT[a](el);
  }

  // ───────────────────────── старт ─────────────────────────
  function boot() {
    initTelegram();
    $app = D.getElementById('app'); $dock = D.getElementById('dock'); $sheet = D.getElementById('sheet');
    $pane = $sheet.querySelector('.pane'); $toast = D.getElementById('toast');
    S.theme = Object.assign({}, DEF_THEME, lsGet('ss:theme') || {});
    applyTheme();
    buildDock();
    D.addEventListener('click', onClick);
    D.addEventListener('change', onChange);
    var map = { calc: 'calc', marks: 'marks', diary: 'diary', schedule: 'schedule', settings: 'look', look: 'look', notify: 'notify', news: 'news',
      calendar: 'calendar', holidays: 'calendar', birthdays: 'birthdays', archive: 'archive', feedback: 'feedback', privacy: 'privacy', more: 'more', home: 'home' };
    S.screen = map[START] || 'home';
    if (TABS.indexOf(S.screen) < 0) S.stack = [{ screen: 'home', params: {}, y: 0 }];
    var cached = UID ? lsGet('ss:me:' + UID) : null;
    if (cached) { S.me = cached; render(); }
    if (!INIT && !DEV) {
      $app.innerHTML = '<div class="card empty" style="margin-top:40px"><span class="big">📱</span>Открой это приложение из Telegram — кнопка «Дневник» в чате с ботом.</div>';
      return;
    }
    api('me').then(function (me) {
      delete me._status;
      S.me = me;
      if (UID) lsSet('ss:me:' + UID, me);
      S.theme = Object.assign({}, DEF_THEME, me.theme || {});
      lsSet('ss:theme', S.theme);
      applyTheme();
      render();
    }).catch(function (e) {
      if (S.me) { setOffline(!e.status); return; }
      $app.innerHTML = errorCard(e.status ? e : { message: 'Нет связи с сервером 📴 Проверь интернет (и VPN) и открой ещё раз.' });
    });
    if ('serviceWorker' in navigator && location.protocol === 'https:') {
      navigator.serviceWorker.register('/app/sw.js', { scope: '/app/' }).catch(function () { /* без офлайна */ });
    }
    W.addEventListener('online', function () { invalidate(); render(); });
  }
  loadSdk(boot);
})();
