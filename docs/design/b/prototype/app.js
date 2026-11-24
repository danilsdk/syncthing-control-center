'use strict';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
const esc = (value) =>
  String(value ?? '').replace(
    /[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c],
  );
const attr = esc;
const PATHS = {
  overview: '/overview',
  folders: '/folders',
  rules: '/rules',
  history: '/history/commands',
  analytics: '/analytics',
};
const sectionNames = {
  overview: 'Обзор',
  folders: 'Папки',
  rules: 'Правила',
  history: 'История',
  analytics: 'Аналитика',
  login: 'Вход',
};
const paths = {
  home: 'M3 10.5 12 3l9 7.5M5 9.5V21h5v-7h4v7h5V9.5',
  history: 'M3 5v5h5 M4 9a8.5 8.5 0 1 1-.4 6 M12 7v5l3.5 2',
  chart: 'M4 4v16h17 M8 15l4-5 4 2 4-7',
  search: 'M16 16l5 5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  tune: 'M3 7h6m5 0h7 M3 17h10m5 0h3 M9 4h5v6H9z M13 14h5v6h-5z',
  pin: 'm8 3 9 0-1 6 3 4v2H5v-2l3-4z M12 15v6',
  move: 'M2 12h20 M6 8l-4 4 4 4 M18 8l4 4-4 4',
  docs: 'M8 3h8l4 4v13H8z M15 3v5h5 M4 7v15h12 M11 12h6 M11 16h4',
  archive: 'M3 4h18v5H3z M5 9v11h14V9 M9 13h6',
  photo: 'M3 4h18v16H3z M3 16l6-6 5 5 3-3 4 4 M17 8h.01',
  music:
    'M9 17V6l11-3v12 M9 9l11-3 M9 17a3 2 0 1 1-6 0 3 2 0 0 1 6 0 M20 15a3 2 0 1 1-6 0 3 2 0 0 1 6 0',
  code: 'm7 7-5 5 5 5 M17 7l5 5-5 5 M14 4l-4 16',
  briefcase: 'M3 7h18v13H3z M8 7V4h8v3 M3 12q9 5 18 0 M12 12v3',
  book: 'M12 6Q7 3 2 5v14q5-2 10 1 5-3 10-1V5q-5-2-10 1Zm0 0v14',
  folder: 'M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z',
  server: 'M3 4h18v6H3z M3 14h18v6H3z M6 7h.1 M6 17h.1',
  calendar: 'M4 5h16v16H4z M4 10h16 M8 3v4 M16 3v4 M8 14h2 M14 14h2 M8 17h2',
  clock: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M12 7v5l3 2',
  arrow: 'M5 12h14m-5-5 5 5-5 5',
  diagonal: 'M6 18 18 6M6 6h12v12',
  back: 'M19 12H5m5-5-5 5 5 5',
  down: 'm6 9 6 6 6-6',
  add: 'M12 5v14M5 12h14',
  edit: 'm4 20 5-1L20 8l-4-4L5 15Z M13 7l4 4',
  pause: 'M8 5v14M16 5v14',
  play: 'm7 4 13 8-13 8z',
  pulse: 'M2 12h5l3-8 4 16 3-8h5',
  check: 'm4 12 5 5L20 6',
  event: 'M5 3h10l4 4v6 M5 3v18h6 M14 3v5h5 M13 17l3 3 5-6',
  close: 'm6 6 12 12M18 6 6 18',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  reset: 'M4 10a8 8 0 1 1 1 8 M4 4v6h6',
  list: 'M4 6h16M4 12h16M4 18h16',
  info: 'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z M12 11v6 M12 7h.01',
};
function icon(name) {
  return `<svg class="ic" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${paths[name]}"/></svg>`;
}
const link = (path, body, cls = 'text-link', extra = '') =>
  `<a class="${cls}" href="#${attr(path)}" ${extra}>${body}</a>`;
const action = (name, body, cls = 'btn', extra = '') =>
  `<button type="button" class="${cls}" data-act="${name}" ${extra}>${body}</button>`;
const opt = (v, l, current) =>
  `<option value="${attr(v)}"${String(current) === String(v) ? ' selected' : ''}>${esc(l)}</option>`;
const note = (s) => `<p class="hint">${s}</p>`;
const badge = (status, label) =>
  `<span class="badge kind-${status}"><i aria-hidden="true">${MARK[status] ?? ''}</i>${esc(label ?? LABEL[status])}</span>`;
const address = (f) => `${nodeBy(f.node).name} / ${f.key}`;
const folderPath = (f) => '/folders/' + f.id;
const rulePath = (r) => '/rules/' + r.id;
const commandPath = (a) => '/history/commands/' + a.id;
const scopedHistory = (f) =>
  '/history/commands?' + new URLSearchParams({ node: f.node, folder: f.id });
const localName = (f) => `<span class="mono">${esc(address(f))}</span>`;
const facts = (rows) =>
  `<dl class="facts">${rows.map(([a, b]) => `<div><dt>${a}</dt><dd>${b}</dd></div>`).join('')}</dl>`;
const block = (title, body, cls = '') =>
  `<section class="panel ${cls}"><h2>${title}</h2>${body}</section>`;
const empty = (title, body, button = '') =>
  `<div class="empty"><span class="empty-icon">${icon('list')}</span><h2>${esc(title)}</h2><p>${esc(body)}</p>${button}</div>`;
function current() {
  const raw = location.hash.slice(1) || '/overview',
    i = raw.indexOf('?');
  const path = i < 0 ? raw : raw.slice(0, i),
    q = new URLSearchParams(i < 0 ? '' : raw.slice(i + 1)),
    parts = path.split('/').filter(Boolean);
  return { raw, path, q, parts, sec: parts[0] };
}
function queryURL(path, params) {
  const q = new URLSearchParams(params);
  for (const k of [...q.keys()]) if (!q.get(k)) q.delete(k);
  return path + (q.size ? '?' + q : '');
}
function replaceQuery(changes) {
  const r = current();
  for (const [k, v] of Object.entries(changes)) v ? r.q.set(k, v) : r.q.delete(k);
  history.replaceState(history.state, '', '#' + queryURL(r.path, r.q));
}
const main = $('#content');
const pos = () => ({
  url: current().raw,
  y: window.scrollY,
  key:
    document.activeElement?.getAttribute('data-key') ||
    document.activeElement?.getAttribute('href'),
  index: history.state?.index ?? 0,
});
function savePos() {
  history.replaceState({ ...history.state, position: pos() }, '');
}
function go(url, { nav = false, replace = false, restore = null } = {}) {
  if (url === current().raw && !replace) return;
  const before = current();
  const fromMark =
    before.path === '/folders' && /^\/folders\/[^/?]+$/.test(url)
      ? document.activeElement
          ?.closest('.folder-entry')
          ?.querySelector('.collection-mark')
          ?.getBoundingClientRect()
      : null;
  savePos();
  let origin = nav ? null : history.state?.origin;
  if (!nav && before.path === '/folders' && url.startsWith('/folders/')) origin = pos();
  const isTabSwitch =
    before.parts[0] === 'history' &&
    before.parts.length === 2 &&
    /^\/history\/(commands|events)(?:\?|$)/.test(url);
  const oldTab = $('.tab[aria-current]')?.getBoundingClientRect();
  const state = {
    index: (history.state?.index ?? 0) + (replace ? 0 : 1),
    origin,
    previous: before.raw,
    position: restore,
  };
  history[replace ? 'replaceState' : 'pushState'](state, '', '#' + url);
  render(!isTabSwitch, restore);
  if (fromMark) moveFolderMark(fromMark);
  if (isTabSwitch) {
    const tab = $('.tab[aria-current]');
    tab?.focus({ preventScroll: true });
    if (tab && oldTab && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const nextTab = tab.getBoundingClientRect(),
        delta = oldTab.left - nextTab.left;
      $('.tab-ink').animate(
        [
          { transform: `translateX(${delta}px) scaleX(${oldTab.width / nextTab.width})` },
          { transform: 'none' },
        ],
        { duration: 160, easing: 'cubic-bezier(.2,.75,.25,1)' },
      );
      $('[data-audit]')?.classList.add('updated');
    }
  }
}
let markFlight;
function moveFolderMark(from) {
  const target = $('.dossier-identity>.collection-mark');
  if (
    !target ||
    matchMedia('(prefers-reduced-motion:reduce)').matches ||
    from.bottom <= 0 ||
    from.top >= innerHeight
  )
    return;
  const to = target.getBoundingClientRect();
  if (to.bottom <= 0 || to.top >= innerHeight) return;
  markFlight?.cancel();
  const layer = document.createElement('div');
  layer.className = 'objects mark-flight';
  layer.setAttribute('aria-hidden', 'true');
  const clone = target.cloneNode(true);
  layer.append(clone);
  document.body.append(layer);
  Object.assign(clone.style, {
    left: to.left + 'px',
    top: to.top + 'px',
    width: to.width + 'px',
    height: to.height + 'px',
    borderRadius: getComputedStyle(target).borderRadius,
    fontSize: getComputedStyle(target).fontSize,
  });
  target.style.visibility = 'hidden';
  const flight = clone.animate(
    [
      {
        transform: `translate(${from.left - to.left}px,${from.top - to.top}px) scale(${from.width / to.width})`,
      },
      { transform: 'none' },
    ],
    { duration: 260, easing: 'cubic-bezier(.2,.78,.25,1)' },
  );
  markFlight = flight;
  function done() {
    target.style.removeProperty('visibility');
    layer.remove();
    if (markFlight === flight) markFlight = null;
  }
  flight.onfinish = done;
  flight.oncancel = done;
}
function refreshControl() {
  const { sec, path } = current();
  return ['folders', 'rules', 'history', 'analytics'].includes(sec) &&
    !path.endsWith('/edit') &&
    !path.endsWith('/new')
    ? action(
        'refresh',
        icon('reset'),
        'iconbtn',
        'aria-label="Обновить данные" data-tip="Перечитать снимок"',
      )
    : '';
}
function metadata() {
  return `<div class="right-meta">${refreshControl()}<button type="button" class="demo" data-act="demo" aria-expanded="false">Демоданные</button><span class="date-a">22 ноября 2026</span></div>`;
}
function title(text) {
  return `<div class="title-a"><h1 tabindex="-1">${esc(text)}</h1>${metadata()}</div>`;
}
function breadcrumbs(items = [], includeReturn = true) {
  const origin = history.state?.origin;
  return `<div class="crumbs"><nav aria-label="Путь">${items.map(([txt, url]) => (url ? link(url === '/folders' && origin ? origin.url : url, esc(txt), 'crumb', url === '/folders' && origin ? 'data-return' : '') : `<span>${esc(txt)}</span>`)).join('<b aria-hidden="true">/</b>')}</nav>${includeReturn && origin ? link(origin.url, icon('back') + 'К списку папок', 'return-link', 'data-return') : ''}</div>`;
}
function folderStatus(f) {
  return isFresh(f)
    ? badge('applied', f.paused ? 'Приостановлена' : 'Синхронизация разрешена')
    : badge('unknown', f.observed ? 'Данные устарели' : 'Нет свежих данных');
}
const nodeStatus = (n) =>
  badge(n.available ? 'online' : 'failed', n.available ? 'Доступен' : 'Недоступен');
