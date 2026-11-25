import { test, expect, type Page } from "@playwright/test";
import type { SyncEvent } from "../src/types";
const nodeId = "11111111-1111-4111-8111-111111111111",
  folderId = "22222222-2222-4222-8222-222222222222",
  ruleId = "33333333-3333-4333-8333-333333333333";
const now = new Date().toISOString();
const node = {
  id: nodeId,
  display_name: "Тестовый узел A",
  enabled: true,
  api_status: "ok",
  is_stale: false,
  syncthing_device_id: "TEST-DEVICE-A",
  last_checked_at: now,
  last_seen_at: now,
  created_at: now,
  updated_at: now,
};
const folder = {
  id: folderId,
  node_id: nodeId,
  syncthing_folder_id: "shared",
  label: "Демонстрационные документы",
  present: true,
  actual_paused: true,
  config_observed_at: now,
  runtime_state: "idle",
  runtime_observed_at: now,
  need_bytes: 0,
  pull_errors: 0,
  desired_paused: null,
  active_rule_id: null,
  is_stale: false,
};
const rule = {
  id: ruleId,
  folder_id: folderId,
  timezone: "Europe/Moscow",
  days_of_week: [1, 2, 3, 4, 5],
  start_time: "09:00",
  end_time: "18:00",
  enabled: true,
  version: 1,
  created_at: now,
  updated_at: now,
};
const execution = {
  id: "44444444-4444-4444-8444-444444444444",
  node_id: nodeId,
  folder_id: folderId,
  source: "manual",
  rule_id: null,
  desired_paused: false,
  observed_paused: null,
  restart_required: null,
  status: "pending",
  created_at: now,
  completed_at: null,
  verified_at: null,
  error_code: null,
};
async function backend(page: Page) {
  const state = {
    loggedIn: true,
    sessionNow: () => Date.now(),
    rules: [] as (typeof rule)[],
    stale: false,
    paused: true,
    desiredPaused: false,
    mutationStatus: 200,
    logoutStatus: 204,
    history: [] as object[],
    events: [] as SyncEvent[],
    eventsStatus: 200,
    requests: [] as string[],
    writes: [] as object[],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      path = url.pathname,
      method = request.method();
    state.requests.push(`${method} ${url.pathname}${url.search}`);
    const send = (body: unknown, status = 200) =>
      route.fulfill({
        status,
        contentType: "application/json",
        body: status === 204 ? "" : JSON.stringify(body),
      });
    const fail = (status: number, code: string) =>
      send(
        { error: { code, message: "raw error must not be displayed" } },
        status,
      );
    const pageOf = (items: object[]) => {
      const limit = Number(url.searchParams.get("limit") ?? 20),
        offset = Number(url.searchParams.get("offset") ?? 0);
      return {
        items: items.slice(offset, offset + limit),
        total: items.length,
        limit,
        offset,
      };
    };
    if (path === "/api/auth/login") {
      state.loggedIn = true;
      return send({
        user: { id: "test-admin", login: "admin" },
        csrf_token: "public-test-token",
        expires_at: new Date(state.sessionNow() + 3_600_000).toISOString(),
      });
    }
    if (!state.loggedIn) return fail(401, "authentication_required");
    if (path === "/api/auth/me")
      return send({
        user: { id: "test-admin", login: "admin" },
        csrf_token: "public-test-token",
        expires_at: new Date(state.sessionNow() + 3_600_000).toISOString(),
      });
    if (path === "/api/auth/logout") {
      if (state.logoutStatus === 204) state.loggedIn = false;
      return state.logoutStatus === 204
        ? send(null, 204)
        : fail(503, "service_unavailable");
    }
    if (path === "/api/nodes") return send(pageOf([node]));
    if (path.endsWith("/folders"))
      return send(
        pageOf([
          {
            ...folder,
            actual_paused: state.paused,
            is_stale: state.stale,
            desired_paused: state.rules.some((item) => item.enabled)
              ? state.desiredPaused
              : null,
            active_rule_id:
              state.rules.find((item) => item.enabled)?.id ?? null,
          },
        ]),
      );
    if (path === "/api/rules" && method === "GET")
      return send(pageOf(state.rules));
    if (path.startsWith("/api/rules") && method !== "GET") {
      state.writes.push(request.postDataJSON());
      if (state.mutationStatus !== 200)
        return fail(
          state.mutationStatus,
          state.mutationStatus === 401
            ? "authentication_required"
            : "version_conflict",
        );
      if (method === "POST") {
        const created = { ...rule, ...request.postDataJSON() };
        state.rules.push(created);
        return send(created, 201);
      }
      if (method === "PATCH") {
        Object.assign(state.rules[0], request.postDataJSON(), {
          version: state.rules[0].version + 1,
        });
        return send(state.rules[0]);
      }
      state.rules = [];
      return send(null, 204);
    }
    if (path === "/api/executions/resume") {
      state.history = [execution];
      return send(execution, 202);
    }
    if (path.startsWith("/api/executions/"))
      return send({
        ...execution,
        status: "applied",
        observed_paused: false,
        restart_required: false,
        completed_at: now,
        verified_at: now,
      });
    if (path === "/api/executions") return send(pageOf(state.history));
    if (path === "/api/events") {
      if (state.eventsStatus !== 200)
        return fail(state.eventsStatus, "service_unavailable");
      const folder = url.searchParams.get("folder_id");
      const node = url.searchParams.get("node_id");
      const events = state.events.filter(
        (event) =>
          (!folder || folder === event.folder_id) &&
          (!node || node === event.node_id),
      );
      return send(pageOf(events));
    }
    return fail(404, "not_found");
  });
  return state;
}

