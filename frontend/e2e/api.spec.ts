import { expect, test } from "@playwright/test";

test("real API: create rule, observe confirmed result and event, disable, logout", async ({
  page,
}) => {
  await page.goto("/#/rules");
  await page.getByLabel("Логин", { exact: true }).fill("admin");
  await page
    .getByLabel("Пароль", { exact: true })
    .fill("public-browser-test-password");
  await page.getByRole("button", { name: "Войти", exact: true }).click();
  const selector = page.getByRole("combobox", { name: "Папка", exact: true });
  await expect(selector.locator("option")).toHaveCount(3);
  await selector.selectOption({ index: 1 });
  await page.getByLabel("Часовой пояс", { exact: true }).fill("UTC");
  await page.getByLabel("Начало окна", { exact: true }).fill("00:00");
  await page.getByLabel("Конец окна", { exact: true }).fill("23:59");
  for (const day of ["Сб", "Вс"])
    await page.getByRole("button", { name: day, exact: true }).click();
  await page
    .getByRole("button", { name: "Создать правило", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Отключить расписание", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Отключить расписание", exact: true }),
  ).toBeVisible();
  await page.getByRole("link", { name: "История", exact: true }).click();
  await expect(async () => {
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(page.getByRole("table")).toContainText("Подтверждено");
  }).toPass({ timeout: 15_000 });
  await expect(page.getByRole("table")).toContainText("Цель: возобновить");
  await page.screenshot({
    path: "test-results/real-api-history.png",
    fullPage: true,
  });
  await page.getByRole("link", { name: "События Syncthing" }).click();
  const events = page.getByRole("table", { name: "События Syncthing" });
  await expect(events).toContainText("Обработка элемента завершена");
  await expect(events).toContainText("Demo documents");
  await expect(events).toContainText("E2E A");
  await page.getByRole("combobox", { name: "Узел", exact: true }).selectOption({
    label: "E2E B",
  });
  await expect(page.getByText("Событий пока нет")).toBeVisible();
  await page.getByRole("link", { name: "Аналитика", exact: true }).click();
  const commands = page.getByRole("region", {
    name: "Подтверждение команд",
  });
  const probes = page.getByRole("region", {
    name: "Наблюдаемая доступность",
  });
  await expect(commands).toContainText(/100\s*%/);
  await expect(probes).toContainText("2 из 3 проверок API");
  await expect(
    page.getByRole("table", { name: "Дневные показатели UTC" }),
  ).toBeVisible();
  await page.screenshot({
    path: "test-results/analytics-desktop.png",
    fullPage: true,
  });
  await page.getByText("Фильтры аналитики", { exact: true }).click();
  await page
    .getByRole("combobox", { name: "Источник команд" })
    .selectOption("manual");
  await expect(commands).toContainText("Нет данных");
  await expect(probes).toContainText("2 из 3 проверок API");
  await page
    .getByRole("combobox", { name: "Узел", exact: true })
    .selectOption({ label: "E2E B" });
  await expect(probes).toContainText("Нет данных");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page.getByRole("table", { name: "Дневные показатели UTC" }),
  ).toBeVisible();
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await page.screenshot({
    path: "test-results/analytics-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.getByRole("link", { name: "Правила", exact: true }).click();
  await page
    .getByRole("button", { name: "Отключить расписание", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Включить расписание", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Войти в систему" }),
  ).toBeVisible();
  const response = await page.request.get("/api/rules");
  expect(response.status()).toBe(401);
  expect((await page.request.get("/api/events")).status()).toBe(401);
  const analytics = await page.request.get("/api/analytics/summary", {
    params: { from: "2026-11-19T00:00:00Z", to: "2026-11-20T00:00:00Z" },
  });
  expect(analytics.status()).toBe(401);
});