function demoPanel() {
  const r = current();
  return `<aside class="popover demo-panel" aria-label="Демоданные"><strong>Демоданные</strong><label>Набор объектов<select id="fixture">${opt('base', 'Контрольный снимок', db.extended ? 'extended' : 'base')}${opt('extended', 'Два узла и свежая пауза', db.extended ? 'extended' : 'base')}</select></label><p class="hint">Смена набора восстанавливает исходные записи.</p><label>Состояние экрана<select id="view-mode">${[
    ['', 'Обычное'],
    ['loading', 'Загрузка'],
    ['empty', 'Нет записей'],
    ['error', 'Ошибка чтения'],
    ['stale', 'Устаревший снимок'],
  ]
    .map(([k, v]) => opt(k, v, r.q.get('view') || ''))
    .join(
      '',
    )}</select></label>${action('reset-data', icon('reset') + 'Восстановить снимок', 'text-link')}</aside>`;
}
function overview() {
  const box = document.createElement('div');
  box.innerHTML = $('#approved').innerHTML;
  const f = featuredFolder(),
    r = activeFor(f.id);
  $('.folder-bottom a', box).href = '#' + folderPath(f);
  const n = nodeBy(f.node);
  $('.folder-well', box).setAttribute('aria-label', folderName(f));
  const headline = $('.folder-body h2', box);
  if (folderName(f) !== 'Рабочие документы') headline.textContent = folderName(f);
  $('.folder-head .vcenter', box).innerHTML =
    folderIcon(f) + (db.folders.length === 1 ? 'Единственная папка' : 'Закреплено на обзоре');
  $('.folder-head .mono', box).textContent = address(f);
  $('.folder-state', box).innerHTML = folderStatus(f);
  const fields = $$('.folder-bottom dd', box);
  fields[0].textContent =
    f.paused == null
      ? 'Неизвестна'
      : (f.paused ? 'Включена' : 'Выключена') + (isFresh(f) ? '' : ' · устарело');
  fields[1].textContent = f.runtime === 'idle' ? 'Ожидание' : f.runtime || 'Неизвестно';
  $('.node-a h2', box).textContent = n.label;
  $('.node-foot .mono', box).textContent = n.name;
  $('.node-status', box).innerHTML = nodeStatus(n);
  $('.folder-bottom a', box).innerHTML = 'Состояние папки ' + icon('diagonal');
  const latest = db.attempts[0];
  const ev = db.events[0];
  const a = $('.schedule-a a', box);
  a.href = '#' + (r ? rulePath(r) : queryURL('/rules/new', { folder: f.id }));
  if (r) {
    $('.schedule-time', box).innerHTML = esc(r.start) + '<span>—</span>' + esc(r.end);
    $('.schedule-zone', box).innerHTML =
      esc(dayText(r.days)) + ' · <span class="mono">' + esc(r.zone) + '</span>';
    $('.rule-now strong', box).innerHTML = icon(currentPause(r) ? 'pause' : 'play') + desired(r);
  } else {
    $('.schedule-a', box).innerHTML =
      '<div class="card-hat">Расписание</div><h2 class="schedule-time">Без правила</h2>' +
      link(
        queryURL('/rules/new', { folder: f.id }),
        'Создать правило ' + icon('arrow'),
        'text-link',
      );
  }
  const row = $('[data-kind="command"]', box),
    who = folderBy(latest.folder);
  $('a', row).href = '#' + commandPath(latest);
  const cells = $$('td', row);
  cells[0].textContent = stamp(latest.created).slice(11, 16);
  $('span', cells[1]).innerHTML =
    icon(latest.paused ? 'pause' : 'play') +
    (latest.paused ? 'Приостановить' : 'Возобновить') +
    (latest.rule ? ' по расписанию' : ' вручную');
  cells[2].textContent = folderName(who);
  cells[3].innerHTML = badge(latest.status) + `<small>${verification(latest)}</small>`;
  $('[data-kind="event"] a', box).href = '#/history/events/' + ev.id;
  $('[data-kind="event"]', box).cells[2].textContent = folderName(folderBy(ev.folder));
  if (db.folders.length > 1) {
    $('.log-head h2', box).insertAdjacentHTML('beforeend', ' <small>Все папки</small>');
    for (const [row, object] of [
      [$('[data-kind="event"]', box), folderBy(ev.folder)],
      [$('[data-kind="command"]', box), who],
    ]) {
      row.cells[2].insertAdjacentHTML('beforeend', '<small>' + esc(address(object)) + '</small>');
    }
  }
  if (latest.created > ev.at) $('tbody', box).prepend(row);
  const filter = current().q.get('type') || 'all';
  $$('[data-filter]', box).forEach((b) =>
    b.setAttribute('aria-pressed', String(b.dataset.filter === filter)),
  );
  $$('[data-kind]', box).forEach(
    (el) => (el.hidden = filter !== 'all' && el.dataset.kind !== filter),
  );
  return title('Обзор') + box.innerHTML;
}
function nodeOptions(val) {
  return opt('', 'Все узлы', val) + db.nodes.map((n) => opt(n.id, n.name, val)).join('');
}
const glyphs = {
  folder: 'Папка',
  docs: 'Документы',
  archive: 'Архив',
  photo: 'Фотографии',
  music: 'Музыка',
  code: 'Код',
  briefcase: 'Проекты',
  book: 'Библиотека',
};
const prefKey = 'scc-folder-appearance-v1';
function readPreferences() {
  try {
    return JSON.parse(localStorage.getItem(prefKey)) || {};
  } catch {
    return {};
  }
}
let preferences = readPreferences();
function storePreferences() {
  try {
    localStorage.setItem(prefKey, JSON.stringify(preferences));
  } catch {}
}
const appearance = (f) => preferences.folders?.[f?.id] || {};
const folderName = (f) => (f ? appearance(f).label || f.name : '');
const featuredFolder = () => folderBy(preferences.featured) || db.folders[0];
function folderIcon(f, overrides = appearance(f)) {
  const key = Object.hasOwn(glyphs, overrides.glyph) ? overrides.glyph : 'folder';
  return `<span class="collection-mark mark-${key}" aria-hidden="true">${overrides.symbol ? `<span class="custom-symbol">${esc(overrides.symbol)}</span>` : icon(key)}</span>`;
}
function inventory(q) {
  const node = q.get('node') || '',
    state = q.get('state') || '',
    term = (q.get('search') || '').trim().toLocaleLowerCase('ru');
  const folders = db.folders.filter(
    (f) =>
      (!node || f.node === node) &&
      (!state || (state === 'fresh') === isFresh(f)) &&
      (!term ||
        [folderName(f), f.name, address(f), nodeBy(f.node).label].some((x) =>
          x.toLocaleLowerCase('ru').includes(term),
        )),
  );
  const groups = db.nodes
    .map((n) => {
      const items = folders.filter((f) => f.node === n.id);
      if (!items.length) return '';
      return `<section class="catalog-group"><header><div class="identity"><span class="node-symbol">${icon('server')}</span><div><h2>${esc(n.name)}</h2><span class="hint">${esc(n.label)}</span></div></div>${nodeStatus(n)}</header><div class="catalog-head"><span>Папка</span><span>Наблюдение</span><span>По расписанию</span><span></span></div>${items
        .map((f) => {
          const isFeatured = f.id === featuredFolder().id;
          return `<div class="catalog-row">${link(folderPath(f), `${folderIcon(f)}<span class="catalog-name"><strong>${esc(folderName(f))}</strong><small>${esc(f.key)}${isFeatured ? ' · На обзоре' : ''}</small></span>`, 'folder-entry', `data-key="row-${f.id}"`)}<span data-fresh="${f.id}">${folderStatus(f)}</span><span class="catalog-desired">${esc(desired(activeFor(f.id)))}</span>${link(folderPath(f) + '/appearance', icon('tune'), 'iconbtn', `data-key="appearance-${f.id}" aria-label="Настроить оформление: ${attr(folderName(f))}" data-tip="Имя и значок"`)}</div>`;
        })
        .join('')}</section>`;
    })
    .join('');
  return (
    groups ||
    empty(
      'Папок по этим условиям нет',
      'Попробуйте другое имя, ID или узел.',
      action('clear', 'Сбросить фильтры'),
    )
  );
}
function foldersPage(q) {
  const state = q.get('state') || '';
  return (
    title('Папки') +
    `<div class="catalog-tools"><label class="searchbox"><span class="only-reader">Найти папку</span>${icon('search')}<input type="search" id="folder-search" placeholder="Имя папки, ID или узел" value="${attr(q.get('search') || '')}" autocomplete="off"><kbd>/</kbd></label><label>Узел<select data-query="node">${nodeOptions(q.get('node') || '')}</select></label><label>Наблюдение<select data-query="state">${opt('', 'Все состояния', state)}${opt('stale', 'Без свежих данных', state)}${opt('fresh', 'Свежие данные', state)}</select></label></div><div id="inventory">${inventory(q)}</div>`
  );
}
function finishAppearance(f) {
  const prev = history.state?.previous;
  if (prev && (prev.split('?')[0] === '/folders' || prev === folderPath(f))) history.back();
  else go(folderPath(f), { replace: true });
}
function appearancePage(f) {
  const pref = appearance(f);
  return (
    breadcrumbs([['Папки', '/folders'], [folderName(f), folderPath(f)], ['Оформление']]) +
    title('Оформление папки') +
    `<div class="custom-layout"><form id="appearance-form" class="panel editor" data-id="${f.id}"><h2>Имя и значок</h2><p class="hint">Оформление сохраняется в этом браузере. Folder ID — <code>${esc(f.key)}</code>.</p><label>Название в панели<input name="label" required maxlength="64" value="${attr(folderName(f))}"></label><fieldset><legend>Значок</legend><div class="glyph-picker">${Object.entries(
      glyphs,
    )
      .map(
        ([key, label]) =>
          `<label data-tip="${label}"><input type="radio" name="glyph" value="${key}" aria-label="${label}" ${!pref.symbol && (pref.glyph || 'folder') === key ? 'checked' : ''}><span>${icon(key)}</span></label>`,
      )
      .join(
        '',
      )}</div></fieldset><label>Свой символ или эмодзи<input name="symbol" maxlength="32" value="${attr(pref.symbol || '')}" placeholder="Например, 📚" autocomplete="off"></label><p class="form-error" role="alert"></p><div class="actions"><button class="btn primary" type="submit">Сохранить оформление</button>${action('cancel-appearance', 'Отмена', 'quiet', `data-id="${f.id}"`)}</div></form><aside class="identity-preview"><span class="panel-label">В списке папок</span><div class="identity" id="folder-sample">${folderIcon(f)}<div><strong>${esc(folderName(f))}</strong><small>${localName(f)}</small></div></div></aside></div>`
  );
}
function readAppearance(form) {
  return {
    label: form.elements.label.value.trim(),
    glyph: form.elements.glyph.value || appearance(folderBy(form.dataset.id)).glyph || 'folder',
    symbol: form.elements.symbol.value.trim(),
  };
}
function updateSample(form) {
  const f = folderBy(form.dataset.id),
    pref = readAppearance(form);
  $('#folder-sample').innerHTML =
    `${folderIcon(f, pref)}<div><strong>${esc(pref.label || f.name)}</strong><small>${localName(f)}</small></div>`;
}
function saveAppearance(form) {
  const f = folderBy(form.dataset.id),
    pref = readAppearance(form);
  if (
    !pref.label ||
    [...new Intl.Segmenter('ru', { granularity: 'grapheme' }).segment(pref.symbol)].length > 1
  ) {
    $('.form-error', form).textContent = pref.label
      ? 'Введите один символ или эмодзи.'
      : 'Введите название папки.';
    return;
  }
  preferences.folders = { ...preferences.folders, [f.id]: pref };
  storePreferences();
  finishAppearance(f);
  toast('Оформление папки сохранено');
}
function folderPage(f) {
  const n = nodeBy(f.node),
    r = activeFor(f.id),
    other = db.rules.find((item) => item.folder === f.id),
    reason = resumeReason(f);
  const observation = !f.observed
    ? 'Нет наблюдения'
    : time(f.observed) + (isFresh(f) ? '' : ' · устарело');
  const isFeatured = featuredFolder().id === f.id;
  const ruleBox = `<div class="panel-label">${icon('calendar')}Расписание</div>${r ? `<div class="large-time">${r.start}<i>—</i>${r.end}</div><p class="mut">${dayText(r.days)} · ${esc(r.zone)}</p>${facts([['Сейчас по правилу', desired(r)]])}${link(rulePath(r), 'Открыть правило ' + icon('arrow'), 'btn primary')}` : `<h2>Без активного правила</h2>${link(other ? rulePath(other) : queryURL('/rules/new', { folder: f.id }), other ? 'Открыть выключенное правило' : 'Создать правило', 'btn')}`}`;
  const manage = reason
    ? `<button class="btn" type="button" aria-disabled="true" data-tip="${attr(reason)}">${icon('play')}Возобновить вручную</button>`
    : action('resume', icon('play') + 'Возобновить вручную', 'btn primary', `data-id="${f.id}"`);
  return (
    breadcrumbs([['Папки', '/folders'], [address(f)]]) +
    `<div class="object-meta">${metadata()}</div><section class="dossier"><div class="dossier-identity">${folderIcon(f)}<div><span class="mono">${esc(address(f))}</span><h1 tabindex="-1">${esc(folderName(f))}</h1></div></div><div class="dossier-actions">${link(folderPath(f) + '/appearance', icon('tune') + 'Имя и значок', 'btn')}${isFeatured ? `<span class="featured-tag">${icon('pin')}На обзоре</span>` : action('feature', icon('pin') + 'На обзор', 'btn', `data-id="${f.id}"`)}</div></section><div class="dossier-grid"><section class="panel observation-panel"><div class="panel-label">${icon('pulse')}Наблюдения</div><div class="reading-status" data-folder-status>${folderStatus(f)}</div>${facts(
      [
        ['Узел', `${esc(n.name)} · ${nodeStatus(n)}`],
        [
          'Фактическая пауза',
          f.paused == null
            ? 'Неизвестна'
            : (f.paused ? 'Включена' : 'Выключена') + (isFresh(f) ? '' : ' · устарело'),
        ],
        ['Синхронизация', f.runtime === 'idle' ? 'Ожидание' : esc(f.runtime || 'Неизвестно')],
        ['Наблюдение конфигурации', observation],
        [
          'Наблюдение синхронизации',
          time(f.runtimeAt) + (f.runtimeAt && db.clock - f.runtimeAt > 60000 ? ' · устарело' : ''),
        ],
      ],
    )}</section><aside class="panel schedule-detail">${ruleBox}</aside></div><section class="panel folder-commands"><div><h2>Управление</h2>${note(reason || 'Папка на паузе. Доступно ручное возобновление.')}</div><div class="action-pair">${manage}${link(scopedHistory(f), 'История папки ' + icon('arrow'), 'text-link')}</div><div data-manual="${f.id}">${manualResult(f)}</div></section>`
  );
}

