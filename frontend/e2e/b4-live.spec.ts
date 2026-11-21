import { randomUUID } from "node:crypto";
import { expect, test, type APIResponse } from "@playwright/test";
import type { Execution, Folder, Page, Rule, Session } from "../src/types";

async function read<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), `HTTP ${response.status()} from demo stand`).toBe(true);
  return response.json();
}

test("live stand: a browser rule pauses Syncthing, is confirmed in history and enters analytics", async ({
  page,
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
      await api.get("/api/rules", { params: { folder_id: folderId } }),
    );
  const folder = async () => {
    const data = await read<Page<Folder>>(
      await api.get(`/api/nodes/${nodeId}/folders`),
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

  try {
    await page
      .getByRole("button", { name: "Создать правило", exact: true })
      .click();
    const disable = page.getByRole("button", {
      name: "Отключить расписание",
      exact: true,
    });
    await expect(disable).toBeVisible();
    const [rule] = (await rules()).items;
    expect(rule).toMatchObject({
      start_time: start,
      end_time: end,
      enabled: true,
    });

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
  } finally {
    // Restore only the rule created in this dedicated folder and its initial pause state.
    const created = (await rules()).items.find(
      (item) =>
        item.timezone === "UTC" &&
        item.start_time === start &&
        item.end_time === end,
    );
    if (created) {
      const removed = await api.delete(`/api/rules/${created.id}`, {
        params: { expected_version: created.version },
        headers,
      });
      expect(removed.status()).toBe(204);
      await expect
        .poll(
          async () => {
            const response = await api.get("/api/executions", {
              params: { folder_id: folderId, status: "pending" },
            });
            return (await read<Page<Execution>>(response)).total;
          },
          { timeout: 45_000 },
        )
        .toBe(0);
      const resume = await api.post("/api/executions/resume", {
        data: { folder_id: folderId, idempotency_key: randomUUID() },
        headers,
      });
      expect(resume.status()).toBe(202);
      await expect.poll(folder, { timeout: 75_000 }).toMatchObject({
        actual_paused: false,
        is_stale: false,
      });
    }
  }
});
