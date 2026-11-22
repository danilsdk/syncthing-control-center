// Данные прототипа существуют только в этой вкладке.
'use strict';
const IDS = {
  node: '11111111-1111-4111-8111-111111111111',
  folder: '22222222-2222-4222-8222-222222222222',
  rule: '33333333-3333-4333-8333-333333333333',
  command: '44444444-4444-4444-8444-444444444444',
  event: '66666666-6666-4666-8666-666666666666',
};
const DAYS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
const INITIAL = Date.parse('2026-11-22T15:32:50Z');
const LABEL = {
  unknown: 'Неизвестно',
  applied: 'Подтверждено',
  failed: 'Отказ',
  pending: 'Ожидает проверки',
  cancelled: 'Отменено',
};
const MARK = { unknown: '?', applied: '✓', failed: '!', pending: '·', cancelled: '−' };
const STORE = 'scc-b8-prototype-v1';
function seed(extended = false) {
  const nodes = [
    { id: IDS.node, name: 'DEMO-A', label: 'Демонстрационный узел A', available: true },
  ];
  const folders = [
    {
      id: IDS.folder,
      node: IDS.node,
      key: 'shared',
      name: 'Рабочие документы',
      paused: null,
      observed: null,
      runtime: null,
      runtimeAt: null,
    },
  ];
  const rules = [
    {
      id: IDS.rule,
      folder: IDS.folder,
      days: [1, 2, 3, 4, 5],
      start: '09:00',
      end: '18:00',
      zone: 'Europe/Moscow',
      enabled: true,
      version: 2,
    },
  ];
  const attempts = [
    {
      id: IDS.command,
      folder: IDS.folder,
      rule: IDS.rule,
      version: 2,
      paused: true,
      status: 'unknown',
      created: INITIAL - 50000,
      sent: null,
      completed: INITIAL - 42000,
      verified: null,
      code: 'verification_unavailable',
    },
  ];
  const events = [{ id: IDS.event, folder: IDS.folder, at: INITIAL, type: 'ItemFinished' }];
  const probes = [];
  if (extended) {
    nodes.push({ id: 'node-b', name: 'DEMO-B', label: 'Демонстрационный узел B', available: true });
    folders.push(
      {
        id: 'folder-archive',
        node: 'node-b',
        name: 'Архив',
        key: 'shared',
        paused: true,
        observed: INITIAL - 5000,
        runtimeAt: INITIAL - 12000,
        runtime: 'idle',
      },
      {
        id: 'folder-inbox',
        node: 'node-b',
        name: 'Входящие',
        key: 'inbox',
        paused: false,
        observed: INITIAL - 120000,
        runtimeAt: INITIAL - 150000,
        runtime: 'idle',
      },
    );
    for (let i = 1; i <= 6; i++) {
      const at = INITIAL - i * 86400000;
      attempts.push({
        id: 'archive-' + i,
        folder: 'folder-archive',
        rule: 'old-rule',
        version: 1,
        paused: i % 2 === 0,
        status: 'applied',
        created: at,
        sent: at,
        verified: at + 1000,
        completed: at + 1000,
      });
      events.push({
        id: 'archive-event-' + i,
        folder: 'folder-archive',
        at: at + 3000,
        type: 'ItemFinished',
      });
    }
    for (let i = 0; i < 7; i++)
      probes.push({ node: 'node-b', at: INITIAL - i * 3600000, ok: i !== 3 });
    attempts.push({
      id: 'archive-pause',
      folder: 'folder-archive',
      rule: 'old-rule',
      version: 1,
      paused: true,
      status: 'applied',
      created: INITIAL - 3600000,
      sent: INITIAL - 3600000,
      verified: INITIAL - 3598000,
      completed: INITIAL - 3598000,
    });
  }
  return {
    nodes,
    folders,
    rules,
    attempts,
    events,
    probes,
    clock: INITIAL,
    extended,
    logged: true,
  };
}
function loadModel() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE));
    if (saved?.folders && saved?.attempts) return saved;
  } catch {}
  return seed();
}
let db = loadModel();
function persist() {
  try {
    sessionStorage.setItem(STORE, JSON.stringify(db));
  } catch {}
}
const folderBy = (id) => db.folders.find((f) => f.id === id);
const nodeBy = (id) => db.nodes.find((n) => n.id === id);
const ruleBy = (id) => db.rules.find((r) => r.id === id);
const activeFor = (id) => db.rules.find((r) => r.folder === id && r.enabled);
const stamp = (ms) => new Date(ms).toISOString().slice(0, 19).replace('T', ' ');
const time = (ms) => (ms == null ? 'Нет наблюдения' : stamp(ms).slice(11) + ' UTC');
const dayText = (days) =>
  days.join() === '1,2,3,4,5'
    ? 'Пн–Пт'
    : days.length === 7
      ? 'Ежедневно'
      : days.map((d) => DAYS[d - 1]).join(', ');