function manualResult(f) {
  const a = db.attempts.find((a) => a.folder === f.id && !a.rule);
  return a
    ? `<div class="inline-result">${badge(a.status)} ${link(commandPath(a), 'Открыть попытку ' + icon('diagonal'))}</div>`
    : '';
}
const minuteValue = (t) => {
  if (!/^\d{2}:\d{2}$/.test(t || '')) return NaN;
  const [h, m] = t.split(':').map(Number);
  return h * 60 + m;
};
const formatMinute = (m) =>
  String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(m % 60).padStart(2, '0');
function weekRows(r) {
  const start = minuteValue(r.start),
    end = minuteValue(r.end),
    valid = Number.isFinite(start + end) && start !== end,
    overnight = end < start;
  function segment(a, b) {
    return b > a
      ? `<i class="window-fill" style="left:${a / 14.4}%;width:${(b - a) / 14.4}%"></i>`
      : '';
  }
  function handle(edge, at, owner) {
    const value = edge === 'start' ? start : end,
      label = edge === 'start' ? 'Начало окна' : 'Конец окна';
    return `<button type="button" class="window-grip" role="slider" data-edge="${edge}" data-owner="${owner}" tabindex="${owner === r.days[0] ? 0 : -1}" aria-label="${label}, ${DAYS[owner - 1]}" aria-valuemin="0" aria-valuemax="1439" aria-valuenow="${value}" aria-valuetext="${formatMinute(value)}" style="left:${at / 14.4}%"><span aria-hidden="true"></span></button>`;
  }
  return DAYS.map((day, i) => {
    const today = i + 1,
      prev = i || 7,
      starts = valid && r.days.includes(today),
      tail = valid && overnight && end > 0 && r.days.includes(prev);
    const finish = overnight ? 1440 : end;
    const blocks = (starts ? segment(start, finish) : '') + (tail ? segment(0, end) : '');
    const grips =
      (starts ? handle('start', start, today) : '') +
      (starts && (!overnight || end === 0) ? handle('end', finish, today) : '') +
      (tail ? handle('end', end, prev) : '');
    return `<div class="planner-row ${blocks ? 'has-window' : ''}"><span>${day}</span><div class="planner-track">${blocks}${grips}</div></div>`;
  }).join('');
}
function windowNote(r) {
  if (!r.days.length) return 'Выберите хотя бы один день начала.';
  if (!r.start || !r.end) return 'Укажите обе границы окна.';
  if (r.start === r.end) return 'Начало и конец должны различаться.';
  const d = r.days[0],
    cross = r.end < r.start;
  return `${dayText(r.days)} · ${r.start}–${r.end}${cross ? ' следующего дня' : ''}.${cross ? ` Например, ${DAYS[d - 1]} ${r.start} → ${DAYS[d % 7]} ${r.end}.` : ''}${r.enabled ? '' : ' Расписание выключено.'}`;
}
function board(r) {
  return `<div class="week-board" aria-label="Недельная шкала"><div class="planner-axis"><span>00</span><span>06</span><span>12</span><span>18</span><span>24</span></div><div class="week-grid">${weekRows(r)}</div><div class="week-legend"><span>Синхронизация разрешена</span><span>Пауза</span></div><p class="board-instruction">${icon('move')}<span>Потяните край полосы. Время общее для выбранных дней.</span></p></div>`;
}
function preview(r) {
  return `<div class="panel-label">${icon('calendar')}Неделя синхронизации</div><div class="large-time" data-live-time>${esc(r.start)}<i>—</i>${esc(r.end)}</div><div class="schedule-caption" data-live-zone>${esc(r.zone)}</div>${board(r)}<p class="window-note" data-window-note>${esc(windowNote(r))}</p>`;
}
function quickWindow(r) {
  return `<form class="panel quick-window" data-id="${r.id}"><div class="planner-heading"><h2>Неделя синхронизации</h2><span class="mono">${esc(r.zone)}</span></div><div class="range-times">${timeInput('start', 'Начало', r.start)}<span aria-hidden="true">—</span>${timeInput('end', 'Конец', r.end)}</div>${board(r)}<div class="planner-foot"><div><p data-window-note>${esc(windowNote(r))}</p><span class="hint" data-draft>Сохранено · версия ${r.version}</span></div><div class="action-pair"><button type="button" class="quiet" data-act="undo-window" hidden>Отменить</button><button class="btn primary" type="submit" disabled>Сохранить окно</button></div></div><p class="form-error" role="alert"></p></form>`;
}
function nextWindows(r) {
  if (!r.enabled) return '<p class="hint">Расписание выключено.</p>';
  if (!r.start || !r.end || r.start === r.end)
    return '<p class="hint">Укажите разные границы окна.</p>';
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: r.zone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(db.clock)
      .map((p) => [p.type, p.value]),
  );
  const first = Date.UTC(+parts.year, +parts.month - 1, +parts.day);
  const dateLabel = new Intl.DateTimeFormat('ru', {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  });
  const items = [];
  for (let i = 0; i < 21 && items.length < 3; i++) {
    const date = new Date(first + i * 86400000),
      d = date.getUTCDay() || 7;
    if (!r.days.includes(d) || (!i && r.start <= `${parts.hour}:${parts.minute}`)) continue;
    items.push(
      `<li><div class="day-stamp">${DAYS[d - 1]}</div><div><strong>${dateLabel.format(date)}</strong><small>${esc(r.start)} — ${esc(r.end)}${r.end < r.start ? ' · +1 день' : ''}</small></div></li>`,
    );
  }
  return `<ol class="next-windows">${items.join('')}</ol>`;
}
function rulesPage() {
  const all = [...db.rules].sort((a, b) => Number(b.enabled) - Number(a.enabled));
  const selected = ruleBy(current().q.get('rule')) || all[0];
  const create = link('/rules/new', icon('add') + 'Создать правило', 'btn primary');
  if (!selected)
    return title('Правила') + empty('Правил пока нет', 'Создайте расписание для папки.', create);
  const f = folderBy(selected.folder);
  const selector =
    all.length > 1
      ? `<label class="rule-selector">Правило<select data-query="rule">${all.map((r) => opt(r.id, folderName(folderBy(r.folder)) + ' · ' + address(folderBy(r.folder)) + ' · ' + r.start + '–' + r.end + ' · v' + r.version, selected.id)).join('')}</select></label>`
      : '';
  return (
    title('Правила') +
    `<div class="planner-selection"><div class="identity">${folderIcon(f)}<div><span class="mono">${esc(address(f))}</span><h2>${esc(folderName(f))}</h2></div></div>${selector}<div class="action-pair">${link(rulePath(selected), 'Параметры правила ' + icon('diagonal'), 'btn')}${create}</div></div><div class="planner-layout">${quickWindow(selected)}<aside class="planner-aside"><section class="panel next-panel"><div class="panel-label">${icon('calendar')}Ближайшие окна</div><div data-upcoming>${nextWindows(selected)}</div><div class="schedule-now"><span>Сейчас по правилу</span><strong>${desired(selected)}</strong></div></section>${block('Последняя попытка', `<div data-latest="${selected.id}">${lastAttempt(selected)}</div>`)}</aside></div>`
  );
}
function windowForm(el) {
  return el.closest('.quick-window') || $('#rule-form');
}
function windowDraft(form) {
  if (form.id === 'rule-form') return readRule(form);
  return {
    ...ruleBy(form.dataset.id),
    start: form.elements.start.value,
    end: form.elements.end.value,
  };
}
function updateWindow(form) {
  const draft = windowDraft(form),
    quick = form.classList.contains('quick-window'),
    root = quick ? form : $('#preview');
  $('.week-grid', root).innerHTML = weekRows(draft);
  if (edgeDrag && edgeDrag.form === form)
    edgeDrag.board
      .querySelector(`[data-edge="${edgeDrag.edge}"][data-owner="${edgeDrag.owner}"]`)
      ?.classList.add('drag-origin');
  $('[data-window-note]', root).textContent = windowNote(draft);
  $('.form-error', form).textContent = '';
  if (quick) {
    const saved = ruleBy(draft.id),
      dirty = draft.start !== saved.start || draft.end !== saved.end;
    $('[type=submit]', form).disabled = !dirty || current().q.get('view') === 'stale';
    $('[data-act=undo-window]', form).hidden = !dirty;
    $('[data-draft]', form).textContent = dirty
      ? 'Есть несохранённые изменения'
      : 'Сохранено · версия ' + saved.version;
    const upcoming = $('[data-upcoming]');
    if (upcoming) upcoming.innerHTML = nextWindows(draft);
  } else {
    $('[data-live-time]', root).innerHTML =
      esc(draft.start || '––:––') + '<i>—</i>' + esc(draft.end || '––:––');
    $('[data-live-zone]', root).textContent = draft.zone;
    $('#inline-window').textContent = windowNote(draft);
  }
}
function moveEdge(form, edge, value) {
  if (current().q.get('view') === 'stale') return;
  const next = formatMinute(value);
  if (
    next === form.elements[edge].value ||
    next === form.elements[edge === 'start' ? 'end' : 'start'].value
  )
    return;
  form.elements[edge].value = next;
  updateWindow(form);
}
let edgeDrag = null;
function focusEdge(drag) {
  const grip = drag.board.querySelector(`[data-edge="${drag.edge}"][data-owner="${drag.owner}"]`);
  grip?.focus({ preventScroll: true });
}
function finishEdge(e, undo = false) {
  if (!edgeDrag || (e.pointerId != null && e.pointerId !== edgeDrag.pointer)) return;
  const drag = edgeDrag;
  edgeDrag = null;
  if (undo) {
    drag.form.elements[drag.edge].value = drag.old;
    updateWindow(drag.form);
  }
  if (drag.board.hasPointerCapture(drag.pointer)) drag.board.releasePointerCapture(drag.pointer);
  drag.board.classList.remove('dragging');
  drag.board.querySelector('.drag-origin')?.classList.remove('drag-origin');
  delete drag.board.dataset.dragEdge;
  focusEdge(drag);
}
document.addEventListener('pointerdown', (e) => {
  const grip = e.target.closest('.window-grip');
  if (!grip || e.button !== 0 || !e.isPrimary || current().q.get('view') === 'stale') return;
  const chart = grip.closest('.week-board'),
    form = windowForm(chart),
    rect = grip.closest('.planner-track').getBoundingClientRect();
  e.preventDefault();
  edgeDrag = {
    board: chart,
    form,
    owner: grip.dataset.owner,
    edge: grip.dataset.edge,
    old: form.elements[grip.dataset.edge].value,
    left: rect.left,
    width: rect.width,
    pointer: e.pointerId,
    offset: e.clientX - (rect.left + (rect.width * parseFloat(grip.style.left)) / 100),
  };
  chart.setPointerCapture(e.pointerId);
  chart.classList.add('dragging');
  chart.dataset.dragEdge = grip.dataset.edge;
  grip.classList.add('drag-origin');
  grip.focus({ preventScroll: true });
});
document.addEventListener('pointermove', (e) => {
  const d = edgeDrag;
  if (!d || e.pointerId !== d.pointer) return;
  const minute = Math.max(
    0,
    Math.min(1440, Math.round(((e.clientX - d.offset - d.left) / d.width) * 96) * 15),
  );
  moveEdge(d.form, d.edge, minute === 1440 ? (d.edge === 'end' ? 0 : 1425) : minute);
});
document.addEventListener('pointerup', (e) => finishEdge(e));
document.addEventListener('pointercancel', (e) => finishEdge(e, true));
document.addEventListener('lostpointercapture', (e) => {
  if (edgeDrag?.pointer === e.pointerId) finishEdge(e, true);
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && edgeDrag) {
    finishEdge(e, true);
    return;
  }
  const grip = e.target.closest('.window-grip');
  if (!grip) return;
  const chart = grip.closest('.week-board'),
    form = windowForm(chart),
    edge = grip.dataset.edge;
  const direction = {
    ArrowLeft: -1,
    ArrowDown: -1,
    ArrowRight: 1,
    ArrowUp: 1,
    PageDown: -1,
    PageUp: 1,
  }[e.key];
  if (!direction && !['Home', 'End'].includes(e.key)) return;
  e.preventDefault();
  const step = e.shiftKey || e.key.startsWith('Page') ? 60 : 15;
  let value =
    e.key === 'Home'
      ? 0
      : e.key === 'End'
        ? 1439
        : (minuteValue(form.elements[edge].value) + direction * step + 1440) % 1440;
  if (direction && value === minuteValue(form.elements[edge === 'start' ? 'end' : 'start'].value))
    value = (value + direction * step + 1440) % 1440;
  moveEdge(form, edge, value);
  focusEdge({ board: chart, edge, owner: grip.dataset.owner });
});

