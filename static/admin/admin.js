/* School Sensor — админка. Без сборки и библиотек, ES2017. Пароли и оценки сюда не приходят вовсе. */
(function () {
  'use strict';
  var D = document, W = window, TZ = 'Asia/Ashgabat';
  var S = {
    csrf: null, admin: null, cur: null, timer: null, skew: 0, user: null, mres: null, mmust: null,
    uf: { q: '', status: '', cls: '', sort: 'seen' }, tpl: null, tplKey: null, fb: 'new', rt: null, classes: null,
    ev: { source: 'memory', level: '', kind: '', user_id: '' }
  };

  var STATUS = { active: 'Активен', 'new': 'Не подключён', paused: 'Пауза', bad_password: 'Пароль не подходит',
    bot_blocked: 'Бот заблокирован', banned: 'Заблокирован' };
  var FBS = { 'new': 'Новое', answered: 'Отвечено', closed: 'Закрыто' };
  var GROUPS = { greet: 'Приветствия', weather: 'Погода', mark: 'Оценки', hw: 'Домашка', debt: 'Долги',
    att: 'Посещаемость', birthday: 'Дни рождения', holiday: 'Праздники', vacation: 'Каникулы', term: 'Итоговые',
    mood: 'Главная Mini App', tip: 'Советы', welcome: 'Подключение' };
  var SNAPS = { diary: 'Дневник', report: 'Успеваемость', schedule: 'Расписание', totals: 'Итоговые',
    birthdays: 'Дни рождения', announcements: 'Объявления', calendar: 'Календарь', attendance: 'Посещаемость' };
  var LEVEL = { info: 'инфо', warn: 'внимание', error: 'ошибка' };

  // ───────────── утилиты ─────────────
  function $(s, el) { return (el || D).querySelector(s); }
  function $$(s, el) { return Array.prototype.slice.call((el || D).querySelectorAll(s)); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }
  function ic(n) { return '<svg class="i"><use href="#i-' + n + '"></use></svg>'; }
  function badge(cls, text, plain) { return '<span class="badge b-' + esc(cls) + (plain ? ' plain' : '') + '">' + esc(text) + '</span>'; }
  function num(n) { return n == null ? '—' : String(n).replace(/\B(?=(\d{3})+(?!\d))/g, '\u202f'); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function plural(n, a, b, c) {
    var m10 = n % 10, m100 = n % 100;
    return m10 === 1 && m100 !== 11 ? a : (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20) ? b : c);
  }
  function initials(s) {
    var p = String(s || '?').replace(/^@/, '').split(/\s+/).filter(Boolean);
    return ((p[0] || '?').charAt(0) + (p[1] ? p[1].charAt(0) : '')).toUpperCase();
  }
  function lsGet(k) { try { return W.localStorage.getItem(k); } catch (e) { return null; } }
  function lsSet(k, v) { try { W.localStorage.setItem(k, v); } catch (e) { /* приватный режим */ } }
  var FMT = null;
  try {
    FMT = new Intl.DateTimeFormat('ru-RU', { timeZone: TZ, day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' });
  } catch (e) { FMT = null; }
  function ts(iso) { if (!iso) return null; var t = Date.parse(iso); return isNaN(t) ? null : t; }
  function nowMs() { return Date.now() + S.skew; }
  function fmt(iso) {
    var t = ts(iso);
    if (t == null) return '—';
    if (FMT) return FMT.format(new Date(t)).replace(',', '');
    var d = new Date(t);
    return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + ' ' + pad(d.getHours()) + ':' + pad(d.getMinutes());
  }
  function ago(iso) {
    var t = ts(iso);
    if (t == null) return '—';
    var s = Math.round((nowMs() - t) / 1000);
    if (s < 0) {
      s = -s;
      if (s < 90) return 'через минуту';
      if (s < 3600) return 'через ' + Math.round(s / 60) + ' мин';
      if (s < 86400) return 'через ' + Math.round(s / 3600) + ' ч';
      return fmt(iso);
    }
    if (s < 45) return 'только что';
    if (s < 3600) return Math.max(1, Math.round(s / 60)) + ' мин назад';
    if (s < 86400) return Math.round(s / 3600) + ' ч назад';
    if (s < 172800) return 'вчера';
    if (s < 30 * 86400) return Math.round(s / 86400) + ' дн назад';
    return fmt(iso);
  }
  function toast(msg, bad) {
    var el = D.createElement('div');
    el.className = 'toast' + (bad ? ' bad' : '');
    el.textContent = msg;
    $('#toasts').appendChild(el);
    setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, bad ? 5500 : 2600);
  }
  function errBox(e) {
    return '<div class="card empty"><span class="em">😕</span>' + esc(e && e.message ? e.message : e) +
      '<div style="margin-top:12px"><button class="btn sm" data-a="reload">Повторить</button></div></div>';
  }
  function head(title, sub, acts) {
    return '<div class="head"><div><h1>' + esc(title) + '</h1>' + (sub ? '<p>' + sub + '</p>' : '') + '</div>' +
      (acts ? '<div class="acts">' + acts + '</div>' : '') + '</div>';
  }
  function seg(name, opts, cur) {
    return '<div class="seg">' + opts.map(function (o) {
      return '<button data-a="seg" data-n="' + name + '" data-v="' + esc(o[0]) + '" class="' + (o[0] === cur ? 'on' : '') + '">' + esc(o[1]) + '</button>';
    }).join('') + '</div>';
  }
  function select(id, opts, cur, cls) {
    return '<select class="inp ' + (cls || '') + '" id="' + id + '">' + opts.map(function (o) {
      return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(cur) ? ' selected' : '') + '>' + esc(o[1]) + '</option>';
    }).join('') + '</select>';
  }
  function toggle(attrs, on) { return '<span class="tg"><input type="checkbox" ' + attrs + (on ? ' checked' : '') + '><i></i></span>'; }

  // ───────────── API ─────────────
  function api(method, path, body) {
    var opt = { method: method, headers: {}, credentials: 'same-origin' };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    if (method !== 'GET' && S.csrf) opt.headers['X-CSRF-Token'] = S.csrf;
    return fetch('/admin/api/' + path, opt).then(function (r) {
      return r.text().then(function (t) {
        var j = null;
        try { j = t ? JSON.parse(t) : null; } catch (e) { j = null; }
        if (r.status === 401 && path.indexOf('login/') !== 0 && path !== 'session') {
          S.csrf = null;
          renderLogin('Сессия закончилась — войди снова.');
        }
        if (!r.ok) {
          var d = j && j.detail, msg = 'Ошибка ' + r.status;
          if (typeof d === 'string') msg = d;
          else if (d && d.length && d[0].msg) msg = d[0].msg;
          else if (r.status === 429) msg = 'Слишком часто — подожди немного';
          else if (r.status >= 500) msg = 'Сервер не ответил (' + r.status + ')';
          var err = new Error(msg);
          err.status = r.status;
          throw err;
        }
        return j;
      });
    }, function () { throw new Error('Нет связи с сервером'); });
  }

  // ───────────── модалка ─────────────
  function ask(o) {
    return new Promise(function (resolve) {
      if (S.mres) S.mres(false);
      S.mres = resolve;
      S.mmust = o.must || null;
      var inp = '';
      if (o.input === 'area') inp = '<textarea class="inp" id="m-in" rows="5" maxlength="3500" placeholder="' + esc(o.placeholder || '') + '"></textarea>';
      else if (o.input) inp = '<input class="inp" id="m-in" style="width:100%" autocomplete="off" placeholder="' + esc(o.placeholder || '') + '">';
      var m = $('#modal');
      $('.modal-pane', m).innerHTML = '<h3>' + esc(o.title) + '</h3>' + (o.text ? '<p>' + esc(o.text) + '</p>' : '') + inp +
        '<div class="row"><button class="btn" data-a="m-no">Отмена</button><button class="btn ' + (o.danger ? 'bad fill' : 'pri') +
        '" data-a="m-ok" id="m-ok"' + (o.must ? ' disabled' : '') + '>' + esc(o.ok || 'Ок') + '</button></div>';
      m.classList.remove('hidden');
      var f = $('#m-in') || $('#m-ok');
      if (f) f.focus();
    });
  }
  function closeModal(val) {
    $('#modal').classList.add('hidden');
    var r = S.mres;
    S.mres = null;
    if (r) r(val);
  }

  // ───────────── вход ─────────────
  function setMsg(text, bad) {
    var el = $('#lg-msg');
    if (!el) return;
    el.textContent = text;
    el.style.color = bad ? 'var(--bad)' : '';
  }
  function renderLogin(msg) {
    clearInterval(S.timer);
    S.timer = null;
    S.cur = null;
    closeDrawer();
    var saved = lsGet('ss:adm:id') || '';
    $('#root').innerHTML = '<div class="login"><div class="card"><img src="/brand/mark.svg" alt="">' +
      '<h1>Вход в админку</h1><p>Код придёт от бота в Telegram. Быстрее всего — написать боту <b>/admin</b>: придёт кнопка для входа.</p>' +
      '<div class="stack" id="lg-1"><label class="field"><span>Твой Telegram ID</span><input class="inp" id="lg-id" inputmode="numeric" autocomplete="username" placeholder="Например, 123456789" value="' + esc(saved) + '"></label>' +
      '<button class="btn pri" data-a="lg-req">Получить код</button></div>' +
      '<div class="stack hidden" id="lg-2"><label class="field"><span>Код из Telegram</span><input class="inp code" id="lg-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="••••••"></label>' +
      '<button class="btn pri" data-a="lg-ok">Войти</button><button class="btn ghost" data-a="lg-back">Другой ID</button></div>' +
      '<p class="small faint" id="lg-msg" style="margin:14px 0 0"></p></div></div>';
    setMsg(msg || 'Пароли и оценки учеников в админке недоступны — даже тебе 🙂');
  }
  function verify(id, code) {
    setMsg('Проверяю…');
    return api('POST', 'login/verify', { admin_id: id, code: code }).then(function (r) {
      S.csrf = r.csrf;
      S.admin = r.admin_id;
      if (r.now) S.skew = Date.parse(r.now) - Date.now();
      lsSet('ss:adm:id', String(r.admin_id));
      start();
    }).catch(function (e) {
      setMsg(e.message, true);
      var c = $('#lg-code');
      if (c) { c.value = ''; c.focus(); }
    });
  }

  // ───────────── каркас и маршруты ─────────────
  var ROUTES = {
    overview: [vOverview, 'Обзор', 'chart'], users: [vUsers, 'Ученики', 'users'], templates: [vTemplates, 'Заготовки', 'quote'],
    broadcast: [vBroadcast, 'Рассылка', 'mega'], feedback: [vFeedback, 'Обращения', 'inbox'],
    runtime: [vRuntime, 'Настройки', 'sliders'], invites: [vInvites, 'Приглашения', 'ticket'], events: [vEvents, 'Журнал', 'list']
  };
  function shell() {
    var nav = Object.keys(ROUTES).map(function (k) {
      var r = ROUTES[k];
      return '<a href="#/' + k + '" data-r="' + k + '">' + ic(r[2]) + '<span>' + r[1] + '</span>' +
        (k === 'feedback' ? '<span class="cnt hidden" id="fb-cnt"></span>' : '') + '</a>';
    }).join('');
    $('#root').innerHTML = '<div class="shell"><aside class="side" id="side"><div class="brand"><img src="/brand/mark.svg" alt="">' +
      '<div><b>School Sensor</b><span>админка</span></div></div><nav class="nav">' + nav + '</nav>' +
      '<div class="side-foot"><div class="who">Администратор · ID ' + esc(S.admin) + '</div>' +
      '<button data-a="theme">' + ic('moon') + 'Тема</button><button data-a="logout">' + ic('logout') + 'Выйти</button></div></aside>' +
      '<div><div class="topbar"><button class="btn icon ghost" data-a="side">' + ic('menu') + '</button><b>School Sensor</b></div>' +
      '<main class="main"><div class="wrap" id="view"></div></main></div></div>';
  }
  function start() {
    if (!/^#\//.test(location.hash)) history.replaceState(null, '', location.pathname + '#/overview');
    S.cur = null;
    shell();
    route();
    api('GET', 'feedback?status=new').then(function (r) { setFbCount(r['new']); }).catch(function () { /* не важно */ });
  }
  function route() {
    if (!S.csrf) return;
    if (!$('#view')) shell();
    var h = location.hash.replace(/^#\/?/, '').split('/');
    var name = ROUTES[h[0]] ? h[0] : 'overview', arg = h[1] ? decodeURIComponent(h[1]) : null;
    $$('.nav a').forEach(function (a) { a.classList.toggle('on', a.getAttribute('data-r') === name); });
    var side = $('#side');
    if (side) side.classList.remove('open');
    if (name === 'users' && S.cur === 'users' && $('#u-list')) {
      if (arg) openUser(arg); else closeDrawer();
      return;
    }
    clearInterval(S.timer);
    S.timer = null;
    closeDrawer();
    S.cur = name;
    var el = $('#view');
    el.innerHTML = '<div class="empty">Загружаю…</div>';
    D.title = ROUTES[name][1] + ' · School Sensor';
    ROUTES[name][0](el, arg);
  }
  function setFbCount(n) {
    var el = $('#fb-cnt');
    if (!el) return;
    el.textContent = n || '';
    el.classList.toggle('hidden', !n);
  }
  function live(name) { return S.cur === name && !D.hidden; }

  // ───────────── обзор ─────────────
  function kpi(v, l, sub, cls) {
    return '<div class="card kpi ' + (cls || '') + '"><span class="v">' + v + '</span><span class="l">' + esc(l) + '</span><span class="s">' + sub + '</span></div>';
  }
  function fillDays(daily, last) {
    var map = {};
    (daily || []).forEach(function (d) { map[d.day] = d; });
    var p = String(last || (daily.length ? daily[daily.length - 1].day : '')).split('-'), out = [];
    if (p.length < 3) return daily || [];
    for (var i = 13; i >= 0; i--) {
      var day = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] - i)).toISOString().slice(0, 10);
      out.push(map[day] || { day: day });
    }
    return out;
  }
  function bars(daily, series) {
    var max = 1;
    daily.forEach(function (d) {
      var s = 0;
      series.forEach(function (x) { s += d[x[0]] || 0; });
      if (s > max) max = s;
    });
    var cols = daily.map(function (d) {
      var tot = 0, tip = [];
      series.forEach(function (x) { tot += d[x[0]] || 0; tip.push(x[1] + ': ' + (d[x[0]] || 0)); });
      var parts = series.map(function (x) {
        var v = d[x[0]] || 0;
        return v ? '<i style="height:' + (v / tot * 100).toFixed(1) + '%;background:' + x[2] + '"></i>' : '';
      }).join('');
      return '<div class="col" title="' + esc(d.day + '\n' + tip.join('\n')) + '"><div class="stk" style="height:' +
        Math.max(3, tot / max * 100).toFixed(1) + '%">' + parts + '</div><span class="d">' + d.day.slice(8, 10) + '</span></div>';
    }).join('');
    return '<div class="bars">' + cols + '</div><div class="legend">' + series.map(function (x) {
      return '<span><i style="background:' + x[2] + '"></i>' + esc(x[1]) + '</span>';
    }).join('') + '</div>';
  }
  function evLine(e, withUser) {
    return '<div class="ev ' + esc(e.level) + '"><span class="t">' + fmt(e.ts) + '</span><span class="m"><b>' + esc(e.kind) + '</b> ' +
      esc(e.message) + (withUser && e.user_id ? ' · <a href="#/users/' + e.user_id + '">ID ' + e.user_id + '</a>' : '') + '</span></div>';
  }
  function vOverview(el, arg, silent) {
    api('GET', 'overview').then(function (o) {
      if (S.cur !== 'overview') return;
      if (o.now) S.skew = Date.parse(o.now) - Date.now();
      setFbCount(o.feedback_new);
      var u = o.users, sy = o.sync, br = o.breaker, rt = o.runtime, td = o.today || {}, pr = o.process, db = o.db;
      var sub = 'Версия ' + esc(o.version) + (pr.demo ? ' · <b>демо-режим</b>' : '') + (o.bot ? ' · <a href="https://t.me/' + esc(o.bot) + '" target="_blank" rel="noopener">@' + esc(o.bot) + '</a>' : ' · бот не подключён');
      var h = head('Обзор', sub, '<button class="btn" data-a="reload">' + ic('refresh') + 'Обновить</button>');
      var al = '';
      (o.problems || []).forEach(function (p) {
        al += '<div class="alert bad">' + ic('shield') + '<div class="grow"><b>Проблема настройки</b>' + esc(p) + '</div></div>';
      });
      if (rt.sync_paused || br.state !== 'closed') {
        var why = rt.sync_paused ? 'Опрос NetSchool на паузе' : (br.state === 'open' ? 'Автопауза: сайт школы отвечает ошибками' : 'Пробный запрос после автопаузы');
        al += '<div class="alert ' + (br.state === 'open' ? 'bad' : 'warn') + '"><span style="font-size:22px">⛔</span><div class="grow"><b>' + why + '</b>' +
          esc(br.reason || (rt.sync_paused ? 'Включено вручную' : '')) + (br.until ? ' · до ' + fmt(br.until) : '') +
          (br.opens ? ' · срабатываний: ' + br.opens : '') + '</div><button class="btn pri" data-a="sync" data-v="resume">Возобновить</button></div>';
      }
      if (rt.maintenance) {
        al += '<div class="alert warn"><span style="font-size:22px">🛠</span><div class="grow"><b>Включены техработы</b>Бот отвечает ученикам заглушкой</div>' +
          '<button class="btn" data-a="maint-off">Выключить</button></div>';
      }
      if (o.scheduler.enabled && !o.scheduler.alive) {
        al += '<div class="alert bad">' + ic('shield') + '<div class="grow"><b>Планировщик не работает</b>Проверь логи сервера — опросы и сводки не идут</div></div>';
      }
      if (rt.registration !== 'open') {
        al += '<div class="alert info">' + ic('ticket') + '<div class="grow"><b>Регистрация ' + (rt.registration === 'invite' ? 'по приглашениям' : 'закрыта') +
          '</b>Новые ученики ' + (rt.registration === 'invite' ? 'заходят только по ссылке-приглашению' : 'не могут подключиться') + '</div><a class="btn sm" href="#/invites">Приглашения</a></div>';
      }
      var k = '<div class="grid g4">' +
        kpi(num(u.total), 'Учеников', 'подключено ' + num(u.connected) + ' · новых сегодня ' + num(td.new_users || 0)) +
        kpi(num(u.seen_24h), 'Заходили за сутки', 'опрошены за 3 ч: ' + num(u.synced_3h), 'peach') +
        kpi(num(sy.last_hour), 'Опросов за час', 'ошибок ' + num(sy.errors_last_hour) + ' · в среднем ' + (sy.avg_seconds || 0) + ' с' +
          (sy.running ? ' · идёт ' + sy.running : ''), sy.errors_last_hour ? 'badc' : 'okc') +
        kpi(num(o.feedback_new || 0), 'Новых обращений', o.feedback_new ? '<a href="#/feedback">ответить →</a>' : 'всё разобрано 👌', o.feedback_new ? 'badc' : '') +
        '</div>';
      var days = fillDays(o.daily || [], (o.now || '').slice(0, 10));
      var charts = '<div class="grid g2" style="margin-top:14px"><div class="card"><h2>Опросы NetSchool <span class="muted">14 дней</span></h2>' +
        bars(days, [['sync_ok', 'успешно', 'var(--accent)'], ['sync_fail', 'ошибки', 'var(--bad)']]) + '</div>' +
        '<div class="card"><h2>Активность <span class="muted">14 дней</span></h2>' +
        bars(days, [['messages_out', 'сообщений от бота', 'var(--peach)'], ['app_requests', 'запросов Mini App', 'var(--ok)']]) + '</div></div>';
      var stRows = Object.keys(STATUS).map(function (s) {
        var v = u[s] || 0;
        return '<div class="row" style="gap:10px;flex-wrap:nowrap"><span style="width:160px">' + badge(s, STATUS[s]) + '</span><div class="meter grow"><i style="width:' +
          (u.total ? v / u.total * 100 : 0).toFixed(1) + '%"></i></div><b style="width:44px;text-align:right">' + num(v) + '</b></div>';
      }).join('');
      var cls = Object.keys(u.classes || {}).map(function (c) {
        return '<a class="badge b-new plain" href="#/users" data-a="cls" data-v="' + esc(c) + '">' + esc(c) + ' · ' + u.classes[c] + '</a>';
      }).join(' ');
      var dirty = 0;
      Object.keys(db.dirty || {}).forEach(function (x) { dirty += db.dirty[x]; });
      var sys = '<dl class="dl">' +
        '<dt>Память</dt><dd>' + (pr.rss_mb || '—') + ' МБ из 512 <div class="meter" style="margin-top:4px"><i style="width:' + Math.min(100, (pr.rss_mb || 0) / 5.12).toFixed(0) + '%;background:' + ((pr.rss_mb || 0) > 400 ? 'var(--bad)' : 'var(--accent)') + '"></i></div></dd>' +
        '<dt>Работает</dt><dd>' + pr.uptime_h + ' ч · Python ' + esc(pr.python) + '</dd>' +
        '<dt>База</dt><dd>' + esc(db.dialect) + ' · сброс раз в ' + db.flush_every_min + ' мин · последний ' + esc(db.last_flush || 'ещё не было') + ' · ждут записи: ' + dirty + '</dd>' +
        '<dt>Планировщик</dt><dd>' + (o.scheduler.alive ? 'работает' : (o.scheduler.enabled ? 'остановлен' : 'выключен')) + ' · в плане ' + o.scheduler.planned + ' · очередь ' + o.scheduler.queue + '</dd>' +
        '<dt>Опрос</dt><dd>каждые ' + rt.sync_interval + ' мин · одновременно ' + rt.sync_concurrency + ' · предохранитель: ' + esc(br.state) + ' (ошибок подряд ' + br.fail_streak + ')</dd>' +
        '<dt>Погода</dt><dd>' + (o.weather.ok ? 'обновлена в ' + esc(o.weather.updated) : (o.weather.error ? 'ошибка: ' + esc(o.weather.error) : 'ещё не загружена')) + '</dd>' +
        '</dl><div class="row" style="margin-top:14px"><button class="btn sm" data-a="sync" data-v="flush">Записать в БД сейчас</button>' +
        (rt.sync_paused ? '' : '<button class="btn sm bad" data-a="sync" data-v="pause">Стоп-кран: пауза опроса</button>') + '</div>';
      var warns = (o.warnings || []).length ? '<div class="timeline">' + o.warnings.map(function (e) { return evLine(e, true); }).join('') + '</div>' :
        '<div class="empty" style="padding:20px"><span class="em">✨</span>Тишина — ни одного предупреждения</div>';
      el.innerHTML = h + (al ? '<div class="stack" style="margin-bottom:14px">' + al + '</div>' : '') + k + charts +
        '<div class="grid g2" style="margin-top:14px"><div class="card"><h2>Статусы</h2><div class="stack" style="gap:9px">' + stRows + '</div>' +
        (u.paused_self ? '<p class="small muted" style="margin:12px 0 0">Сами поставили паузу уведомлений: ' + u.paused_self + '</p>' : '') +
        (u.bad_password ? '<div style="margin-top:12px"><button class="btn sm" data-a="bulk-bad">Вернуть в опрос всех с «пароль не подходит»</button></div>' : '') +
        (cls ? '<h2 style="margin-top:18px">Классы</h2><div class="row" style="gap:6px">' + cls + '</div>' : '') + '</div>' +
        '<div class="card"><h2>Система</h2>' + sys + '</div></div>' +
        '<div class="card" style="margin-top:14px"><h2>Последние предупреждения <a class="small" href="#/events" style="margin-left:auto">весь журнал →</a></h2>' + warns + '</div>';
      if (!S.timer) S.timer = setInterval(function () { if (live('overview') && !S.mres) vOverview(el, null, true); }, 30000);
    }).catch(function (e) { if (!silent) el.innerHTML = errBox(e); });
  }

  // ───────────── ученики ─────────────
  function userName(u) { return u.ns_name || u.first_name || u.tg || ('ID ' + u.id); }
  function vUsers(el, arg) {
    el.innerHTML = head('Ученики', 'Пароли и оценки здесь не видны — только служебная информация.',
      '<button class="btn" data-a="bulk-bad">Вернуть «пароль не подходит»</button>') +
      '<div class="filters"><input class="inp q" id="u-q" type="search" placeholder="Поиск: имя, @ник, ID, логин, класс" value="' + esc(S.uf.q) + '">' +
      select('u-status', [['', 'Все статусы']].concat(Object.keys(STATUS).map(function (k) { return [k, STATUS[k]]; })), S.uf.status) +
      select('u-cls', [['', 'Все классы']], '') +
      select('u-sort', [['seen', 'Недавно заходили'], ['created', 'Новые сверху'], ['errors', 'Больше ошибок'], ['name', 'По имени'], ['class', 'По классу']], S.uf.sort) +
      '<span class="muted" id="u-cnt"></span></div><div id="u-list"><div class="empty">Загружаю…</div></div>';
    loadUsers().then(function () { if (arg) openUser(arg); });
  }
  function loadUsers() {
    var f = S.uf;
    return api('GET', 'users?q=' + encodeURIComponent(f.q) + '&status=' + encodeURIComponent(f.status) + '&cls=' + encodeURIComponent(f.cls) + '&sort=' + f.sort).then(function (r) {
      S.classes = r.classes;
      var cs = $('#u-cls'), list = $('#u-list');
      if (!list) return;
      if (cs) {
        cs.innerHTML = '<option value="">Все классы</option>' + r.classes.map(function (c) {
          return '<option' + (c === f.cls ? ' selected' : '') + '>' + esc(c) + '</option>';
        }).join('');
      }
      $('#u-cnt').textContent = num(r.total) + ' ' + plural(r.total, 'ученик', 'ученика', 'учеников') + (r.total > r.items.length ? ' (показаны первые ' + r.items.length + ')' : '');
      list.innerHTML = r.items.length ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Ученик</th><th>Класс</th><th>Статус</th><th>Опрос</th><th>Ошибка</th><th>Заходил</th></tr></thead><tbody>' +
        r.items.map(userRow).join('') + '</tbody></table></div>' : '<div class="card empty"><span class="em">🔍</span>Никого не нашлось</div>';
    }).catch(function (e) { var l = $('#u-list'); if (l) l.innerHTML = errBox(e); });
  }
  function userRow(u) {
    return '<tr class="click" data-a="user" data-id="' + u.id + '"><td><div class="nm">' + esc(userName(u)) + '</div><div class="sub">' + esc((u.tg ? u.tg + ' · ' : '') + 'ID ' + u.id) + '</div></td>' +
      '<td>' + esc(u['class'] || '—') + '</td><td>' + badge(u.status, STATUS[u.status] || u.status) + (u.syncing ? ' <span class="badge b-new plain">опрос…</span>' : '') +
      (u.paused_until ? '<div class="sub">сам на паузе до ' + fmt(u.paused_until) + '</div>' : '') + '</td>' +
      '<td class="nowrap">' + ago(u.last_sync) + (u.last_ok && u.last_ok !== u.last_sync ? '<div class="sub">успешно ' + ago(u.last_ok) + '</div>' : '') + '</td>' +
      '<td>' + (u.error ? '<div class="err" title="' + esc(u.error) + '">' + esc(u.error) + '</div>' : '<span class="faint">—</span>') + (u.fails ? '<div class="sub">подряд: ' + u.fails + '</div>' : '') + '</td>' +
      '<td class="nowrap">' + ago(u.seen) + '</td></tr>';
  }
  function closeDrawer() {
    var d = $('#drawer');
    if (d) d.parentNode.removeChild(d);
    S.user = null;
  }
  function openUser(id) {
    var dr = $('#drawer');
    if (!dr) {
      dr = D.createElement('div');
      dr.className = 'drawer';
      dr.id = 'drawer';
      dr.innerHTML = '<div class="bk" data-a="drawer-close"></div><div class="pane"><div class="empty">Загружаю…</div></div>';
      D.body.appendChild(dr);
    }
    return api('GET', 'users/' + encodeURIComponent(id)).then(function (u) {
      S.user = u;
      var p = $('#drawer .pane');
      if (p) p.innerHTML = userCard(u);
    }).catch(function (e) { var p = $('#drawer .pane'); if (p) p.innerHTML = errBox(e); });
  }
  function userCard(u) {
    var s = u.settings || {}, b = [];
    if (u.connected) b.push('<button class="btn sm pri" data-a="ua" data-act="sync">' + ic('refresh') + 'Опросить сейчас</button>');
    if (u.status === 'active') b.push('<button class="btn sm" data-a="ua" data-act="pause">На паузу</button>');
    if ((u.status === 'paused' || u.status === 'bad_password') && u.connected) b.push('<button class="btn sm ok" data-a="ua" data-act="resume">Вернуть в опрос</button>');
    b.push('<button class="btn sm" data-a="ua" data-act="message">Написать</button>');
    if (u.connected) b.push('<button class="btn sm" data-a="ua" data-act="reset_creds">Сбросить вход</button>');
    b.push(u.status === 'banned' ? '<button class="btn sm ok" data-a="ua" data-act="unblock">Разблокировать</button>' : '<button class="btn sm bad" data-a="ua" data-act="block">Заблокировать</button>');
    b.push('<button class="btn sm bad" data-a="ua" data-act="delete">Удалить</button>');
    var on = [['morning', 'Утро ' + s.morning_time], ['evening', 'Вечер ' + s.evening_time], ['n_marks', 'Оценки'], ['n_hw', 'ДЗ'],
      ['hw_instant', 'ДЗ сразу'], ['n_attendance', 'Посещаемость'], ['n_debts', 'Долги'], ['n_news', 'Объявления'], ['n_totals', 'Итоговые'],
      ['reactions', 'Реакции'], ['quiet', 'Тихие часы ' + s.quiet_from + '–' + s.quiet_to], ['cheatsheet', 'Шпаргалка'],
      ['weekly_archive', 'Архив'], ['weather', 'Погода']].map(function (x) {
      return '<span class="badge plain ' + (s[x[0]] ? 'b-ok' : 'b-closed') + '">' + esc(x[1]) + '</span>';
    }).join(' ');
    var snaps = Object.keys(u.snapshots || {}).map(function (k) {
      return '<dt>' + esc(SNAPS[k] || k) + '</dt><dd>' + ago(u.snapshots[k]) + '</dd>';
    }).join('');
    var fb = (u.feedback || []).map(function (f) {
      return '<div class="tpl"><div class="row small">' + badge(f.status, FBS[f.status] || f.status) + '<span class="faint">' + fmt(f.created) + '</span></div><div class="txt">' + esc(f.text) + '</div>' +
        (f.reply ? '<div class="preview small">' + esc(f.reply) + '</div>' : '') + '</div>';
    }).join('');
    return '<div class="pane-h"><div class="ava">' + esc(initials(userName(u))) + '</div><div class="grow"><h2>' + esc(userName(u)) + '</h2>' +
      '<div class="muted small">' + esc((u.tg ? u.tg + ' · ' : '') + 'ID ' + u.id) + (u['class'] ? ' · ' + esc(u['class']) : '') + '</div>' +
      '<div style="margin-top:6px">' + badge(u.status, STATUS[u.status] || u.status) + (u.syncing ? ' <span class="badge b-new plain">опрос идёт…</span>' : '') + '</div></div>' +
      '<button class="btn icon ghost" data-a="drawer-close" title="Закрыть">' + ic('x') + '</button></div>' +
      '<div class="row" style="gap:6px">' + b.join('') + '</div>' +
      '<div class="card"><dl class="dl">' +
      '<dt>Логин NetSchool</dt><dd>' + esc(u.ns_login || '—') + '</dd>' +
      '<dt>Подключён</dt><dd>' + (u.connected ? 'да' : 'нет') + (u.consent ? ' · согласие ' + fmt(u.consent) : '') + '</dd>' +
      '<dt>С нами с</dt><dd>' + fmt(u.created) + (u.invite ? ' · приглашение ' + esc(u.invite) : '') + '</dd>' +
      '<dt>Заходил</dt><dd>' + ago(u.seen) + '</dd>' +
      '<dt>Последний опрос</dt><dd>' + ago(u.last_sync) + (u.last_ok ? ' · успешный ' + ago(u.last_ok) : '') + '</dd>' +
      '<dt>Следующий</dt><dd>' + (u.next_sync ? ago(u.next_sync) + ' (' + fmt(u.next_sync) + ')' : '—') + '</dd>' +
      '<dt>Ошибка</dt><dd>' + (u.error ? '<span style="color:var(--bad)">' + esc(u.error) + '</span>' + (u.fails ? ' · подряд ' + u.fails : '') : '—') + '</dd>' +
      (u.paused_until ? '<dt>Сам на паузе</dt><dd>до ' + fmt(u.paused_until) + '</dd>' : '') +
      '<dt>Закреп</dt><dd>' + (u.pinned ? 'шпаргалка закреплена' : 'нет') + ' · в очереди тихих часов: ' + (u.pending || 0) + '</dd>' +
      '<dt>Тема</dt><dd>' + esc(u.theme || '—') + '</dd>' +
      '</dl></div>' +
      '<div class="card"><h2>Уведомления</h2><div class="row" style="gap:6px">' + on + '</div></div>' +
      (snaps ? '<div class="card"><h2>Данные обновлены</h2><dl class="dl">' + snaps + '</dl></div>' : '') +
      (fb ? '<div class="card"><h2>Обращения</h2><div class="stack">' + fb + '</div></div>' : '') +
      '<div class="card"><h2>События</h2>' + ((u.events || []).length ? '<div class="timeline">' + u.events.map(function (e) { return evLine(e, false); }).join('') + '</div>' : '<div class="muted">Пока ничего</div>') + '</div>';
  }

  // ───────────── заготовки ─────────────
  function findCat(key) {
    var c = null;
    (S.tpl ? S.tpl.catalog : []).forEach(function (x) { if (x.key === key) c = x; });
    return c;
  }
  function vTemplates(el) {
    api('GET', 'templates').then(function (r) {
      S.tpl = r;
      if (!S.tplKey || !findCat(S.tplKey)) S.tplKey = r.catalog[0].key;
      renderTemplates(el);
    }).catch(function (e) { el.innerHTML = errBox(e); });
  }
  function renderTemplates(el) {
    var r = S.tpl, counts = {}, grp = null, cat = '';
    r.items.forEach(function (t) {
      var c = counts[t.key] || (counts[t.key] = [0, 0]);
      c[1]++;
      if (t.enabled) c[0]++;
    });
    r.catalog.forEach(function (c) {
      var g = c.key.split('.')[0], n = counts[c.key] || [0, 0];
      if (g !== grp) { grp = g; cat += '<div class="grp">' + esc(GROUPS[g] || g) + '</div>'; }
      cat += '<a href="#" data-a="tpl-key" data-k="' + esc(c.key) + '" class="' + (c.key === S.tplKey ? 'on' : '') + '"><span>' + esc(c.title) + '</span><span class="n">' +
        n[0] + (n[1] > n[0] ? '/' + n[1] : '') + '</span></a>';
    });
    var c = findCat(S.tplKey), items = r.items.filter(function (t) { return t.key === S.tplKey; });
    var phs = String(c.placeholders || '').split(/\s+/).filter(Boolean);
    var subj = /^(mark|debt|att|term)\./.test(c.key);
    el.innerHTML = head('Заготовки фраз', 'Бот берёт случайную включённую фразу и старается не повторяться. Текст — обычный, без HTML; эмодзи можно.') +
      '<div class="tpl-layout"><div class="card cat">' + cat + '</div><div>' +
      '<div class="card stack"><div><h2>' + esc(c.title) + ' <span class="muted mono">' + esc(c.key) + '</span></h2>' +
      '<div class="muted small">' + (phs.length ? 'Подстановки (клик — вставить): ' + phs.map(function (p) {
        return '<button class="ph" data-a="ph" data-p="' + esc(p) + '">' + esc(p) + '</button>';
      }).join('') : 'Подстановок нет — просто текст.') + '</div></div>' +
      '<textarea class="inp" id="tpl-text" rows="3" maxlength="600" placeholder="Новая фраза…"></textarea>' +
      '<div class="row">' + (subj ? '<input class="inp" id="tpl-subj" maxlength="64" placeholder="Только для предмета, напр. «алгебр» (необязательно)" style="flex:1 1 260px">' : '<span class="grow"></span>') +
      '<button class="btn" data-a="tpl-prev">Превью</button><button class="btn pri" data-a="tpl-add">' + ic('plus') + 'Добавить</button></div><div id="tpl-pv"></div></div>' +
      '<div class="stack" style="margin-top:14px" id="tpl-items">' + (items.length ? items.map(tplItem).join('') :
        '<div class="card empty"><span class="em">✍️</span>Пока пусто — бот обойдётся стандартной фразой или пропустит этот кусочек.</div>') + '</div></div></div>';
  }
  function tplItem(t) {
    return '<div class="tpl' + (t.enabled ? '' : ' off') + '" data-id="' + t.id + '"><div class="txt">' + esc(t.text) + '</div>' +
      '<div class="row small muted">' + (t.subject ? badge('new', 'только: ' + t.subject, true) : '') + '<span class="faint">#' + t.id + '</span><span class="grow"></span>' +
      '<label class="chk">' + toggle('data-c="tpl-on" data-id="' + t.id + '"', t.enabled) + 'вкл</label>' +
      '<button class="btn sm ghost" data-a="tpl-edit" data-id="' + t.id + '">Изменить</button>' +
      '<button class="btn sm ghost bad" data-a="tpl-del" data-id="' + t.id + '">Удалить</button></div></div>';
  }
  function tplById(id) {
    var f = null;
    S.tpl.items.forEach(function (t) { if (String(t.id) === String(id)) f = t; });
    return f;
  }
  function tplSave(t, patch) {
    var body = { key: t.key, text: t.text, subject: t.subject || '', enabled: t.enabled };
    Object.keys(patch).forEach(function (k) { body[k] = patch[k]; });
    return api('PUT', 'templates/' + t.id, body).then(function (r) {
      S.tpl.items = S.tpl.items.map(function (x) { return x.id === r.id ? r : x; });
      return r;
    });
  }
  function previewInto(box, text) {
    if (!text.trim()) { box.innerHTML = ''; return; }
    api('POST', 'templates/preview', { text: text }).then(function (r) {
      box.innerHTML = r.ok ? '<div class="small muted" style="margin-bottom:6px">Так увидит ученик (с примером подстановок):</div><div class="tg-bubble"></div>' :
        '<div class="alert bad">' + esc(r.error) + '</div>';
      var bb = $('.tg-bubble', box);
      if (bb) bb.textContent = r.text;
    }).catch(function (e) { toast(e.message, true); });
  }

  // ───────────── рассылка ─────────────
  function vBroadcast(el) {
    var cl = S.classes ? Promise.resolve({ classes: S.classes }) : api('GET', 'classes');
    cl.then(function (r) {
      S.classes = r.classes;
      var opts = [['all', 'Все ученики'], ['active', 'Подключённые и активные'], ['not_connected', 'Не подключили NetSchool'],
        ['bad_password', '«Пароль не подходит»'], ['me', 'Только мне (проверка)']].concat(r.classes.map(function (c) { return ['class:' + c, 'Класс ' + c]; }));
      el.innerHTML = head('Рассылка', 'Сообщение придёт от бота. Отправка идёт плавно, с учётом лимитов Telegram.') +
        '<div class="grid g2"><div class="card stack">' +
        '<label class="field"><span>Заголовок (необязательно)</span><input class="inp" id="b-title" maxlength="100" placeholder="Например: Техработы в субботу"></label>' +
        '<label class="field"><span>Текст</span><textarea class="inp" id="b-text" rows="8" maxlength="3500" placeholder="Что сказать ученикам?"></textarea><span class="hint" id="b-len">0 / 3500</span></label>' +
        '<label class="field"><span>Кому</span>' + select('b-target', opts, 'all') + '</label>' +
        '<label class="chk">' + toggle('id="b-html"', false) + 'HTML-разметка (&lt;b&gt;, &lt;i&gt;, &lt;a href&gt;)</label>' +
        '<div class="row"><span class="muted" id="b-count"></span><span class="grow"></span><button class="btn pri" data-a="b-send">' + ic('mega') + 'Отправить</button></div></div>' +
        '<div class="card"><h2>Превью</h2><div class="tg-bubble" id="b-prev"><span class="faint">Начни писать — тут появится сообщение</span></div></div></div>' +
        '<div class="card" style="margin-top:14px"><h2>История рассылок</h2><div id="b-hist"><div class="muted">Загружаю…</div></div></div>';
      bCount();
      loadHist();
    }).catch(function (e) { el.innerHTML = errBox(e); });
  }
  function bPreview() {
    var t = $('#b-text'), p = $('#b-prev');
    if (!t || !p) return;
    $('#b-len').textContent = t.value.length + ' / 3500';
    var title = $('#b-title').value.trim(), html = $('#b-html').checked, body = t.value;
    if (!body.trim() && !title) { p.innerHTML = '<span class="faint">Начни писать — тут появится сообщение</span>'; return; }
    var safe = esc(body);
    if (html) {
      safe = safe.replace(/&lt;(\/?)(b|i|u|s|code)&gt;/g, '<$1$2>')
        .replace(/&lt;a href=&quot;(https?:\/\/[^&]+?)&quot;&gt;/g, '<a href="$1" target="_blank" rel="noopener">').replace(/&lt;\/a&gt;/g, '</a>');
    }
    p.innerHTML = (title ? '📣 <b>' + esc(title) + '</b>\n\n' : '') + safe;
  }
  var bTimer = null;
  function bCount() {
    clearTimeout(bTimer);
    bTimer = setTimeout(function () {
      var tg = $('#b-target');
      if (!tg) return;
      api('POST', 'broadcast', { text: '.', target: tg.value, dry_run: true }).then(function (r) {
        var c = $('#b-count');
        if (c) c.textContent = 'Получат: ' + num(r.count) + ' ' + plural(r.count, 'человек', 'человека', 'человек');
        S.bcount = r.count;
      }).catch(function () { /* не критично */ });
    }, 200);
  }
  function loadHist() {
    api('GET', 'broadcasts').then(function (r) {
      var h = $('#b-hist');
      if (!h) return;
      var TG = { all: 'все', active: 'активные', not_connected: 'не подключены', bad_password: 'пароль не подходит', me: 'только мне' };
      h.innerHTML = r.items.length ? '<div class="tbl-wrap" style="box-shadow:none"><table class="tbl"><thead><tr><th>#</th><th>Когда</th><th>Кому</th><th>Текст</th><th>Статус</th><th class="right">Дошло</th></tr></thead><tbody>' +
        r.items.map(function (b) {
          var txt = String(b.text || '').replace(/<[^>]+>/g, '');
          return '<tr><td class="faint">' + b.id + '</td><td class="nowrap">' + fmt(b.created) + '</td><td>' + esc(TG[b.target] || String(b.target).replace('class:', 'класс ')) + '</td>' +
            '<td><div class="err" style="color:inherit;max-width:360px" title="' + esc(txt) + '">' + esc(txt) + '</div></td><td>' + badge(b.status, b.status === 'done' ? 'готово' : 'идёт') +
            '</td><td class="right nowrap">' + num(b.sent) + ' / ' + num(b.total) + (b.failed ? ' <span style="color:var(--bad)">· ✗' + b.failed + '</span>' : '') + '</td></tr>';
        }).join('') + '</tbody></table></div>' : '<div class="muted">Рассылок ещё не было</div>';
      var going = r.items.some(function (b) { return b.status === 'sending'; });
      clearInterval(S.timer);
      S.timer = going ? setInterval(function () { if (live('broadcast')) loadHist(); }, 3000) : null;
    }).catch(function () { /* потом */ });
  }

  // ───────────── обращения ─────────────
  function vFeedback(el) {
    el.innerHTML = head('Обращения', 'Ученики пишут через бота или Mini App — твой ответ придёт им в Telegram.',
      seg('fb', [['new', 'Новые'], ['answered', 'Отвеченные'], ['closed', 'Закрытые'], ['', 'Все']], S.fb)) + '<div id="fb-list"><div class="empty">Загружаю…</div></div>';
    loadFb();
  }
  function loadFb() {
    api('GET', 'feedback?status=' + encodeURIComponent(S.fb)).then(function (r) {
      setFbCount(r['new']);
      var l = $('#fb-list');
      if (!l) return;
      l.innerHTML = r.items.length ? '<div class="stack">' + r.items.map(fbItem).join('') + '</div>' :
        '<div class="card empty"><span class="em">📭</span>' + (S.fb === 'new' ? 'Новых обращений нет — красота' : 'Пусто') + '</div>';
    }).catch(function (e) { var l = $('#fb-list'); if (l) l.innerHTML = errBox(e); });
  }
  function fbItem(f) {
    var u = f.user, nm = u ? (u.first_name || u.tg || 'Ученик') : 'Ученик удалил аккаунт';
    return '<div class="card stack"><div class="row" style="flex-wrap:nowrap"><div class="ava" style="width:38px;height:38px;font-size:14px;border-radius:12px">' + esc(initials(nm)) + '</div>' +
      '<div class="grow"><b>' + esc(nm) + '</b> <span class="muted small">' + (u ? esc([u.tg, u['class']].filter(Boolean).join(' · ')) : '') + '</span>' +
      '<div class="small faint">#' + f.id + ' · ' + fmt(f.created_at) + (u ? ' · <a href="#/users/' + f.user_id + '">профиль</a>' : '') + '</div></div>' + badge(f.status, FBS[f.status] || f.status) + '</div>' +
      '<div style="white-space:pre-wrap;overflow-wrap:anywhere">' + esc(f.text) + '</div>' +
      (f.reply ? '<div class="preview"><b>Ответ</b> <span class="small faint">' + fmt(f.replied_at) + '</span>\n' + esc(f.reply) + '</div>' : '') +
      (f.status !== 'closed' && u ? '<textarea class="inp" rows="2" maxlength="3500" placeholder="Ответ ученику…" id="fb-r-' + f.id + '"></textarea>' +
        '<div class="row"><span class="grow"></span><button class="btn sm" data-a="fb-close" data-id="' + f.id + '">Закрыть' + (f.reply ? '' : ' без ответа') + '</button>' +
        '<button class="btn sm pri" data-a="fb-reply" data-id="' + f.id + '">Ответить</button></div>' : '') + '</div>';
  }

  // ───────────── живые настройки ─────────────
  var RT_GROUPS = [
    ['Опрос NetSchool', 'Бережём сайт школы: при ошибках опрос сам встанет на паузу.', ['sync_paused', 'sync_interval', 'sync_jitter', 'sync_concurrency', 'night_from', 'night_to', 'slow_factor',
      'report_every', 'details_per_sync', 'announcements', 'breaker_streak', 'breaker_cooldown']],
    ['Бот', '', ['maintenance', 'maintenance_text', 'registration', 'welcome_note', 'weekly_archive_time']],
    ['Лимиты на ученика', '', ['manual_sync_minutes', 'files_per_day', 'feedback_per_day']],
    ['Калькулятор оценок', 'С какой дробной части средний балл округляется вверх.', ['calc_sure', 'calc_maybe']],
    ['Звонки', 'Нужны для «сейчас идёт / следующий урок» и для времени сводок.', ['bells']]
  ];
  var FLOATS = { sync_jitter: 0.01, slow_factor: 0.5, calc_sure: 0.01, calc_maybe: 0.01 };
  var TIMES = { night_from: 1, night_to: 1, weekly_archive_time: 1 };
  function vRuntime(el) {
    api('GET', 'runtime').then(function (r) { S.rt = r; renderRuntime(el); }).catch(function (e) { el.innerHTML = errBox(e); });
  }
  function rtInput(k, v, def) {
    if (typeof def === 'boolean') return toggle('data-k="' + k + '"', v);
    if (k === 'registration') {
      return '<select class="inp" data-k="' + k + '">' + [['open', 'Открыта всем'], ['invite', 'По приглашениям'], ['closed', 'Закрыта']].map(function (o) {
        return '<option value="' + o[0] + '"' + (o[0] === v ? ' selected' : '') + '>' + o[1] + '</option>';
      }).join('') + '</select>';
    }
    if (TIMES[k]) return '<input class="inp" type="time" data-k="' + k + '" value="' + esc(v) + '">';
    if (typeof def === 'number') return '<input class="inp" type="number" data-k="' + k + '" step="' + (FLOATS[k] || 1) + '" value="' + esc(v) + '">';
    return '<input class="inp" data-k="' + k + '" maxlength="300" value="' + esc(v) + '">';
  }
  function renderRuntime(el) {
    var r = S.rt, V = r.values, DF = r.defaults, H = r.help;
    var html = head('Живые настройки', 'Меняются без перезапуска и сохраняются в базе.',
      '<button class="btn" data-a="rt-reset">Отменить</button><button class="btn pri" data-a="rt-save" id="rt-save" disabled>Сохранить</button>');
    RT_GROUPS.forEach(function (g) {
      html += '<div class="card" style="margin-bottom:14px"><h2>' + esc(g[0]) + (g[1] ? ' <span class="muted">' + esc(g[1]) + '</span>' : '') + '</h2>';
      g[2].forEach(function (k) {
        if (!(k in V)) return;
        if (k === 'bells') {
          var nums = Object.keys(V.bells).sort(function (a, b) { return a - b; });
          html += '<div class="bells" data-k="bells">' + nums.map(function (n) {
            return '<label>' + n + '<input class="inp" type="time" data-bell="' + n + '" data-i="0" value="' + esc(V.bells[n][0]) + '"><input class="inp" type="time" data-bell="' + n + '" data-i="1" value="' + esc(V.bells[n][1]) + '"></label>';
          }).join('') + '</div>';
          return;
        }
        var dv = DF[k];
        html += '<div class="rt" data-row="' + k + '"><div class="k">' + esc(H[k] || k) + '<small>' + k + '</small></div><div>' + rtInput(k, V[k], dv) + '</div>' +
          '<div class="small faint nowrap">' + (typeof dv === 'boolean' ? '' : 'по умолч.: ' + esc(dv === '' ? '—' : dv)) + '</div></div>';
      });
      html += '</div>';
    });
    el.innerHTML = html;
  }
  function rtCollect() {
    var V = S.rt.values, patch = {};
    $$('[data-k]').forEach(function (inp) {
      var k = inp.getAttribute('data-k');
      if (k === 'bells' || !(k in V)) return;
      var def = S.rt.defaults[k], v;
      if (typeof def === 'boolean') v = inp.checked;
      else if (typeof def === 'number') v = inp.value === '' ? V[k] : Number(inp.value);
      else v = inp.value;
      var row = inp.closest('.rt');
      var changed = String(v) !== String(V[k]);
      if (row) row.classList.toggle('changed', changed);
      if (changed) patch[k] = v;
    });
    var bells = {}, bch = false;
    $$('[data-bell]').forEach(function (inp) {
      var n = inp.getAttribute('data-bell');
      bells[n] = bells[n] || ['', ''];
      bells[n][+inp.getAttribute('data-i')] = inp.value;
    });
    Object.keys(bells).forEach(function (n) {
      if (!V.bells[n] || bells[n][0] !== V.bells[n][0] || bells[n][1] !== V.bells[n][1]) bch = true;
    });
    if (bch) patch.bells = bells;
    var b = $('#rt-save');
    if (b) b.disabled = !Object.keys(patch).length;
    return patch;
  }

  // ───────────── приглашения ─────────────
  function vInvites(el) {
    api('GET', 'invites').then(function (r) {
      var reg = r.registration;
      el.innerHTML = head('Приглашения', 'Ссылка вида t.me/бот?start=inv_КОД — по ней можно зайти, даже когда регистрация только по приглашениям.') +
        '<div class="grid g2"><div class="card"><h2>Регистрация</h2>' + seg('reg', [['open', 'Открыта всем'], ['invite', 'По приглашениям'], ['closed', 'Закрыта']], reg) +
        '<p class="small muted" style="margin:12px 0 0">' + (reg === 'open' ? 'Любой может написать боту и подключиться.' : reg === 'invite' ? 'Новые ученики — только по ссылке-приглашению. Уже подключённых это не касается.' : 'Новых не принимаем. Уже подключённые пользуются как обычно.') + '</p></div>' +
        '<div class="card stack"><h2>Новое приглашение</h2><div class="row"><input class="inp grow" id="i-note" maxlength="100" placeholder="Для кого, напр. «11В пилот»">' +
        '<input class="inp" id="i-uses" type="number" min="1" max="1000" value="30" style="width:90px" title="Сколько раз можно использовать"></div>' +
        '<div class="row"><input class="inp grow" id="i-code" maxlength="24" placeholder="Свой код (необязательно)"><input class="inp" id="i-days" type="number" min="0" max="365" value="14" style="width:90px" title="Дней действует (0 — бессрочно)">' +
        '<button class="btn pri" data-a="inv-add">' + ic('plus') + 'Создать</button></div><div class="small faint">Число слева — сколько человек, справа — сколько дней (0 — бессрочно).</div></div></div>' +
        '<div style="margin-top:14px">' + (r.items.length ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Код</th><th>Для кого</th><th>Использовано</th><th>Действует до</th><th>Статус</th><th>Ссылка</th><th></th></tr></thead><tbody>' +
          r.items.map(function (i) {
            return '<tr><td class="mono">' + esc(i.code) + '</td><td>' + esc(i.note || '—') + '</td><td>' + i.uses + ' / ' + i.max_uses + '</td><td class="nowrap">' + (i.expires ? fmt(i.expires) : 'бессрочно') + '</td>' +
              '<td>' + (i.ok ? badge('ok', 'работает') : badge('closed', i.active ? 'исчерпано' : 'выключено')) + '</td>' +
              '<td>' + (i.link ? '<button class="btn sm" data-a="copy" data-v="' + esc(i.link) + '">' + ic('copy') + 'Копировать</button>' : '<span class="faint small">бот не подключён</span>') + '</td>' +
              '<td class="nowrap right"><button class="btn sm ghost" data-a="inv-toggle" data-v="' + esc(i.code) + '">' + (i.active ? 'Выключить' : 'Включить') + '</button>' +
              '<button class="btn sm ghost bad" data-a="inv-del" data-v="' + esc(i.code) + '">Удалить</button></td></tr>';
          }).join('') + '</tbody></table></div>' : '<div class="card empty"><span class="em">🎟</span>Приглашений пока нет</div>') + '</div>';
    }).catch(function (e) { el.innerHTML = errBox(e); });
  }

  // ───────────── журнал ─────────────
  function vEvents(el) {
    var f = S.ev;
    el.innerHTML = head('Журнал', 'Служебные события: опросы, ошибки, действия админа. Свежие — из памяти, архив — из базы (хранится 30 дней).',
      seg('evsrc', [['memory', 'Свежие'], ['db', 'Архив']], f.source)) +
      '<div class="filters">' + select('e-level', [['', 'Все уровни'], ['info', 'Инфо'], ['warn', 'Внимание'], ['error', 'Ошибки']], f.level) +
      select('e-kind', [['', 'Все типы']], '') + '<input class="inp" id="e-user" inputmode="numeric" placeholder="ID ученика" style="width:140px" value="' + esc(f.user_id) + '">' +
      '<button class="btn" data-a="reload">' + ic('refresh') + 'Обновить</button></div><div id="e-list"><div class="empty">Загружаю…</div></div>';
    loadEvents();
  }
  function loadEvents() {
    var f = S.ev;
    api('GET', 'events?source=' + f.source + '&level=' + f.level + '&kind=' + encodeURIComponent(f.kind) + (f.user_id ? '&user_id=' + encodeURIComponent(f.user_id) : '') + '&limit=300').then(function (r) {
      var k = $('#e-kind'), l = $('#e-list');
      if (!l) return;
      if (k) {
        k.innerHTML = '<option value="">Все типы</option>' + r.kinds.concat(f.kind && r.kinds.indexOf(f.kind) < 0 ? [f.kind] : []).map(function (x) {
          return '<option' + (x === f.kind ? ' selected' : '') + '>' + esc(x) + '</option>';
        }).join('');
      }
      l.innerHTML = r.items.length ? '<div class="tbl-wrap"><table class="tbl"><thead><tr><th>Время</th><th>Уровень</th><th>Тип</th><th>Ученик</th><th>Событие</th></tr></thead><tbody>' +
        r.items.map(function (e) {
          return '<tr><td class="nowrap faint">' + fmt(e.ts) + '</td><td>' + badge(e.level === 'info' ? 'closed' : e.level, LEVEL[e.level] || e.level) + '</td><td class="mono">' + esc(e.kind) + '</td>' +
            '<td>' + (e.user_id ? '<a href="#/users/' + e.user_id + '">' + e.user_id + '</a>' : '<span class="faint">—</span>') + '</td><td>' + esc(e.message) + '</td></tr>';
        }).join('') + '</tbody></table></div>' : '<div class="card empty"><span class="em">🗒</span>Событий нет</div>';
    }).catch(function (e) { var l = $('#e-list'); if (l) l.innerHTML = errBox(e); });
  }

  // ───────────── действия ─────────────
  function busy(t, on) { if (t && t.tagName === 'BUTTON') t.disabled = on; }
  var ACT = {
    reload: function () { S.cur = null; route(); },
    side: function () { $('#side').classList.toggle('open'); },
    theme: function () {
      var dark = !D.documentElement.classList.contains('dark');
      D.documentElement.classList.toggle('dark', dark);
      lsSet('ss:adm:theme', dark ? 'dark' : 'light');
    },
    logout: function () {
      api('POST', 'logout').then(function () { S.csrf = null; renderLogin('Ты вышел. До встречи 👋'); }).catch(function (e) { toast(e.message, true); });
    },
    'lg-req': function (t) {
      var id = ($('#lg-id').value || '').replace(/\D/g, '');
      if (!id) { setMsg('Введи числовой Telegram ID', true); return; }
      lsSet('ss:adm:id', id);
      busy(t, true);
      api('POST', 'login/request', { admin_id: id }).then(function () {
        $('#lg-1').classList.add('hidden');
        $('#lg-2').classList.remove('hidden');
        $('#lg-code').focus();
        setMsg('Если этот ID есть в списке админов, бот уже прислал код. Он живёт 5 минут.');
      }).catch(function (e) { setMsg(e.message, true); }).then(function () { busy(t, false); });
    },
    'lg-ok': function () {
      var id = ($('#lg-id').value || '').replace(/\D/g, ''), code = ($('#lg-code').value || '').replace(/\D/g, '');
      if (code.length !== 6) { setMsg('Код — 6 цифр', true); return; }
      verify(id, code);
    },
    'lg-back': function () { $('#lg-2').classList.add('hidden'); $('#lg-1').classList.remove('hidden'); $('#lg-id').focus(); setMsg(''); },
    'm-ok': function () {
      var i = $('#m-in');
      if (i && !S.mmust && !i.value.trim()) { i.focus(); return; }
      closeModal(i ? i.value : true);
    },
    'm-no': function () { closeModal(false); },
    'modal-close': function () { closeModal(false); },
    sync: function (t) {
      var v = t.getAttribute('data-v');
      var go = function () {
        busy(t, true);
        api('POST', 'sync', { action: v }).then(function (r) {
          toast(v === 'flush' ? 'Записано строк: ' + r.rows : v === 'pause' ? 'Опрос на паузе' : 'Опрос возобновлён ✅');
          if (S.cur === 'overview') vOverview($('#view'), null, true);
        }).catch(function (e) { toast(e.message, true); busy(t, false); });
      };
      if (v === 'pause') {
        ask({ title: 'Поставить опрос на паузу?', text: 'Бот перестанет ходить на сайт школы для всех учеников, пока не нажмёшь «Возобновить». Уведомления о новом не будут приходить.', ok: 'На паузу', danger: true })
          .then(function (ok) { if (ok) go(); });
      } else go();
    },
    'maint-off': function (t) {
      busy(t, true);
      api('POST', 'runtime', { maintenance: false }).then(function () { toast('Техработы выключены'); vOverview($('#view'), null, true); })
        .catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'bulk-bad': function (t) {
      ask({ title: 'Вернуть в опрос?', text: 'Всех с «пароль не подходит» снова начнём опрашивать (по очереди, не разом). Если пароль и правда сменили — они вернутся в этот статус.', ok: 'Вернуть' })
        .then(function (ok) {
          if (!ok) return;
          busy(t, true);
          api('POST', 'users/bulk', { action: 'reactivate_bad_password' }).then(function (r) {
            toast('Вернул в опрос: ' + r.count);
            busy(t, false);
            if (S.cur === 'users') loadUsers(); else if (S.cur === 'overview') vOverview($('#view'), null, true);
          }).catch(function (e) { toast(e.message, true); busy(t, false); });
        });
    },
    cls: function (t) {
      S.uf = { q: '', status: '', cls: t.getAttribute('data-v'), sort: S.uf.sort };
      S.cur = null;
      if (location.hash === '#/users') route(); else location.hash = '#/users';
    },
    user: function (t) { location.hash = '#/users/' + t.getAttribute('data-id'); },
    'drawer-close': function () { if (S.cur === 'users') location.hash = '#/users'; else closeDrawer(); },
    ua: function (t) {
      var act = t.getAttribute('data-act'), u = S.user;
      if (!u) return;
      var p;
      if (act === 'message') {
        p = ask({ title: 'Сообщение ученику', text: 'Придёт от бота с пометкой «Сообщение от администратора».', input: 'area', ok: 'Отправить' })
          .then(function (v) { return v ? { action: 'message', text: String(v).trim() } : null; });
      } else if (act === 'delete') {
        p = ask({ title: 'Удалить ученика?', text: 'Удалятся все данные и сохранённый вход в NetSchool. Это необратимо. Напиши «удалить», чтобы подтвердить.', input: 'text', must: 'удалить', ok: 'Удалить навсегда', danger: true })
          .then(function (v) { return v ? { action: 'delete' } : null; });
      } else if (act === 'block') {
        p = ask({ title: 'Заблокировать?', text: 'Бот перестанет отвечать этому человеку, опрос остановится. Разблокировать можно в любой момент.', ok: 'Заблокировать', danger: true })
          .then(function (v) { return v ? { action: act } : null; });
      } else if (act === 'reset_creds') {
        p = ask({ title: 'Сбросить вход в NetSchool?', text: 'Сохранённый пароль удалится, ученику придёт просьба подключиться заново (/login).', ok: 'Сбросить', danger: true })
          .then(function (v) { return v ? { action: act } : null; });
      } else p = Promise.resolve({ action: act });
      p.then(function (body) {
        if (!body) return;
        busy(t, true);
        return api('POST', 'users/' + u.id + '/action', body).then(function (r) {
          toast(r.deleted ? 'Ученик удалён' : act === 'sync' ? 'Опрос поставлен в очередь' : act === 'message' ? 'Отправлено ✉️' : 'Готово');
          if (r.deleted) { closeDrawer(); location.hash = '#/users'; loadUsers(); return; }
          loadUsers();
          openUser(u.id);
        });
      }).catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'tpl-key': function (t) { S.tplKey = t.getAttribute('data-k'); renderTemplates($('#view')); },
    ph: function (t) {
      var ta = S.lastTa && D.body.contains(S.lastTa) ? S.lastTa : $('#tpl-text');
      if (!ta) return;
      var p = t.getAttribute('data-p'), s = ta.selectionStart || ta.value.length;
      ta.value = ta.value.slice(0, s) + p + ta.value.slice(ta.selectionEnd || s);
      ta.focus();
      ta.selectionStart = ta.selectionEnd = s + p.length;
    },
    'tpl-prev': function () { previewInto($('#tpl-pv'), $('#tpl-text').value); },
    'tpl-add': function (t) {
      var text = $('#tpl-text').value.trim(), sj = $('#tpl-subj');
      if (!text) { $('#tpl-text').focus(); return; }
      busy(t, true);
      api('POST', 'templates', { key: S.tplKey, text: text, subject: sj ? sj.value.trim() : '', enabled: true }).then(function (r) {
        S.tpl.items.push(r);
        toast('Фраза добавлена');
        renderTemplates($('#view'));
      }).catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'tpl-edit': function (t) {
      var it = tplById(t.getAttribute('data-id')), box = t.closest('.tpl');
      if (!it || !box) return;
      var subj = /^(mark|debt|att|term)\./.test(it.key);
      box.innerHTML = '<textarea class="inp" rows="3" maxlength="600">' + esc(it.text) + '</textarea>' +
        '<div class="row">' + (subj ? '<input class="inp" maxlength="64" data-f="subject" placeholder="Только для предмета (необязательно)" value="' + esc(it.subject) + '" style="flex:1 1 220px">' : '<span class="grow"></span>') +
        '<button class="btn sm" data-a="tpl-cancel">Отмена</button><button class="btn sm" data-a="tpl-eprev">Превью</button><button class="btn sm pri" data-a="tpl-esave" data-id="' + it.id + '">Сохранить</button></div><div class="pv"></div>';
      $('textarea', box).focus();
    },
    'tpl-cancel': function () { renderTemplates($('#view')); },
    'tpl-eprev': function (t) { var box = t.closest('.tpl'); previewInto($('.pv', box), $('textarea', box).value); },
    'tpl-esave': function (t) {
      var it = tplById(t.getAttribute('data-id')), box = t.closest('.tpl'), sj = $('[data-f=subject]', box);
      busy(t, true);
      tplSave(it, { text: $('textarea', box).value.trim(), subject: sj ? sj.value.trim() : it.subject }).then(function () {
        toast('Сохранено');
        renderTemplates($('#view'));
      }).catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'tpl-del': function (t) {
      var id = t.getAttribute('data-id');
      ask({ title: 'Удалить фразу?', text: 'Можно вместо этого просто выключить её переключателем.', ok: 'Удалить', danger: true }).then(function (ok) {
        if (!ok) return;
        api('DELETE', 'templates/' + id).then(function () {
          S.tpl.items = S.tpl.items.filter(function (x) { return String(x.id) !== String(id); });
          toast('Удалено');
          renderTemplates($('#view'));
        }).catch(function (e) { toast(e.message, true); });
      });
    },
    'b-send': function (t) {
      var text = $('#b-text').value.trim(), target = $('#b-target').value;
      if (!text) { $('#b-text').focus(); toast('Сначала текст', true); return; }
      var n = S.bcount == null ? '?' : S.bcount;
      ask({ title: 'Отправить рассылку?', text: 'Получат: ' + n + ' ' + plural(+n || 0, 'человек', 'человека', 'человек') + '. Отменить после старта нельзя.', ok: 'Отправить' }).then(function (ok) {
        if (!ok) return;
        busy(t, true);
        api('POST', 'broadcast', { title: $('#b-title').value.trim(), text: text, target: target, html: $('#b-html').checked }).then(function (r) {
          toast('Рассылка #' + r.id + ' пошла: ' + r.count + ' получателей');
          $('#b-text').value = '';
          $('#b-title').value = '';
          bPreview();
          busy(t, false);
          loadHist();
        }).catch(function (e) { toast(e.message, true); busy(t, false); });
      });
    },
    'fb-reply': function (t) {
      var id = t.getAttribute('data-id'), ta = $('#fb-r-' + id);
      if (!ta || !ta.value.trim()) { if (ta) ta.focus(); return; }
      busy(t, true);
      api('POST', 'feedback/' + id + '/reply', { text: ta.value.trim() }).then(function () { toast('Ответ отправлен ✉️'); loadFb(); })
        .catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'fb-close': function (t) {
      busy(t, true);
      api('POST', 'feedback/' + t.getAttribute('data-id') + '/close').then(function () { toast('Закрыто'); loadFb(); })
        .catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    seg: function (t) {
      var n = t.getAttribute('data-n'), v = t.getAttribute('data-v');
      if (n === 'fb') { S.fb = v; vFeedback($('#view')); }
      else if (n === 'evsrc') { S.ev.source = v; vEvents($('#view')); }
      else if (n === 'reg') {
        api('POST', 'runtime', { registration: v }).then(function () { toast('Регистрация: ' + t.textContent.toLowerCase()); vInvites($('#view')); })
          .catch(function (e) { toast(e.message, true); });
      }
    },
    'rt-reset': function () { renderRuntime($('#view')); },
    'rt-save': function (t) {
      var patch = rtCollect();
      if (!Object.keys(patch).length) return;
      busy(t, true);
      api('POST', 'runtime', patch).then(function (r) {
        S.rt.values = r.values;
        toast('Сохранено: ' + Object.keys(patch).length);
        renderRuntime($('#view'));
      }).catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'inv-add': function (t) {
      busy(t, true);
      api('POST', 'invites', { note: $('#i-note').value.trim(), max_uses: +$('#i-uses').value || 30, days: +$('#i-days').value || 0, code: $('#i-code').value.trim() })
        .then(function (r) {
          toast('Приглашение создано');
          vInvites($('#view'));
          if (r.link) copy(r.link);
        }).catch(function (e) { toast(e.message, true); busy(t, false); });
    },
    'inv-toggle': function (t) {
      api('POST', 'invites/' + encodeURIComponent(t.getAttribute('data-v')) + '/toggle').then(function () { vInvites($('#view')); })
        .catch(function (e) { toast(e.message, true); });
    },
    'inv-del': function (t) {
      var code = t.getAttribute('data-v');
      ask({ title: 'Удалить приглашение ' + code + '?', text: 'Ссылка перестанет работать. Уже подключённых это не затронет.', ok: 'Удалить', danger: true }).then(function (ok) {
        if (!ok) return;
        api('DELETE', 'invites/' + encodeURIComponent(code)).then(function () { toast('Удалено'); vInvites($('#view')); })
          .catch(function (e) { toast(e.message, true); });
      });
    },
    copy: function (t) { copy(t.getAttribute('data-v')); }
  };
  function copy(text) {
    var done = function () { toast('Ссылка скопирована 📋'); };
    if (navigator.clipboard && W.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, function () { prompt('Скопируй ссылку:', text); });
    } else {
      W.prompt('Скопируй ссылку:', text);
    }
  }

  // ───────────── события DOM ─────────────
  D.addEventListener('click', function (e) {
    var t = e.target.closest ? e.target.closest('[data-a]') : null;
    if (!t) return;
    var a = t.getAttribute('data-a');
    if (!ACT[a]) return;
    if (t.tagName === 'A') e.preventDefault();
    ACT[a](t, e);
  });
  D.addEventListener('change', function (e) {
    var t = e.target;
    if (t.getAttribute('data-c') === 'tpl-on') {
      var it = tplById(t.getAttribute('data-id'));
      if (!it) return;
      tplSave(it, { enabled: t.checked }).then(function (r) {
        var box = t.closest('.tpl');
        if (box) box.classList.toggle('off', !r.enabled);
        toast(r.enabled ? 'Фраза включена' : 'Фраза выключена');
      }).catch(function (er) { t.checked = !t.checked; toast(er.message, true); });
      return;
    }
    if (t.id === 'u-status' || t.id === 'u-cls' || t.id === 'u-sort') {
      S.uf[t.id.slice(2)] = t.value;
      loadUsers();
    } else if (t.id === 'e-level' || t.id === 'e-kind') {
      S.ev[t.id === 'e-level' ? 'level' : 'kind'] = t.value;
      loadEvents();
    } else if (t.id === 'e-user') {
      S.ev.user_id = t.value.replace(/\D/g, '');
      loadEvents();
    } else if (t.id === 'b-target') {
      bCount();
    } else if (t.id === 'b-html') {
      bPreview();
    }
    if (S.cur === 'runtime' && (t.hasAttribute('data-k') || t.hasAttribute('data-bell'))) rtCollect();
  });
  var qTimer = null;
  D.addEventListener('input', function (e) {
    var t = e.target;
    if (t.id === 'u-q') {
      clearTimeout(qTimer);
      qTimer = setTimeout(function () { S.uf.q = t.value.trim(); loadUsers(); }, 300);
    } else if (t.id === 'b-text' || t.id === 'b-title') {
      bPreview();
    } else if (t.id === 'm-in' && S.mmust) {
      $('#m-ok').disabled = t.value.trim().toLowerCase() !== S.mmust;
    } else if (t.id === 'lg-code' && t.value.replace(/\D/g, '').length === 6) {
      ACT['lg-ok']();
    } else if (S.cur === 'runtime' && (t.hasAttribute('data-k') || t.hasAttribute('data-bell'))) {
      rtCollect();
    }
  });
  D.addEventListener('focusin', function (e) {
    if (e.target.tagName === 'TEXTAREA' && S.cur === 'templates') S.lastTa = e.target;
  });
  D.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      if (S.mres) closeModal(false);
      else if ($('#drawer')) ACT['drawer-close']();
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      var id = e.target.id;
      if (id === 'lg-id') ACT['lg-req']($('[data-a=lg-req]'));
      else if (id === 'lg-code') ACT['lg-ok']();
      else if (id === 'm-in' && e.target.tagName === 'INPUT') ACT['m-ok']();
    }
  });
  W.addEventListener('hashchange', route);
  D.addEventListener('visibilitychange', function () {
    if (!D.hidden && S.cur === 'overview') vOverview($('#view'), null, true);
  });

  // ───────────── старт ─────────────
  function boot() {
    var th = lsGet('ss:adm:theme');
    var dark = th ? th === 'dark' : !!(W.matchMedia && W.matchMedia('(prefers-color-scheme: dark)').matches);
    D.documentElement.classList.toggle('dark', dark);
    var h = location.hash;
    if (/^#id=/.test(h)) {
      // ссылка из бота: #id=…&code=… — входим и сразу убираем код из адресной строки
      var p = {};
      h.slice(1).split('&').forEach(function (kv) {
        var i = kv.indexOf('=');
        if (i > 0) p[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1));
      });
      history.replaceState(null, '', location.pathname + '#/overview');
      renderLogin('Вхожу…');
      if (p.id && p.code) {
        $('#lg-id').value = p.id;
        $('#lg-1').classList.add('hidden');
        $('#lg-2').classList.remove('hidden');
        verify(p.id, p.code);
      }
      return;
    }
    api('GET', 'session').then(function (r) {
      S.csrf = r.csrf;
      S.admin = r.admin_id;
      if (r.now) S.skew = Date.parse(r.now) - Date.now();
      start();
    }).catch(function () { renderLogin(); });
  }
  boot();
})();
