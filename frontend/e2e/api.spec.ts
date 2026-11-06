import { expect, test } from "@playwright/test";

test("real A2 auth and B1/B2 API: create, observe confirmed result, disable, logout", async ({
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
  await page.getByLabel("Начало", { exact: true }).fill("00:00");
  await page.getByLabel("Конец", { exact: true }).fill("23:59");
  for (const day of ["Сб", "Вс"])
    await page.getByRole("button", { name: day, exact: true }).click();
  await page
    .getByRole("button", { name: "Создать правило", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Отключить правило", exact: true }),
  ).toBeEnabled();
  await page.reload();
  await expect(
    page.getByRole("button", { name: "Отключить правило", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "История", exact: true }).click();
  await expect(async () => {
    await page.getByRole("button", { name: "Обновить", exact: true }).click();
    await expect(page.getByRole("table")).toContainText("Подтверждено");
  }).toPass({ timeout: 15_000 });
  await expect(page.getByRole("table")).toContainText("Цель: возобновить");
  await page.screenshot({
    path: "test-results/real-api-history.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Правила", exact: true }).click();
  await page
    .getByRole("button", { name: "Отключить правило", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Включить правило", exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Выйти", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Войти в систему" }),
  ).toBeVisible();
  const response = await page.request.get("/api/rules");
  expect(response.status()).toBe(401);
});