function lastAttempt(r) {
  const a = db.attempts.find((a) => a.rule === r.id);
  if (!a) return `<p class="mut">Попыток для этого правила пока нет.</p>`;
  const prior =
    r.version !== a.version
      ? `<p class="hint prior">Для версии ${r.version} попыток пока нет.</p>`
      : '';
  return `${prior}<div class="attempt-line"><span>${stamp(a.created).slice(11)} UTC · версия ${a.version}</span>${badge(a.status)}</div>${link(commandPath(a), 'Открыть последнюю попытку ' + icon('arrow'), 'text-link')}`;
}
function rulePage(r) {
  const f = folderBy(r.folder);
  return (
    breadcrumbs([['Правила', '/rules'], [address(f)]]) +
    title('Правило папки') +
    `<div class="split rule-layout"><div class="stack"><section class="panel"><div class="list-rule-head"><span class="panel-label">${icon('folder')}${localName(f)}</span>${badge(r.enabled ? 'online' : 'cancelled', r.enabled ? 'Включено' : 'Выключено')}</div><h2 class="record-name">${esc(folderName(f))}</h2>${facts(
      [
        ['Сейчас по правилу', desired(r)],
        ['Версия расписания', String(r.version)],
        [
          'Фактическая пауза',
          f.paused == null
            ? 'Неизвестна'
            : (f.paused ? 'Включена' : 'Выключена') + (isFresh(f) ? '' : ' · устарело'),
        ],
      ],
    )}<div class="actions">${link(rulePath(r) + '/edit', icon('edit') + 'Изменить расписание', 'btn primary')}${action('toggle-rule', r.enabled ? 'Отключить' : 'Включить', 'btn', `data-id="${r.id}"`)}${action('delete-rule', 'Удалить', 'quiet', `data-id="${r.id}"`)}</div></section>${block('Последняя попытка', `<div data-latest="${r.id}">${lastAttempt(r)}</div>`)}</div><aside class="stack">${quickWindow(r)}${link(folderPath(f), 'Карточка папки ' + icon('arrow'), 'text-link')}</aside></div>`
  );
}
const timeInput = (name, label, val) =>
  `<label>${label}<input type="time" required name="${name}" value="${attr(val)}"></label>`;