test("restores a session; unavailable analytics does not block the workspace", async ({
  page,
}) => {
  const state = await backend(page);
  await page.goto("/");
  await expect(
    page.getByText("Тестовый узел A", { exact: true }),
  ).toBeVisible();
  expect(state.requests.some((url) => url.includes("/analytics"))).toBe(false);
  await page.getByRole("link", { name: "Аналитика", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Этот раздел API ещё не подключён",
  );
  await page.getByRole("link", { name: "Папки", exact: true }).click();
  await page
    .getByRole("link", { name: /Демонстрационные документы shared/ })
    .click();
  await expect(
    page.getByRole("heading", { name: "Демонстрационные документы" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Возобновить", exact: true }),
  ).toBeEnabled();
  await page.screenshot({
    path: "test-results/folders-desktop.png",
    fullPage: true,
  });
});
test("login, create, conflict recovery and disable use the API and exact versions", async ({
  page,
}) => {
  const state = await backend(page);
  state.loggedIn = false;
  await page.goto("/#/rules");
  await page.getByLabel("Логин", { exact: true }).fill("admin");
  await page.getByLabel("Пароль", { exact: true }).fill("public-test-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Папка", exact: true })
    .selectOption(folderId);
  await page
    .getByRole("button", { name: "Создать правило", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Отключить расписание", exact: true }),
  ).toBeEnabled();
  state.mutationStatus = 409;
  await page
    .getByRole("button", { name: "Отключить расписание", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("Правило уже изменено");
  expect(state.writes.at(-1)).toEqual({ expected_version: 1, enabled: false });
  state.mutationStatus = 200;
  await page
    .getByRole("button", { name: "Перечитать данные", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Отключить расписание", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Включить расписание", exact: true }),
  ).toBeVisible();
  const result = page
    .getByRole("status")
    .filter({ hasText: "Расписание отключено." });
  await expect(result).toContainText(
    "По последнему наблюдению папка на паузе.",
  );
  await result.getByRole("link", { name: "Открыть папки" }).click();
  await expect(
    page.getByRole("button", { name: "Возобновить", exact: true }),
  ).toBeEnabled();
  expect(state.requests).not.toContain("POST /api/executions/resume");
});

for (const { stale, expected } of [
  {
    stale: false,
    expected: "По последнему наблюдению синхронизация разрешена.",
  },
  { stale: true, expected: "Текущее состояние папки неизвестно." },
]) {
  test(`disabling a schedule reports ${stale ? "unknown" : "unpaused"} folder state`, async ({
    page,
  }) => {
    const state = await backend(page);
    state.rules = [{ ...rule }];
    state.paused = false;
    state.desiredPaused = true;
    state.stale = stale;
    await page.goto("/#/folders");
    await expect(
      page.getByText("По расписанию сейчас", { exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Правила", exact: true }).click();
    const currentSchedule = page.locator(".planner-aside section").filter({
      has: page.getByRole("heading", { name: "Сейчас по правилу" }),
    });
    await expect(currentSchedule.getByText("Пауза", { exact: true })).toBeVisible();
    await page
      .getByRole("button", { name: "Отключить расписание", exact: true })
      .click();
    const result = page
      .getByRole("status")
      .filter({ hasText: "Расписание отключено." });
    await expect(result).toContainText(expected);
    expect(state.requests).not.toContain("POST /api/executions/resume");
  });
}
test("auth loss on a mutation returns to login and removes private data", async ({
  page,
}) => {
  const state = await backend(page);
  state.rules = [{ ...rule }];
  state.mutationStatus = 401;
  await page.goto("/#/rules");
  await page
    .getByRole("button", { name: "Отключить расписание", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Войти в систему" }),
  ).toBeVisible();
  await expect(
    page.getByText("Демонстрационные документы", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Сессия завершена. Войдите снова."),
  ).toBeVisible();
});
test("stale folders cannot be resumed; mobile navigation and logout stay accessible", async ({
  page,
}) => {
  const state = await backend(page);
  state.stale = true;
  await page.setViewportSize({ width: 360, height: 800 });
  await page.goto("/#/folders");
  await expect(
    page.getByText("Нет свежих данных", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Возобновить", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Выйти", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/folders-mobile.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "Правила", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Правила", exact: true }),
  ).toBeVisible();
});
test("resume remains pending until a separate GET confirms it", async ({
  page,
}) => {
  await backend(page);
  await page.goto("/#/folders/" + folderId);
  await page.getByRole("button", { name: "Возобновить", exact: true }).click();
  await expect(
    page.getByRole("status", { name: "Результат команды" }),
  ).toContainText("Ожидаем отдельной проверки");
  await expect(
    page.getByRole("status", { name: "Результат команды" }),
  ).toContainText("Подтверждено", {
    timeout: 8000,
  });
});
test("a new pause after navigation gets a new resume attempt", async ({
  page,
}) => {
  const state = await backend(page);
  const first = { ...execution };
  const second = { ...execution, id: "55555555-5555-4555-8555-555555555555" };
  const confirmed = (attempt: typeof execution) => ({
    ...attempt,
    status: "applied",
    observed_paused: false,
    completed_at: now,
    verified_at: now,
  });
  const keys: string[] = [];
  let firstResult: object = first;
  let readSecond = false;
  await page.route(`**/api/executions/${first.id}`, (route) =>
    route.fulfill({ json: firstResult }),
  );
  await page.route(`**/api/executions/${second.id}`, (route) => {
    readSecond = true;
    return route.fulfill({ json: confirmed(second) });
  });
  await page.route("**/api/executions/resume", (route) => {
    const key: string = route.request().postDataJSON().idempotency_key;
    const isFirst = keys.length === 0;
    const isReplay = keys.includes(key);
    keys.push(key);
    if (isReplay) return route.fulfill({ status: 200, json: firstResult });
    return route.fulfill({ status: 202, json: isFirst ? first : second });
  });

  await page.goto("/#/folders/" + folderId);
  const resume = page.getByRole("button", { name: "Возобновить", exact: true });
  const result = page.getByRole("status", { name: "Результат команды" });
  await resume.click();
  await expect(result).toContainText("Ожидаем отдельной проверки");
  await page.getByRole("link", { name: "Обзор", exact: true }).click();
  await expect(result).toHaveCount(0);

  firstResult = confirmed(first);
  state.history = [firstResult];
  state.paused = false;
  await page.getByRole("link", { name: "История", exact: true }).click();
  await expect(page.getByRole("table")).toContainText("Подтверждено");
  state.paused = true;
  await page.getByRole("link", { name: "Папки", exact: true }).click();
  await page.getByRole("button", { name: "Обновить", exact: true }).click();

  await page
    .getByRole("link", { name: /Демонстрационные документы shared/ })
    .click();
  await resume.click();
  await expect.poll(() => keys.length).toBe(2);
  expect(keys[1]).not.toBe(keys[0]);
  await expect(result).toContainText("Ожидаем отдельной проверки");
  await expect(result).toContainText("Подтверждено");
  expect(readSecond).toBe(true);
});
test("history has server pagination and UTC filters", async ({ page }) => {
  const state = await backend(page);
  state.history = Array.from({ length: 21 }, (_, i) => ({
    ...execution,
    id: `history-${i}`,
    status: "unknown",
    error_code: "verification_unavailable",
  }));
  await page.goto("/#/history");
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page.getByText("21–21 из 21")).toBeVisible();
  await page
    .getByRole("combobox", { name: "Результат", exact: true })
    .selectOption("unknown");
  await page.getByLabel("С (UTC)", { exact: true }).fill("2026-11-01T00:00");
  await page
    .getByLabel("До (UTC, не включая)", { exact: true })
    .fill("2026-11-07T00:00");
  await page.getByRole("button", { name: "Применить период" }).click();
  await expect
    .poll(() => state.requests.at(-1))
    .toContain("from=2026-11-01T00%3A00%3A00Z");
  expect(state.requests.some((url) => url.includes("offset=20"))).toBe(true);
  await expect(page.getByRole("table")).not.toContainText("Подтверждено");
});
test("event history shows item activity and gaps with separate filters and error state", async ({
  page,
}) => {
  const state = await backend(page);
  const item: SyncEvent = {
    id: "event-file",
    node_id: nodeId,
    folder_id: folderId,
    kind: "source",
    event_type: "ItemFinished",
    occurred_at: now,
    received_at: now,
    payload_summary: {},
  };
  state.history = [{ ...execution, status: "applied" }];
  state.events = [
    item,
    ...Array.from({ length: 19 }, (_, i) => ({ ...item, id: `event-${i}` })),
    {
      ...item,
      id: "event-gap",
      kind: "gap",
      event_type: "HistoryGap",
      folder_id: null,
      occurred_at: null,
      payload_summary: { from_id: 15, to_id: 18 },
    },
  ];
  await page.goto("/#/history");
  await expect(page.getByRole("table")).toContainText("Подтверждено");
  await page.getByRole("link", { name: "События Syncthing" }).click();
  await expect(page.getByRole("table")).toContainText(
    "Обработка элемента завершена",
  );
  await expect(page.getByRole("table")).toContainText(folder.label);
  await expect(page.getByRole("table")).not.toContainText("Подтверждено");
  await page.getByRole("button", { name: "Далее", exact: true }).click();
  await expect(page.getByRole("table")).toContainText(
    "Пропущены события 15–18",
  );
  await page
    .getByRole("combobox", { name: "Папка", exact: true })
    .selectOption(folderId);
  await expect(page.getByText("1–20 из 20", { exact: true })).toBeVisible();
  expect(state.requests).toContain(
    `GET /api/events?limit=20&offset=0&folder_id=${folderId}`,
  );
  await page.screenshot({
    path: "test-results/events-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 360, height: 780 });
  await expect(
    page.getByRole("link", { name: "События Syncthing" }),
  ).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    360,
  );
  const eventTable = page.getByLabel("Прокручиваемый журнал событий");
  expect(await eventTable.evaluate((table) => table.scrollWidth)).toBe(
    await eventTable.evaluate((table) => table.clientWidth),
  );
  await page.screenshot({
    path: "test-results/events-mobile.png",
    fullPage: true,
  });

  state.eventsStatus = 503;
  await page.getByRole("button", { name: "Обновить", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Сервер временно недоступен",
  );
  await page.getByRole("link", { name: "Команды", exact: true }).click();
  await expect(page.getByRole("table")).toContainText("Подтверждено");
});
test("failed logout is visible and does not claim a terminated server session", async ({
  page,
}) => {
  const state = await backend(page);
  state.logoutStatus = 503;
  await page.goto("/");
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(
    "Сервер временно недоступен",
  );
  await expect(
    page.getByRole("button", { name: "Выйти", exact: true }),
  ).toBeVisible();
  state.logoutStatus = 204;
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Войти в систему" }),
  ).toBeVisible();
});

test("editing preserves the version; deleting needs a cancellable confirmation", async ({
  page,
}) => {
  const state = await backend(page);
  state.rules = [{ ...rule }];
  await page.goto("/#/rules");
  await page.getByRole("link", { name: "Изменить", exact: true }).click();
  await page.getByLabel("Начало окна", { exact: true }).fill("10:00");
  await page
    .getByRole("button", { name: "Сохранить изменения", exact: true })
    .click();
  await expect.poll(() => state.rules[0].start_time).toBe("10:00");
  expect(state.writes.at(-1)).toMatchObject({ expected_version: 1 });
  await page.getByRole("button", { name: "Удалить", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  expect(state.rules).toHaveLength(1);
  await page.getByRole("button", { name: "Удалить", exact: true }).click();
  await page
    .getByRole("button", { name: "Удалить правило", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Удалить правило?" }),
  ).toHaveCount(0);
  await expect(
    page.getByText("Правил пока нет", { exact: true }),
  ).toBeVisible();
});

test("empty weekdays and an equal window are rejected before sending a write", async ({
  page,
}) => {
  const state = await backend(page);
  await page.goto("/#/rules");
  await page
    .getByRole("combobox", { name: "Папка", exact: true })
    .selectOption(folderId);
  for (const day of ["Пн", "Вт", "Ср", "Чт", "Пт"])
    await page.getByRole("button", { name: day, exact: true }).click();
  await page
    .getByRole("button", { name: "Создать правило", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("хотя бы один день");
  await page.getByRole("button", { name: "Пн", exact: true }).click();
  await page.getByLabel("Конец окна", { exact: true }).fill("09:00");
  await page
    .getByRole("button", { name: "Создать правило", exact: true })
    .click();
  await expect(page.getByRole("alert")).toContainText("должны различаться");
  expect(state.writes).toHaveLength(0);
});

test("a stale folder leads to its rule and latest attempt and returns to the filtered row", async ({
  page,
}) => {
  const state = await backend(page);
  state.stale = true;
  state.rules = [rule];
  const target = {
    ...execution,
    source: "rule",
    rule_id: ruleId,
    rule_version: 1,
    status: "unknown",
    error_code: "verification_unavailable",
  };
  const firstPage = Array.from({ length: 100 }, (_, i) => ({
    ...target,
    id: "unrelated-" + i,
    rule_id: "other-rule",
  }));
  state.history = [...firstPage, target];
  const peers = Array.from({ length: 30 }, (_, index) => ({
    ...folder,
    id: "folder-" + index,
    label: "Документы " + index,
    syncthing_folder_id: "docs-" + index,
    is_stale: true,
  }));
  const catalog = [
    ...peers,
    { ...folder, active_rule_id: ruleId, is_stale: true },
  ];
  await page.route("**/api/nodes/*/folders?*", (route) =>
    route.fulfill({
      json: { items: catalog, total: catalog.length, limit: 100, offset: 0 },
    }),
  );
  await page.route("**/api/executions/" + execution.id, (route) =>
    route.fulfill({ json: target }),
  );
  await page.goto("/#/folders");
  await page.getByLabel("Найти папку").fill("документы");
  await page
    .getByRole("combobox", { name: "Наблюдение", exact: true })
    .selectOption("stale");
  const listUrl = page.url();
  const row = page.getByRole("link", {
    name: /Демонстрационные документы shared/,
  });
  await row.scrollIntoViewIfNeeded();
  const scroll = await page.evaluate(() => window.scrollY);
  expect(scroll).toBeGreaterThan(100);
  await row.click();
  await page.getByRole("link", { name: "Открыть правило" }).click();
  await page.getByRole("link", { name: "Открыть попытку" }).click();
  await expect(page).toHaveURL(
    new RegExp("/history/commands/" + execution.id + "$"),
  );
  await expect(
    page.getByText("verification_unavailable", { exact: true }),
  ).toBeVisible();
  expect(
    state.requests.some(
      (r) => r.includes("folder_id=" + folderId) && r.includes("offset=100"),
    ),
  ).toBe(true);
  await page.reload();
  await page.getByRole("link", { name: "К списку папок" }).click();
  await expect(page).toHaveURL(listUrl);
  await expect(row).toBeFocused();
  expect(
    Math.abs((await page.evaluate(() => window.scrollY)) - scroll),
  ).toBeLessThan(3);
  await page
    .getByRole("link", { name: "Имя и значок: Демонстрационные документы" })
    .click();
  await page.getByLabel("Название в панели").fill("Материалы");
  await page.getByLabel("Свой символ или эмодзи").fill("📚");
  await page.getByRole("button", { name: "Сохранить оформление" }).click();
  await page.reload();
  await expect(
    page.getByRole("link", { name: /Материалы shared/ }),
  ).toBeVisible();
  expect(state.writes).toHaveLength(0);
});

test("dragging across midnight creates a draft and saving uses the server version", async ({
  page,
}) => {
  const state = await backend(page);
  state.rules = [{ ...rule, days_of_week: [5] }];
  await page.goto("/#/rules/" + ruleId);
  const edge = page.getByRole("slider", { name: "Конец окна, Пт" });
  const rect = await edge.locator("..").boundingBox();
  const handle = await edge.boundingBox();
  if (!handle || !rect) throw new Error("Недельная шкала не видна");
  await page.mouse.move(
    handle.x + handle.width / 2,
    handle.y + handle.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    rect.x + rect.width / 12,
    handle.y + handle.height / 2,
    { steps: 12 },
  );
  await page.mouse.up();
  await expect(edge).toHaveAttribute("aria-valuetext", "02:00");
  await expect(
    page.locator(".planner-row").filter({ hasText: "Сб" }).getByRole("slider"),
  ).toBeVisible();
  await edge.focus();
  await page.keyboard.press("ArrowRight");
  await expect(edge).toHaveAttribute("aria-valuetext", "02:15");
  expect(state.writes).toHaveLength(0);
  await page.getByRole("button", { name: "Сохранить окно" }).click();
  await expect
    .poll(() => state.writes.at(-1))
    .toMatchObject({
      end_time: "02:15",
      expected_version: 1,
      days_of_week: [5],
    });
  await expect(page.getByText("Сохранено · версия 2")).toBeVisible();
});

test("a conflicting window keeps the draft until the administrator compares versions", async ({
  page,
}) => {
  const state = await backend(page);
  state.rules = [{ ...rule }];
  await page.goto("/#/rules/" + ruleId);
  await page.getByLabel("Конец окна", { exact: true }).fill("21:00");
  state.rules[0].version = 2;
  state.rules[0].start_time = "10:00";
  state.mutationStatus = 409;
  await page.getByRole("button", { name: "Сохранить окно" }).click();
  await expect(page.getByRole("alert")).toContainText("Правило уже изменено");
  await page.getByRole("button", { name: "Перечитать данные" }).click();
  await expect(page.getByText(/На сервере версия 2/)).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Сохранить окно" }),
  ).toBeDisabled();
  await expect(page.getByLabel("Конец окна", { exact: true })).toHaveValue(
    "21:00",
  );
  await expect(page.getByText(/10:00.*18:00/).first()).toBeVisible();
  state.mutationStatus = 200;
  await page
    .getByRole("button", { name: "Сверено, использовать эту версию" })
    .click();
  await page.getByRole("button", { name: "Сохранить окно" }).click();
  await expect
    .poll(() => state.writes.at(-1))
    .toMatchObject({ expected_version: 2, end_time: "21:00" });
});

test("analytics keeps B3 period controls and applies the B4 command source filter", async ({
  page,
}) => {
  await backend(page);
  let span = 0;
  let source: string | null = null;
  const commands = {
    applied: 0,
    failed: 1,
    unknown: 4,
    cancelled: 0,
    pending_created: 0,
    success_ratio: 0,
  };
  const api_checks = {
    successful_checks: 0,
    total_checks: 0,
    success_ratio: null,
  };
  await page.route("**/api/analytics/summary?*", (route) => {
    const q = new URL(route.request().url()).searchParams;
    span = Date.parse(q.get("to")!) - Date.parse(q.get("from")!);
    source = q.get("source");
    return route.fulfill({
      json: {
        from: q.get("from"),
        to: q.get("to"),
        node_id: q.get("node_id"),
        source,
        commands,
        api_checks,
        daily: [{ date: q.get("from")!.slice(0, 10), commands, api_checks }],
      },
    });
  });
  await page.goto("/#/analytics");
  await expect(
    page.getByRole("region", { name: "Подтверждение команд" }).getByText(/^0\s*%$/),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "Наблюдаемая доступность" }).getByText("Нет данных"),
  ).toBeVisible();
  await expect(
    page.getByRole("table", { name: "Дневные показатели UTC" }),
  ).toBeVisible();
  await page.getByText("Фильтры аналитики", { exact: true }).click();
  await page.getByRole("combobox", { name: "Источник команд" }).selectOption("manual");
  await expect.poll(() => source).toBe("manual");
  await page.getByRole("button", { name: "7 дней" }).click();
  await expect.poll(() => span).toBe(7 * 86_400_000);
  await expect(page).toHaveURL(/source=manual/);
  await page.getByLabel("С (UTC)", { exact: true }).fill("2026-01-01T00:00");
  await page.getByLabel("До (UTC, не включая)", { exact: true }).fill("2026-02-02T00:00");
  await page.getByRole("button", { name: "Применить период" }).click();
  await expect(page.getByRole("alert")).toContainText("Максимальный период — 31 сутки.");
  expect(span).toBe(7 * 86_400_000);
});

test("upcoming windows follow real local minutes across clock changes", async ({
  page,
}) => {
  const state = await backend(page);
  const berlin = { ...rule, timezone: "Europe/Berlin", days_of_week: [7] };
  const firstWindow = page.locator(".next-windows li").first();

  const spring = new Date("2027-03-27T12:00:00Z");
  state.sessionNow = () => spring.getTime();
  await page.clock.setFixedTime(spring);
  state.rules = [{ ...berlin, start_time: "02:10", end_time: "02:20" }];
  await page.goto("/#/rules/" + ruleId);
  await expect(firstWindow).toContainText(/4\s+апр/);

  state.rules = [{ ...berlin, start_time: "02:10", end_time: "03:20" }];
  await page.reload();
  await expect(firstWindow).toContainText(/28\s+мар/);

  // At 02:30 on the first occurrence, the second 02:10 is still ahead.
  const autumn = new Date("2027-10-31T00:30:00Z");
  state.sessionNow = () => autumn.getTime();
  await page.clock.setFixedTime(autumn);
  state.rules = [{ ...berlin, start_time: "02:10", end_time: "02:20" }];
  await page.reload();
  await expect(firstWindow).toContainText(/31\s+окт/);
});
