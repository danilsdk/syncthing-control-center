import { randomUUID } from "node:crypto";
import { mkdir, rmdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  expect,
  test as base,
  type APIRequestContext,
  type APIResponse,
} from "@playwright/test";
import type { Execution, Folder, Page, Rule, RuleInput, Session } from "../src/types";

async function read<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), `HTTP ${response.status()} from demo stand`).toBe(true);
  return response.json();
}

type RestoreFolder = (api: APIRequestContext) => Promise<void>;

const test = base.extend<{ restoreFolder: (restore: RestoreFolder) => void }>({
  restoreFolder: [
    async ({ page }, use) => {
      const folderId = process.env.B4_LIVE_FOLDER_ID?.toLowerCase();
      if (!folderId || !/^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/.test(folderId)) {
        throw new Error("B4_LIVE_FOLDER_ID must identify a dedicated demo folder");
      }
      const lock = join(tmpdir(), `scc-b4-live-${folderId}`);
      await mkdir(lock);
      let restore: RestoreFolder | undefined;
      try {
        await use((callback) => {
          restore = callback;
        });
        await restore?.(page.request);
      } finally {
        await rmdir(lock);
      }
    },
    { timeout: 195_000 },
  ],
});

test("live stand: a browser rule pauses Syncthing, is confirmed in history and enters analytics", async ({
  page,
  restoreFolder,
}) => {
  test.setTimeout(210_000);
  const {
    B4_LIVE_URL: url,
    B4_LIVE_NODE_ID: nodeId,
    B4_LIVE_FOLDER_ID: folderId,
    B4_LIVE_LOGIN: login,
    B4_LIVE_PASSWORD: password,
  } = process.env;
  if (!url || !nodeId || !folderId || !login || !password) {
    throw new Error(
      "Provide the loopback stand, dedicated demo IDs and administrator credentials",
    );
  }
  const origin = new URL(url).origin;
  const api = page.request;
  const rules = async () =>
    read<Page<Rule>>(
      await api.get("/api/rules", {
        params: { folder_id: folderId },
        timeout: 10_000,
      }),
    );
  const folder = async () => {
    const data = await read<Page<Folder>>(
      await api.get(`/api/nodes/${nodeId}/folders`, { timeout: 10_000 }),
    );
    return data.items.find((item) => item.id === folderId);
  };

  await page.goto("/#/rules");
  await page.getByLabel("Логин", { exact: true }).fill(login);
  await page.getByLabel("Пароль", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  const chooser = page.getByRole("combobox", { name: "Папка", exact: true });
  await expect(chooser).toBeVisible();
  expect(await folder()).toMatchObject({
    actual_paused: false,
    is_stale: false,
    present: true,
  });
  expect((await rules()).total).toBe(0);
  const session = await read<Session>(await api.get("/api/auth/me"));
  const headers = { Origin: origin, "X-CSRF-Token": session.csrf_token };

  // The permitted window is several hours away even when the test crosses midnight.
  const future = (hours: number) =>
    new Date(Date.now() + hours * 3_600_000).toISOString().slice(11, 16);
  const start = future(4),
    end = future(5);
  await chooser.selectOption(folderId);
  await page.getByLabel("Часовой пояс", { exact: true }).fill("UTC");
  await page.getByLabel("Начало окна", { exact: true }).fill(start);
  await page.getByLabel("Конец окна", { exact: true }).fill(end);
  for (const day of ["Сб", "Вс"]) {
    await page.getByRole("button", { name: day, exact: true }).click();
  }

  const createUrl = new URL("/api/rules", origin).href;
  let submitted: RuleInput | undefined;
  page.on("request", (request) => {
    if (request.url() === createUrl && request.method() === "POST") {
      submitted = request.postDataJSON();
    }
  });
  const creation = page
    .waitForResponse(
      (response) =>
        response.url() === createUrl && response.request().method() === "POST",
      { timeout: 15_000 },
    )
    .catch(() => undefined);
  // Keep the response available for teardown even if the click or an assertion fails.
  restoreFolder(async (api) => {
    if (!submitted) return;
    const response = await creation;
    const status = response?.status();
    if (status && status >= 400 && status < 500 && status !== 408) return;
    let created: Rule | undefined;
    if (status === 201) {
      created = await response?.json().catch(() => undefined);
    }
    if (!created) {
      // This run owns the disposable folder and verified its empty baseline.
      await expect
        .poll(
          async () => {
            const { items, total } = await rules();
            if (total === 1) created = items[0];
            return total;
          },
          { timeout: 25_000, message: "Resolve the outcome of the demo rule POST" },
        )
        .toBe(1);
      expect(created).toMatchObject({
        ...submitted,
        days_of_week: [...submitted.days_of_week].sort((a, b) => a - b),
        version: 1,
      });
    }
    if (!created) throw new Error("Missing created demo rule");

    const removed = await api.delete(`/api/rules/${created.id}`, {
      params: { expected_version: created.version },
      headers,
      timeout: 10_000,
    });
    expect(removed.status()).toBe(204);
    await expect
      .poll(
        async () => {
          const response = await api.get("/api/executions", {
            params: { folder_id: folderId, status: "pending" },
            timeout: 10_000,
          });
          return (await read<Page<Execution>>(response)).total;
        },
        { timeout: 45_000 },
      )
      .toBe(0);
    const resume = await api.post("/api/executions/resume", {
      data: { folder_id: folderId, idempotency_key: randomUUID() },
      headers,
      timeout: 10_000,
    });
    expect(resume.status()).toBe(202);
    await expect.poll(folder, { timeout: 75_000 }).toMatchObject({
      actual_paused: false,
      is_stale: false,
    });
  });

  const [response] = await Promise.all([
    creation,
    page.getByRole("button", { name: "Создать правило", exact: true }).click(),
  ]);
  if (!response) throw new Error("The demo rule POST response did not arrive");
  expect(response.status()).toBe(201);
  const rule: Rule = await response.json();
  expect(rule).toMatchObject({
    folder_id: folderId,
    start_time: start,
    end_time: end,
    enabled: true,
  });
  await expect(
    page.getByRole("button", {
      name: "Отключить расписание",
      exact: true,
    }),
  ).toBeVisible();

  let confirmedExecutionId: string | undefined;
  await expect
    .poll(
      async () => {
        const history = await read<Page<Execution>>(
          await api.get("/api/executions", {
            params: {
              folder_id: folderId,
              source: "rule",
              status: "applied",
            },
          }),
        );
        confirmedExecutionId = history.items.find(
          (attempt) =>
            attempt.rule_id === rule.id &&
            attempt.desired_paused &&
            attempt.observed_paused === true &&
            !!attempt.verified_at,
        )?.id;
        return confirmedExecutionId;
      },
      { timeout: 75_000 },
    )
    .toBeDefined();
  if (!confirmedExecutionId) throw new Error("Missing confirmed pause ID");
  await expect.poll(folder, { timeout: 45_000 }).toMatchObject({
    actual_paused: true,
    is_stale: false,
  });
  await page.getByRole("button", { name: "История", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Узел", exact: true })
    .selectOption(nodeId);
  await page
    .getByRole("combobox", { name: "Результат", exact: true })
    .selectOption("applied");
  await expect(
    page.getByRole("row").filter({
      has: page.getByText(confirmedExecutionId.slice(0, 8), { exact: true }),
    }),
  ).toContainText("Подтверждено");
  await page.getByRole("button", { name: "Аналитика", exact: true }).click();
  await page
    .getByRole("combobox", { name: "Узел", exact: true })
    .selectOption(nodeId);
  await page
    .getByRole("combobox", { name: "Источник команд" })
    .selectOption("rule");
  await page.getByRole("button", { name: "Применить", exact: true }).click();
  await expect(
    page.getByRole("region", { name: "Доля подтверждённых команд" }),
  ).toContainText(/[1-9]\d* подтверждено/);
  await expect(
    page.getByRole("region", { name: "Доля успешных проверок API" }),
  ).toContainText(/из [1-9]\d* проверок/);
  await expect(
    page.getByRole("table", { name: "Дневные показатели UTC" }),
  ).toBeVisible();
});