function editor(existing, q) {
  const r = existing || {
    folder: folderBy(q.get('folder'))?.id || db.folders[0].id,
    start: '09:00',
    end: '18:00',
    zone: 'Europe/Moscow',
    days: [1, 2, 3, 4, 5],
    enabled: !activeFor(q.get('folder') || db.folders[0].id),
  };
  const f = folderBy(r.folder);
  return (
    breadcrumbs([
      ['Правила', '/rules'],
      [existing ? folderName(f) : 'Создание', existing ? rulePath(existing) : null],
    ]) +
    title(existing ? 'Изменить расписание' : 'Новое правило') +
    `<div class="split rule-layout"><form class="panel editor" id="rule-form" data-id="${existing?.id || ''}"><h2>Параметры расписания</h2>${existing ? `<p class="target-line">${icon('folder')}${esc(folderName(f))} · ${localName(f)}</p><input type="hidden" name="folder" value="${f.id}">` : `<label>Узел и папка<select name="folder">${db.folders.map((f) => opt(f.id, folderName(f) + ' · ' + address(f), r.folder)).join('')}</select></label>`}<fieldset><legend>Дни начала окна</legend><div class="days">${DAYS.map((d, i) => `<label><input type="checkbox" name="day" value="${i + 1}" ${r.days.includes(i + 1) ? 'checked' : ''}><span>${d}</span></label>`).join('')}</div></fieldset><div class="field-pair">${timeInput('start', 'Начало окна', r.start)}${timeInput('end', 'Конец окна', r.end)}</div><label>Часовой пояс IANA<input name="zone" list="zones" required value="${attr(r.zone)}" autocomplete="off"></label><datalist id="zones">${['Europe/Moscow', 'Europe/Berlin', 'Asia/Yekaterinburg', 'UTC', 'America/New_York'].map((z) => opt(z, z)).join('')}</datalist><label class="switch"><input type="checkbox" name="enabled"${r.enabled ? ' checked' : ''}><span aria-hidden="true"></span>Расписание включено</label><p class="mobile-window" id="inline-window">${esc(windowNote(r))}</p><p class="form-error" role="alert"></p><div class="actions"><button class="btn primary" type="submit">${existing ? 'Сохранить изменения' : 'Создать правило'}</button>${link(existing ? rulePath(existing) : '/rules', 'Отмена', 'quiet')}</div></form><aside class="panel schedule-detail" id="preview">${preview(r)}</aside></div>`
  );
}
function verification(a) {
  return {
    unknown: a.code === 'rule_changed_after_send' ? 'Правило изменилось' : 'Нет наблюдения',
    applied: 'Повторное чтение конфигурации',
    pending: 'Ожидается повторное чтение',
    failed: 'Получен отказ',
    cancelled: 'Отменена до проверки',
  }[a.status];
}
function filterFields(q, tab) {
  const node = q.get('node') || '',
    folder = q.get('folder') || '';
  return `<label>Узел<select name="node">${nodeOptions(node)}</select></label><label>Папка<select name="folder">${opt('', 'Все папки', folder)}${db.folders
    .filter((f) => !node || f.node === node)
    .map((f) => opt(f.id, folderName(f) + ' · ' + nodeBy(f.node).name, folder))
    .join('')}</select></label>${
    tab === 'commands'
      ? `<label>Результат<select name="status">${opt('', 'Все результаты', q.get('status'))}${Object.entries(
          LABEL,
        )
          .map(([v, t]) => opt(v, t, q.get('status')))
          .join('')}</select></label>`
      : ''
  }`;
}
function periodForm(q, tab) {
  return `<form id="period-form" class="filter-panel" data-tab="${tab}"><div class="filter-row">${['from', 'to'].map((k, i) => `<label>${i ? 'До, UTC (не включая)' : 'С, UTC'}<input name="${k}" type="datetime-local" value="${attr(q.get(k) || '')}"></label>`).join('')}${tab === 'analytics' ? `<label>Узел<select name="node">${nodeOptions(q.get('node'))}</select></label>` : filterFields(q, tab)}<button class="btn" type="submit">Применить</button></div><p class="form-error" role="alert"></p></form>`;
}
function range(q, defaults = false) {
  const bound = (key, fallback) => {
    const ms = Date.parse((q.get(key) || '') + 'Z');
    return Number.isFinite(ms) ? ms : fallback;
  };
  return [
    bound('from', defaults ? db.clock + 1000 - 86400000 : -Infinity),
    bound('to', defaults ? db.clock + 1000 : Infinity),
  ];
}
function recordRows(tab, q) {
  const [from, to] = range(q);
  return (tab === 'commands' ? db.attempts : db.events)
    .filter((a) => {
      const f = folderBy(a.folder),
        at = a.at ?? a.created;
      return (
        at >= from &&
        at < to &&
        (!q.get('node') || f.node === q.get('node')) &&
        (!q.get('folder') || f.id === q.get('folder')) &&
        (tab === 'events' || !q.get('status') || a.status === q.get('status'))
      );
    })
    .sort((a, b) => (b.at ?? b.created) - (a.at ?? a.created));
}
function auditTable(tab, q) {
  const commands = tab === 'commands',
    records = recordRows(tab, q),
    size = 5;
  const page = Math.max(
    1,
    Math.min(Math.ceil(records.length / size), parseInt(q.get('page'), 10) || 1),
  );
  const items = records.slice((page - 1) * size, page * size);
  if (!items.length)
    return empty(
      'За этот период записей нет',
      'Измените период или снимите фильтры.',
      action('clear', 'Сбросить фильтры'),
    );
  return `<div class="audit-scroll" role="region" aria-label="Записи истории" tabindex="0"><table class="audit-table ${commands ? 'command-table' : 'event-table'}"><thead><tr><th>Время, UTC</th><th>${commands ? 'Цель команды' : 'Событие'}</th><th>Папка и узел</th>${commands ? '<th>Результат и проверка</th>' : ''}<th><span class="only-reader">Открыть запись</span></th></tr></thead><tbody>${items
    .map((a) => {
      const f = folderBy(a.folder),
        ts = stamp(a.at ?? a.created),
        url = commands ? commandPath(a) : '/history/events/' + a.id;
      return `<tr><td><strong class="mono">${ts.slice(11)}</strong><small>${ts.slice(0, 10)}</small></td><td>${link(url, commands ? (a.paused ? 'Приостановить' : 'Возобновить') : 'Обработка элемента завершена', 'cell-link')}<small>${commands ? (a.rule ? 'По расписанию · v' + a.version : 'Вручную') : 'ItemFinished'}</small></td><td>${esc(folderName(f))}<small>${localName(f)}</small></td>${commands ? `<td>${badge(a.status)}<small>${verification(a)}</small></td>` : ''}<td>${link(url, icon('diagonal'), 'iconbtn', `data-tip="Открыть запись" aria-label="Открыть запись ${ts}"`)}</td></tr>`;
    })
    .join(
      '',
    )}</tbody></table></div><div class="pagination"><span>${(page - 1) * size + 1}–${(page - 1) * size + items.length} из ${records.length}</span><div>${pageButton(page - 1, page === 1, 'Назад')}${pageButton(page + 1, page * size >= records.length, 'Далее')}</div></div>`;
}
function pageButton(page, blocked, text) {
  const extra = blocked
    ? `aria-disabled="true" data-tip="${text === 'Назад' ? 'Первая страница' : 'Последняя страница'}"`
    : `data-id="${page}"`;
  return action('page', text, 'btn', extra);
}
function historyPage(tab, q) {
  const other = new URLSearchParams(q);
  other.delete('status');
  other.delete('page');
  const tabs = `<nav class="tabs history-tabs" aria-label="Вид истории"><i class="tab-ink" aria-hidden="true"></i>${[
    ['commands', 'Команды'],
    ['events', 'События'],
  ]
    .map(([k, v]) =>
      link(
        queryURL('/history/' + k, k === tab ? q : other),
        v,
        'tab',
        k === tab ? 'aria-current="page"' : '',
      ),
    )
    .join('')}</nav>`;
  const selected = [
    nodeBy(q.get('node'))?.name,
    folderName(folderBy(q.get('folder'))),
    LABEL[q.get('status')],
    q.has('from') || q.has('to') ? 'Задан период' : null,
  ].filter(Boolean);
  const filters = `<details class="history-filters" ${matchMedia('(min-width:721px)').matches ? 'open' : ''}><summary>Фильтры ${icon('down')}${selected.length ? `<small>${esc(selected.join(' · '))}</small>` : ''}</summary>${periodForm(q, tab)}</details>`;
  return (
    title('История') +
    tabs +
    filters +
    `<section class="panel audit"><div class="bar-head"><h2>${tab === 'commands' ? 'Попытки команд' : 'События Syncthing'}</h2><span class="mono">UTC</span></div><div data-audit>${auditTable(tab, q)}</div></section>`
  );
}
function contextBlock(f) {
  return `<div class="panel-label">${icon('folder')}${localName(f)}</div><h2 class="record-name">${esc(folderName(f))}</h2>${link(folderPath(f), 'Открыть папку ' + icon('arrow'))}<div class="hairline"></div>${link(scopedHistory(f), 'История этой папки ' + icon('arrow'))}`;
}
function attemptBody(a) {
  const items = [
    [
      'Намерение создано',
      time(a.created),
      a.rule ? 'По расписанию · версия ' + a.version : 'Ручное возобновление',
    ],
    [
      'Начало отправки',
      a.sent ? time(a.sent) : 'Нет отметки',
      a.sent ? 'Исполнитель начал попытку.' : 'Время начала отправки отсутствует.',
    ],
    [
      'Проверка конфигурации',
      a.verified ? time(a.verified) : a.status === 'pending' ? 'Ожидание' : 'Нет наблюдения',
      verification(a),
    ],
  ];
  return `<div class="attempt-result"><span class="panel-label">${icon('pulse')}Результат попытки</span>${badge(a.status, a.status === 'unknown' ? 'Результат неизвестен' : LABEL[a.status])}</div><ol class="timeline">${items.map(([label, t, hint], i) => `<li class="${i === 2 ? 'last-point' : ''}"><span class="timeline-dot"></span><div><h3>${label}</h3><small>${hint}</small></div><time>${t}</time></li>`).join('')}</ol>${a.code ? `<div class="reason"><span>Причина</span><code>${esc(a.code)}</code></div>` : ''}`;
}
function recordPage(tab, a) {
  const f = folderBy(a.folder),
    cmd = tab === 'commands';
  const back = queryURL('/history/' + tab, { node: f.node, folder: f.id });
  const context =
    contextBlock(f) +
    (cmd && ruleBy(a.rule)
      ? `<div class="hairline"></div>${link(rulePath(ruleBy(a.rule)), 'Текущее правило ' + icon('arrow'))}`
      : '');
  return (
    breadcrumbs([['История', back], [cmd ? 'Попытка команды' : 'Событие Syncthing']]) +
    title(cmd ? (a.paused ? 'Приостановить' : 'Возобновить') : 'Обработка элемента завершена') +
    `<div class="record-date">${icon('clock')}${stamp(a.created ?? a.at).slice(0, 10)} · ${time(a.created ?? a.at)}</div><div class="split"><section class="panel">${
      cmd
        ? `<div data-attempt="${a.id}">${attemptBody(a)}</div>`
        : `<h2>Событие Syncthing</h2>${facts([
            ['Тип', '<code>ItemFinished</code>'],
            ['Время источника', time(a.at)],
            ['Получено', time(a.at)],
            ['Объект', localName(f)],
          ])}${note('Источник сообщил о завершении обработки элемента.')}`
    }</section><aside class="panel">${context}</aside></div>`
  );
}
function analytics(q) {
  const [from, to] = range(q, true),
    node = q.get('node'),
    within = (t) => t != null && t >= from && t < to;
  const items = db.attempts.filter(
    (a) =>
      (!node || folderBy(a.folder).node === node) &&
      within(a.status === 'pending' ? a.created : a.completed),
  );
  const probes = db.probes.filter((p) => (!node || node === p.node) && within(p.at));
  const count = (status) => items.filter((a) => a.status === status).length;
  const percent = (x, n) => (n ? Math.round((x / n) * 100) + '%' : 'Нет данных');
  const applied = count('applied'),
    known = applied + count('failed'),
    ok = probes.filter((p) => p.ok).length;
  const metric = (name, ic, x, n, caption, rows) =>
    `<section class="panel metric-card"><div class="panel-label">${icon(ic)}${name}</div><div class="metric">${percent(x, n)}</div><p>${caption}</p><div class="ratio ${n ? '' : 'no-denominator'}" aria-hidden="true"><i style="width:${n ? (100 * x) / n : 0}%"></i></div>${facts(rows)}</section>`;
  const periods = [
    ['24h', '24 часа'],
    ['7d', '7 дней'],
    ['30d', '30 дней'],
  ];
  return (
    title('Аналитика') +
    `<div class="bar-head"><div class="tabs period-tabs" aria-label="Период">${periods.map(([k, t]) => action('period', t, 'tab', `data-id="${k}" aria-pressed="${(q.get('period') || '24h') === k}"`)).join('')}</div><span class="period-caption mono">${stamp(from)} — ${stamp(to)} UTC</span></div><details class="range-details" ${q.get('period') === 'custom' ? 'open' : ''}><summary>Период и узел ${icon('down')}</summary>${periodForm(q, 'analytics')}</details><div class="metric-grid">${metric(
      'Подтверждение команд',
      'check',
      applied,
      known,
      `${applied} из ${known} попыток с определённым исходом`,
      [
        ['Подтверждено', applied],
        ['Отказ', count('failed')],
        ['Неизвестно', count('unknown')],
        ['Ожидают проверки / отменены', count('pending') + ' / ' + count('cancelled')],
      ],
    )}${metric(
      'Наблюдаемая доступность',
      'pulse',
      ok,
      probes.length,
      `${ok} из ${probes.length} проверок API узла`,
      [
        ['Успешные проверки', ok],
        ['Неуспешные проверки', probes.length - ok],
        [
          'Последнее наблюдение',
          probes.length ? time(Math.max(...probes.map((p) => p.at))) : 'Нет данных',
        ],
      ],
    )}</div><details class="explain"><summary>Как считаются показатели ${icon('down')}</summary><div class="explain-body"><p>Подтверждение — доля применённых команд среди применённых и отказов по времени завершения. Ожидающие учитываются по времени создания.</p><p>Доступность — доля успешных наблюдений API среди сохранённых проверок узла. При нулевом знаменателе показатель пуст.</p></div></details>`
  );
}
function login() {
  const well = $('#approved').content.querySelector('.folder-well').cloneNode(true);
  $('.folder-head', well).innerHTML =
    `<span class="vcenter">${icon('folder')}Демонстрационный стенд</span>`;
  $('.folder-body', well).innerHTML =
    '<div class="login-wordmark"><span class="emblem" aria-hidden="true"></span><div>Syncthing<br>Control Center</div></div>';
  return `<div class="login-layout">${well.outerHTML}<div class="login-panel"><header class="brand"><span class="emblem" aria-hidden="true"></span>Syncthing <span class="brand-sub">Control Center</span></header><h1 tabindex="-1">Вход</h1><form id="login-form"><label>Логин<input name="login" autocomplete="username" required value="admin"></label><label>Пароль<span class="password"><input name="password" type="password" autocomplete="current-password" required><button type="button" data-act="show-pass" data-tip="Показать пароль" aria-label="Показать пароль">${icon('eye')}</button></span></label><p class="form-error" role="alert"></p><button class="btn primary" type="submit">Войти ${icon('arrow')}</button>${note('Демодоступ · логин <code>admin</code>, пароль <code>demo</code>')}</form></div></div>`;
}
function placeTab() {
  const selected = $('.history-tabs [aria-current]'),
    ink = $('.tab-ink');
  if (!selected || !ink) return;
  Object.assign(ink.style, {
    left: selected.offsetLeft + 'px',
    top: selected.offsetTop + 'px',
    width: selected.offsetWidth + 'px',
    height: selected.offsetHeight + 'px',
  });
}
function placeNav() {
  const nav = $('.mast nav'),
    selected = $('a[aria-current]', nav),
    glass = $('.nav-glass', nav);
  if (!selected || !glass || nav.offsetWidth === 0) return;
  glass.style.transform = 'translateX(' + selected.offsetLeft + 'px)';
  glass.style.width = selected.offsetWidth + 'px';
  glass.style.height = selected.offsetHeight + 'px';
  glass.style.top = selected.offsetTop + 'px';
  glass.hidden = false;
}
function standardView(r) {
  const [s, id, sub] = r.parts;
  if (s === 'login' && r.parts.length === 1) return login();
  if (s === 'overview' && !id) return overview();
  if (s === 'folders' && sub === 'appearance' && r.parts.length === 3 && folderBy(id))
    return appearancePage(folderBy(id));
  if (s === 'folders' && !sub) {
    if (!id) return foldersPage(r.q);
    const f = folderBy(id);
    if (f) return folderPage(f);
  }
  if (s === 'rules') {
    if (!id) return rulesPage();
    if (id === 'new' && !sub) return editor(null, r.q);
    const rule = ruleBy(id);
    if (rule && r.parts.length <= 3) {
      if (sub === 'edit') return editor(rule, r.q);
      if (!sub) return rulePage(rule);
    }
  }
  if (s === 'history' && ['commands', 'events'].includes(id)) {
    if (!sub) return historyPage(id, r.q);
    const a = (id === 'commands' ? db.attempts : db.events).find((x) => x.id === sub);
    if (a && r.parts.length === 3) return recordPage(id, a);
  }
  if (s === 'analytics' && !id) return analytics(r.q);
  return (
    title('Запись не найдена') +
    empty(
      'Этот адрес недоступен',
      'Запись удалена или отсутствует в выбранном наборе.',
      link(PATHS[s] || '/overview', 'В раздел', 'btn'),
    )
  );
}
const STATE_TEXT = {
  overview: ['Сводка пуста', 'Наблюдаемые объекты пока отсутствуют.'],
  folders: ['Папок нет', 'Для выбранного узла пока нет папок.'],
  rules: ['Правил нет', 'Добавьте расписание для управляемой папки.'],
  history: ['Записей нет', 'За выбранный период записей нет.'],
  analytics: ['Нет данных', 'Сохранённых проверок и попыток за период нет.'],
};
let loadTimer, toastTimer, tipOwner;
function render(enter = true, restore = null) {
  if (edgeDrag) {
    const { board, pointer } = edgeDrag;
    edgeDrag = null;
    if (board.hasPointerCapture(pointer)) board.releasePointerCapture(pointer);
  }
  markFlight?.cancel();
  clearTimeout(loadTimer);
  closePop();
  hideTip();
  let r = current();
  if (!db.logged && r.sec !== 'login') {
    const next = r.raw;
    history.replaceState(history.state, '', '#' + queryURL('/login', { next }));
    r = current();
  }
  const mode = r.q.get('view'),
    names = STATE_TEXT[r.sec] || ['Записей нет', 'Данные отсутствуют.'];
  let page;
  if (r.sec !== 'login' && ['loading', 'error', 'empty'].includes(mode)) {
    if (mode === 'loading') {
      page = `<div class="loading" role="status"><i></i>Загружаем данные…</div>`;
      loadTimer = setTimeout(() => {
        replaceQuery({ view: '' });
        render(false);
        toast('Данные загружены');
      }, 1400);
    }
    if (mode === 'error')
      page = empty(
        'Не удалось загрузить данные',
        'Не удалось прочитать этот раздел.',
        action('retry', 'Повторить', 'btn primary'),
      );
    if (mode === 'empty')
      page = empty(
        ...names,
        r.sec === 'rules'
          ? link('/rules/new', 'Создать правило', 'btn primary')
          : action('retry', 'Вернуться к снимку'),
      );
    page = title(sectionNames[r.sec] || 'Раздел') + page;
  } else {
    page = standardView(r);
  }
  main.innerHTML = page;
  placeTab();
  if (mode === 'stale' && r.sec !== 'login') {
    const banner = document.createElement('div');
    banner.className = 'stale-banner';
    banner.setAttribute('role', 'status');
    banner.innerHTML =
      icon('clock') +
      'Показан сохранённый снимок. Данные устарели. ' +
      action('retry', 'Обновить', 'text-link');
    const heading = $('.title-a, .object-meta', main);
    heading?.after(banner);
    $$(
      'button[data-act="resume"],button[data-act="toggle-rule"],button[data-act="delete-rule"],#rule-form button[type="submit"],.quick-window button[type="submit"],.window-grip',
      main,
    ).forEach((el) => {
      el.setAttribute('aria-disabled', 'true');
      el.dataset.tip = 'Обновите снимок перед изменением.';
    });
  }
  const isLogin = r.sec === 'login';
  $('#shell').classList.toggle('auth', isLogin);
  $('.mast').hidden = isLogin;
  $$('.mast nav a').forEach((a) => {
    if (a.dataset.section === r.sec) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  placeNav();
  document.title = ($('h1', main)?.textContent || 'Обзор') + ' · Syncthing Control Center';
  if (enter) {
    main.classList.remove('enter');
    void main.offsetWidth;
    main.classList.add('enter');
    const focusTo =
      restore?.key &&
      $$('a,button,input,select', main).find(
        (el) => el.dataset.key === restore.key || el.getAttribute('href') === restore.key,
      );
    (focusTo || $('h1', main))?.focus({ preventScroll: true });
    window.scrollTo(0, restore?.y || 0);
  }
}
function toast(message) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), 4200);
}
function hideTip() {
  if (tipOwner) {
    tipOwner.removeAttribute('aria-describedby');
    tipOwner = null;
  }
  $('#tip').hidden = true;
}
function showTip(el) {
  if (!el) return;
  hideTip();
  tipOwner = el;
  const tip = $('#tip');
  tip.textContent = el.dataset.tip || el.getAttribute('aria-label');
  tip.hidden = false;
  el.setAttribute('aria-describedby', 'tip');
  const rect = el.getBoundingClientRect(),
    box = tip.getBoundingClientRect();
  tip.style.left =
    Math.max(8, Math.min(innerWidth - box.width - 8, rect.left + (rect.width - box.width) / 2)) +
    'px';
  tip.style.top =
    (rect.bottom + box.height + 10 > innerHeight
      ? Math.max(6, rect.top - box.height - 7)
      : rect.bottom + 6) + 'px';
}
let popButton;
function closePop(refocus = false) {
  const pop = $('.popover');
  if (pop) {
    pop.remove();
    popButton?.setAttribute('aria-expanded', 'false');
    if (refocus) popButton?.focus();
  }
  popButton = null;
}
function openPop(button, html) {
  const same = button === popButton;
  closePop();
  if (same) return;
  popButton = button;
  button.setAttribute('aria-expanded', 'true');
  button.insertAdjacentHTML('afterend', html);
  const box = $('.popover'),
    b = button.getBoundingClientRect();
  box.style.top = b.bottom + window.scrollY + 8 + 'px';
  box.style.left =
    Math.max(8, Math.min(innerWidth - box.offsetWidth - 8, b.right - box.offsetWidth)) + 'px';
  $('select,button,a', box)?.focus();
  hideTip();
}
function confirmAction(titleText, desc, cta, fn) {
  const dlg = $('#dialog');
  hideTip();
  dlg.innerHTML = `<form method="dialog"><h2 id="dialog-title">${esc(titleText)}</h2><p>${esc(desc)}</p><div class="actions"><button class="btn" value="cancel" autofocus>Отмена</button><button class="btn primary" value="confirm">${esc(cta)}</button></div></form>`;
  dlg.returnValue = '';
  dlg.onclose = () => {
    if (dlg.returnValue === 'confirm') fn();
  };
  dlg.showModal();
}
function readRule(form) {
  const fd = new FormData(form);
  return {
    folder: fd.get('folder'),
    days: fd.getAll('day').map(Number),
    start: fd.get('start'),
    end: fd.get('end'),
    zone: fd.get('zone').trim(),
    enabled: fd.has('enabled'),
  };
}
function validateRule(r, id) {
  if (!r.days.length || !r.start || !r.end || r.start === r.end) return windowNote(r);
  if (/^[+-]/.test(r.zone)) return 'Укажите имя часового пояса IANA.';
  try {
    new Intl.DateTimeFormat('ru', { timeZone: r.zone });
  } catch {
    return 'Укажите действующий часовой пояс IANA.';
  }
  if (
    r.enabled &&
    db.rules.some((other) => other.folder === r.folder && other.enabled && other.id !== id)
  )
    return 'У этой папки уже есть включённое правило. Сохраните новое выключенным или отключите действующее.';
  if (
    r.enabled &&
    db.attempts.some((a) => a.folder === r.folder && !a.rule && a.status === 'pending')
  )
    return 'Дождитесь завершения ручной команды.';
  return '';
}
function saveRule(form) {
  const old = ruleBy(form.dataset.id),
    data = windowDraft(form),
    msg = validateRule(data, old?.id);
  if (msg) {
    $('.form-error', form).textContent = msg;
    return;
  }
  if (old && Object.keys(data).every((k) => String(old[k]) === String(data[k]))) {
    if (!form.classList.contains('quick-window')) go(rulePath(old));
    toast('Изменений нет');
    return;
  }
  const rule = { ...data, id: old?.id || crypto.randomUUID(), version: old ? old.version + 1 : 1 };
  if (old) db.rules.splice(db.rules.indexOf(old), 1, rule);
  else db.rules.push(rule);
  if (rule.enabled) enqueue(folderBy(rule.folder), rule);
  persist();
  if (form.classList.contains('quick-window')) {
    render(false);
    $('.quick-window [name=start]')?.focus({ preventScroll: true });
    $('.quick-window')?.classList.add('just-saved');
  } else go(rulePath(rule), { replace: true });
  toast(`Расписание сохранено · версия ${rule.version}`);
}
function onAction(el) {
  const act = el.dataset.act,
    id = el.dataset.id,
    r = ruleBy(id);
  if (act === 'cancel-appearance') {
    finishAppearance(folderBy(id));
    return;
  }
  if (act === 'feature') {
    preferences.featured = id;
    storePreferences();
    render(false);
    $('.featured-tag')?.focus({ preventScroll: true });
    toast('Папка закреплена на обзоре');
    return;
  }
  if (act === 'undo-window') {
    const form = el.closest('form');
    form.reset();
    updateWindow(form);
    form.elements.start.focus({ preventScroll: true });
    return;
  }
  if (act === 'demo') {
    openPop(el, demoPanel());
    return;
  }
  if (act === 'account') {
    openPop(
      el,
      `<aside class="popover account-panel"><strong>Администратор</strong>${action('logout', 'Выйти из демосеанса ' + icon('arrow'), 'text-link')}</aside>`,
    );
    return;
  }
  if (act === 'logout') {
    db.logged = false;
    persist();
    go('/login', { nav: true });
    return;
  }
  if (act === 'show-pass') {
    const field = $('[name="password"]');
    field.type = field.type === 'password' ? 'text' : 'password';
    el.dataset.tip = field.type === 'password' ? 'Показать пароль' : 'Скрыть пароль';
    el.setAttribute('aria-label', el.dataset.tip);
    showTip(el);
    return;
  }
  if (act === 'refresh') {
    if (el.disabled) return;
    el.disabled = true;
    el.classList.add('reading');
    setTimeout(() => {
      if (!el.isConnected) return;
      replaceQuery({ view: '' });
      render(false);
      $('[data-act="refresh"]')?.focus();
      toast('Данные перечитаны');
    }, 400);
    return;
  }
  if (act === 'clear') {
    go(current().path, { replace: true });
    return;
  }
  if (act === 'retry') {
    replaceQuery({ view: '' });
    render();
    toast('Снимок загружен');
    return;
  }
  if (act === 'reset-data') {
    confirmAction(
      'Восстановить снимок?',
      'Прототип удалит изменения расписаний и демонстрационные попытки в этой вкладке.',
      'Восстановить',
      () => {
        db = seed(db.extended);
        persist();
        go('/overview', { replace: true, nav: true });
        toast('Исходный снимок восстановлен');
      },
    );
    return;
  }
  if (act === 'page') {
    replaceQuery({ page: id === '1' ? '' : id });
    render(false);
    $('.audit-scroll')?.focus();
    return;
  }
  if (act === 'period') {
    const days = { '24h': 1, '7d': 7, '30d': 30 }[id],
      end = Math.ceil((db.clock + 1) / 60000) * 60000;
    replaceQuery({
      from: stamp(end - days * 86400000)
        .slice(0, 16)
        .replace(' ', 'T'),
      to: stamp(end).slice(0, 16).replace(' ', 'T'),
      period: id,
    });
    render(false);
    $(`[data-act="period"][data-id="${id}"]`).focus();
    return;
  }
  if (act === 'delete-rule') {
    const f = folderBy(r.folder);
    confirmAction(
      'Удалить правило?',
      `Автоматическое управление по этому правилу для «${folderName(f)}» прекратится. Записи попыток сохранятся.`,
      'Удалить',
      () => {
        db.rules = db.rules.filter((x) => x !== r);
        persist();
        go('/rules', { replace: true });
        toast('Правило удалено. История сохранена.');
      },
    );
    return;
  }
  if (act === 'toggle-rule') {
    const enabled = !r.enabled,
      msg = validateRule({ ...r, enabled }, r.id);
    if (msg) {
      toast(msg);
      return;
    }
    const toggle = () => {
      r.enabled = enabled;
      r.version++;
      if (enabled) enqueue(folderBy(r.folder), r);
      persist();
      render(false);
      $('[data-act="toggle-rule"]')?.focus();
      toast(enabled ? 'Расписание включено' : 'Расписание выключено. Состояние папки сохранено.');
    };
    if (enabled) toggle();
    else
      confirmAction(
        'Отключить расписание?',
        'Автоматическое управление остановится. Текущая пауза папки сохранится.',
        'Отключить',
        toggle,
      );
    return;
  }
  if (act === 'resume') {
    const f = folderBy(id);
    if (resumeReason(f)) return;
    const a = enqueue(f, null);
    if (a) {
      render(false);
      $('[data-manual] a')?.focus();
      toast('Намерение сохранено. Ожидаем проверку.');
    }
    return;
  }
}
document.addEventListener('click', (e) => {
  const el = e.target.closest('button,a,summary');
  if ($('.popover') && !e.target.closest('.popover') && el !== popButton) closePop();
  if (!el) return;
  if (el.getAttribute('aria-disabled') === 'true') {
    e.preventDefault();
    showTip(el);
    return;
  }
  if (el.hasAttribute('data-filter')) {
    const type = el.dataset.filter;
    replaceQuery({ type: type === 'all' ? '' : type });
    $$('[data-filter]').forEach((b) => b.setAttribute('aria-pressed', String(b === el)));
    $$('[data-kind]').forEach((row) => {
      row.hidden = type !== 'all' && row.dataset.kind !== type;
    });
    return;
  }
  if (el.dataset.act) {
    onAction(el);
    return;
  }
  if (el.matches('a.skip')) {
    e.preventDefault();
    main.focus();
    return;
  }
  if (
    el.matches('a[href^="#/"]') &&
    !e.metaKey &&
    !e.ctrlKey &&
    !e.altKey &&
    !e.shiftKey &&
    e.button === 0
  ) {
    e.preventDefault();
    el.focus({ preventScroll: true });
    const origin = history.state?.origin;
    if (el.hasAttribute('data-return') && origin && origin.index < history.state.index) {
      history.go(origin.index - history.state.index);
      return;
    }
    go(el.getAttribute('href').slice(1), { nav: !!el.closest('.mast nav') });
    return;
  }
});
document.addEventListener('change', (e) => {
  const el = e.target;
  if (el.closest('#appearance-form') && el.name === 'glyph') {
    const form = el.form;
    form.elements.symbol.value = '';
    updateSample(form);
  }
  if (el.id === 'fixture') {
    db = seed(el.value === 'extended');
    persist();
    go('/overview', { replace: true, nav: true });
    toast('Набор демоданных загружен');
    return;
  }
  if (el.id === 'view-mode') {
    replaceQuery({ view: el.value });
    render();
    return;
  }
  if (el.dataset.query) {
    replaceQuery({ [el.dataset.query]: el.value });
    render(false);
    $(`[data-query="${el.dataset.query}"]`)?.focus();
  }
  if (el.closest('#period-form') && el.name === 'node') {
    const select = $('#period-form [name="folder"]');
    if (select)
      select.innerHTML =
        opt('', 'Все папки', '') +
        db.folders
          .filter((f) => !el.value || f.node === el.value)
          .map((f) => opt(f.id, folderName(f) + ' · ' + nodeBy(f.node).name, ''))
          .join('');
  }
  if (el.closest('#rule-form') && el.name === 'folder') {
    $('#rule-form [name="enabled"]').checked = !activeFor(el.value);
    updateWindow(el.form);
  }
});
document.addEventListener('input', (e) => {
  const el = e.target;
  if (el.id === 'folder-search') {
    replaceQuery({ search: el.value });
    $('#inventory').innerHTML = inventory(current().q);
    return;
  }
  if (el.closest('#appearance-form')) {
    if (el.name === 'symbol') {
      const choices = $$('[name=glyph]', el.form);
      if (el.value.trim())
        choices.forEach((radio) => {
          radio.checked = false;
        });
      else if (!choices.some((radio) => radio.checked))
        choices.find(
          (radio) => radio.value === (appearance(folderBy(el.form.dataset.id)).glyph || 'folder'),
        ).checked = true;
    }
    updateSample(el.form);
    $('.form-error', el.form).textContent = '';
    return;
  }
  const form = el.closest('#rule-form,.quick-window');
  if (form) updateWindow(form);
});
document.addEventListener('submit', (e) => {
  const form = e.target;
  if (form.closest('dialog')) return;
  e.preventDefault();
  if (form.id === 'appearance-form') {
    saveAppearance(form);
    return;
  }
  if (form.id === 'rule-form' || form.classList.contains('quick-window')) {
    if (current().q.get('view') !== 'stale') saveRule(form);
    return;
  }
  if (form.id === 'period-form') {
    const changes = Object.fromEntries(new FormData(form));
    const msg = $('.form-error', form);
    if (changes.from && changes.to && changes.from >= changes.to) {
      msg.textContent = 'Конец периода должен быть позже начала.';
      return;
    }
    if (form.dataset.tab === 'analytics') {
      const [a, b] = range(new URLSearchParams(changes), true);
      if (!(b > a) || b - a > 31 * 86400000) {
        msg.textContent = 'Выберите период от одной минуты до 31 суток.';
        return;
      }
      changes.period = 'custom';
    }
    replaceQuery({ ...changes, page: '' });
    render(false);
    ($('.history-filters:not([open]) summary') || $('#period-form button'))?.focus();
    toast('Период и фильтры применены');
    return;
  }
  if (form.id === 'login-form') {
    const submit = $('button[type="submit"]', form);
    if (submit.disabled) return;
    const data = new FormData(form);
    submit.disabled = true;
    submit.textContent = 'Входим…';
    form.setAttribute('aria-busy', 'true');
    $('.form-error', form).textContent = '';
    setTimeout(() => {
      if (!form.isConnected) return;
      if (data.get('login') === 'admin' && data.get('password') === 'demo') {
        db.logged = true;
        persist();
        const next = current().q.get('next');
        go(next?.startsWith('/') && !next.startsWith('//') ? next : '/overview', {
          replace: true,
          nav: true,
        });
        toast('Вход выполнен');
      } else {
        submit.disabled = false;
        submit.innerHTML = 'Войти ' + icon('arrow');
        form.removeAttribute('aria-busy');
        $('.form-error', form).textContent = 'Проверьте логин и пароль демодоступа.';
        $('[name="password"]', form).focus();
      }
    }, 500);
  }
});
document.addEventListener('pointerover', (e) => {
  if (e.pointerType === 'touch') return;
  const el = e.target.closest('[data-tip]');
  if (el && el !== tipOwner) showTip(el);
});
document.addEventListener('pointerout', (e) => {
  if (tipOwner && !tipOwner.contains(e.relatedTarget)) hideTip();
});
document.addEventListener('focusin', (e) => {
  const el = e.target.closest('[data-tip]');
  if (el) showTip(el);
  else hideTip();
});
document.addEventListener('focusout', (e) => {
  if (tipOwner === e.target) hideTip();
});
document.addEventListener('keydown', (e) => {
  if (
    e.key === '/' &&
    $('#folder-search') &&
    !e.metaKey &&
    !e.ctrlKey &&
    !e.altKey &&
    !e.target.closest('input,textarea,select,[contenteditable],dialog')
  ) {
    e.preventDefault();
    $('#folder-search').focus();
    return;
  }
  if (e.key === 'Escape') {
    if ($('.popover')) closePop(true);
    hideTip();
  }
});
matchMedia('(min-width:721px)').addEventListener('change', (e) => {
  const filters = $('.history-filters');
  if (filters) filters.open = e.matches;
});
window.addEventListener('resize', () => {
  hideTip();
  closePop();
  placeTab();
});
document.fonts.ready.then(() => {
  placeTab();
  placeNav();
});
new ResizeObserver(placeNav).observe($('.mast nav'));
window.addEventListener('scroll', hideTip, { passive: true });
history.scrollRestoration = 'manual';
if (!history.state?.index)
  history.replaceState({ index: 0, ...history.state }, '', location.hash || '#/overview');