function currentPause(r) {
  if (!r?.enabled) return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-GB', {
      timeZone: r.zone,
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(db.clock)
      .map((p) => [p.type, p.value]),
  );
  const day = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].indexOf(parts.weekday) + 1,
    hm = parts.hour + ':' + parts.minute;
  const today = r.days.includes(day),
    yesterday = r.days.includes(day === 1 ? 7 : day - 1);
  return !(r.end > r.start
    ? today && hm >= r.start && hm < r.end
    : (today && hm >= r.start) || (yesterday && hm < r.end));
}
const desired = (r) =>
  currentPause(r) === null
    ? 'Без расписания'
    : currentPause(r)
      ? 'Пауза'
      : 'Синхронизация разрешена';
const isFresh = (f) =>
  f.observed !== null && db.clock - f.observed <= 60000 && nodeBy(f.node).available;
function resumeReason(f) {
  if (db.attempts.some((a) => a.folder === f.id && a.status === 'pending'))
    return 'Дождитесь результата текущей команды.';
  if (activeFor(f.id))
    return isFresh(f)
      ? 'Папкой управляет включённое правило.'
      : 'Отключите правило и дождитесь свежего наблюдения паузы.';
  if (!isFresh(f)) return 'Нужно свежее наблюдение фактической паузы.';
  if (!f.paused) return 'Синхронизация уже разрешена.';
  return '';
}
function enqueue(f, r) {
  const paused = r ? currentPause(r) : false;
  const busy = db.attempts.some((item) => item.status === 'pending');
  if (!busy && isFresh(f) && f.paused === paused) return null;
  db.clock += 1000;
  const a = {
    id: crypto.randomUUID(),
    folder: f.id,
    rule: r?.id ?? null,
    version: r?.version ?? null,
    paused,
    status: 'pending',
    created: db.clock,
    sent: busy ? null : db.clock,
    verified: null,
    completed: null,
    due: Date.now() + 2400,
  };
  db.attempts.unshift(a);
  persist();
  return a;
}
function settle() {
  // Сначала завершается отправленная команда. Следующее намерение проверяется перед отправкой.
  const queued = db.attempts.filter((item) => item.status === 'pending');
  const a = queued.find((item) => item.sent) || queued.at(-1);
  if (!a || Date.now() < a.due) return null;
  const f = folderBy(a.folder);
  const rule = activeFor(f.id);
  const current = a.rule
    ? rule?.id === a.rule && rule.version === a.version && currentPause(rule) === a.paused
    : !rule;
  db.clock += 1000;
  if (!a.sent && current && !(isFresh(f) && f.paused === a.paused)) {
    a.sent = db.clock;
    a.due = Date.now() + 2400;
  } else {
    a.completed = db.clock;
    if (!a.sent) {
      a.status = 'cancelled';
      a.code = current ? 'already_satisfied' : 'rule_changed_before_send';
    } else if (current && isFresh(f)) {
      a.status = 'applied';
      a.verified = db.clock;
      f.observed = db.clock;
      f.paused = a.paused;
    } else {
      a.status = 'unknown';
      a.code = current ? 'verification_unavailable' : 'rule_changed_after_send';
    }
  }
  persist();
  return a;
}