let displayed = location.href;
window.addEventListener('popstate', () => {
  render(true, history.state?.position);
  displayed = location.href;
});
window.addEventListener('hashchange', () => {
  if (displayed !== location.href) {
    render(true, history.state?.position);
    displayed = location.href;
  }
});
document.addEventListener('visibilitychange', () => {
  if (document.hidden) savePos();
});
function refreshResults() {
  const changed = settle();
  if (!changed || !db.logged) return;
  const latest = $('[data-latest]'),
    attempt = $('[data-attempt]'),
    manual = $('[data-manual]'),
    audit = $('[data-audit]');
  if (latest) {
    latest.innerHTML = lastAttempt(ruleBy(latest.dataset.latest));
    latest.classList.add('updated');
  }
  if (attempt) {
    attempt.innerHTML = attemptBody(db.attempts.find((a) => a.id === attempt.dataset.attempt));
    attempt.classList.add('updated');
  }
  if (manual && manual.dataset.manual === changed.folder) {
    const f = folderBy(manual.dataset.manual);
    manual.innerHTML = manualResult(f);
    manual.classList.add('updated');
    const reason = resumeReason(f);
    const el = $('button[aria-disabled]', manual.closest('.panel'));
    if (el) {
      el.dataset.tip = reason;
    }
    const factsPanel = $('.observation-panel .facts');
    if (factsPanel) {
      const values = $$('dd', factsPanel);
      values[1].textContent =
        f.paused === null ? 'Неизвестна' : f.paused ? 'Включена' : 'Выключена';
      values[3].textContent = time(f.observed);
    }
    const status = $('[data-folder-status]');
    if (status) status.innerHTML = folderStatus(f);
    const hint = $('.folder-commands .hint');
    if (hint) hint.textContent = reason || 'Папка на паузе. Доступно ручное возобновление.';
  }
  if (audit) audit.innerHTML = auditTable(current().parts[1], current().q);
  const { sec } = current();
  if (sec === 'overview' && !current().q.has('view')) {
    const temp = document.createElement('div');
    temp.innerHTML = overview();
    if (featuredFolder().id === changed.folder) {
      const panel = $('.folder-well', main),
        next = $('.folder-well', temp);
      if (panel && next) {
        $('.folder-state', panel).innerHTML = $('.folder-state', next).innerHTML;
        $('.folder-bottom dl', panel).innerHTML = $('.folder-bottom dl', next).innerHTML;
      }
    }
    const row = $('[data-kind="command"]', main),
      next = $('[data-kind="command"]', temp);
    if (row && next) {
      row.cells[3].innerHTML = next.cells[3].innerHTML;
      row.cells[3].classList.add('updated');
    }
  }
  if (sec === 'analytics' && $('.metric-grid')) {
    const temp = document.createElement('div');
    temp.innerHTML = analytics(current().q);
    $('.metric-grid').replaceChildren(...$('.metric-grid', temp).children);
    $('.metric-grid').classList.add('updated');
  }
  if (sec === 'folders' && !manual) {
    $$('[data-fresh]').forEach((cell) => {
      cell.innerHTML = folderStatus(folderBy(cell.dataset.fresh));
    });
  }
  if (manual && !resumeReason(folderBy(manual.dataset.manual))) {
    render(false);
  }
  toast(
    changed.status === 'pending'
      ? 'Исполнитель начал попытку'
      : 'Проверка завершена · ' + LABEL[changed.status],
  );
}
settle();
render(false);
setInterval(refreshResults, 300);
