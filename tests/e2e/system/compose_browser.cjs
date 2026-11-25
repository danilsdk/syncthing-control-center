const assert = require("node:assert/strict");
const { existsSync, readFileSync } = require("node:fs");
const { createRequire } = require("node:module");
const { resolve } = require("node:path");

const frontendRequire = createRequire(resolve(__dirname, "../../../frontend/package.json"));
const { chromium } = frontendRequire("playwright");
const { expect } = frontendRequire("@playwright/test");
let phase = "chromium_executable";

async function verify({ origin, password, folderId, nodeId, pause, resume }) {
  assert.ok(existsSync(chromium.executablePath()));
  phase = "launch";
  const browser = await chromium.launch({ channel: "chromium" });
  try {
    const context = await browser.newContext({ serviceWorkers: "block" });
    const page = await context.newPage();
    page.setDefaultTimeout(10_000);
    let offOrigin = 0;
    page.on("request", (request) => {
      if (new URL(request.url()).origin !== origin) offOrigin += 1;
    });

    const button = (name) => page.getByRole("button", { name, exact: true });
    const link = (name) => page.getByRole("link", { name, exact: true });
    const heading = (name) => page.getByRole("heading", { name, exact: true });
    const label = (name) => page.getByLabel(name, { exact: true });

    phase = "readiness_and_login";
    const ready = await page.request.get(origin + "/health/ready");
    assert.equal(ready.status(), 200);
    assert.deepEqual(await ready.json(), { status: "ok" });
    const fontLicense = await page.request.get(origin + "/licenses/Inter-OFL.txt");
    assert.equal(fontLicense.status(), 200);
    assert.match(await fontLicense.text(), /SIL OPEN FONT LICENSE/);
    const home = await page.goto(origin + "/#/rules");
    assert.equal(home.status(), 200);
    await expect(heading("Войти в систему")).toBeVisible();
    assert.equal((await page.request.get(origin + "/api/rules")).status(), 401);
    await label("Логин").fill("admin");
    await label("Пароль").fill(password);
    await button("Войти").click();
    await expect(heading("Правила")).toBeVisible();
    const session = (await context.cookies(origin)).find((cookie) => cookie.name === "scc_session");
    assert.ok(session?.httpOnly);
    assert.equal(session.sameSite, "Lax");

    phase = "proxy_preserves_write_guards";
    const auth = await page.request.get(origin + "/api/auth/me");
    assert.equal(auth.status(), 200);
    const { csrf_token } = await auth.json();
    assert.ok(csrf_token);
    for (const [headers, errorCode] of [
      [{ Origin: "http://localhost:1", "X-CSRF-Token": csrf_token }, "origin_forbidden"],
      [{ Origin: origin }, "csrf_invalid"],
    ]) {
      const rejected = await page.request.post(origin + "/api/rules", { headers, data: {} });
      assert.equal(rejected.status(), 403);
      assert.equal((await rejected.json()).error.code, errorCode);
    }

    async function saveRule(method, path, buttonName) {
      const [response] = await Promise.all([
        page.waitForResponse((item) => (
          item.url() === origin + path && item.request().method() === method
        )),
        button(buttonName).click(),
      ]);
      assert.equal(response.status(), method === "POST" ? 201 : 200);
      return response.json();
    }

    async function confirmExecution(rule, paused) {
      const params = new URLSearchParams({ folder_id: folderId, source: "rule", status: "applied" });
      let execution;
      await expect.poll(async () => {
        const response = await page.request.get(origin + "/api/executions?" + params);
        assert.equal(response.status(), 200);
        execution = (await response.json()).items.find((row) => (
          row.rule_id === rule.id && row.rule_version === rule.version &&
          row.desired_paused === paused && row.observed_paused === paused &&
          row.verified_at && row.node_id === nodeId
        ));
        return Boolean(execution);
      }, { timeout: 45_000 }).toBe(true);
      return execution.id;
    }

    async function fillWindow(window) {
      await label("Начало окна").fill(window.start_time);
      await label("Конец окна").fill(window.end_time);
    }

    phase = "select_demo_folder";
    const folder = page.getByRole("combobox", { name: "Папка", exact: true });
    await expect(folder.locator("option")).toHaveCount(5);
    await folder.selectOption(folderId);
    phase = "fill_pause_window";
    await label("Часовой пояс").fill("UTC");
    for (const day of ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]) {
      if (await button(day).getAttribute("aria-pressed") === "false") await button(day).click();
    }
    await fillWindow(pause);
    phase = "create_browser_rule";
    const pausedRule = await saveRule("POST", "/api/rules", "Создать правило");
    phase = "rule_pauses_source";
    const pauseExecution = await confirmExecution(pausedRule, true);

    phase = "update_browser_rule";
    await page.reload();
    await link("Изменить").click();
    await fillWindow(resume);
    const rulePath = "/api/rules/" + pausedRule.id;
    const resumedRule = await saveRule("PATCH", rulePath, "Сохранить изменения");
    assert.equal(resumedRule.version, pausedRule.version + 1);
    phase = "updated_rule_resumes_source";
    const resumeExecution = await confirmExecution(resumedRule, false);

    phase = "browser_history";
    await link("История").click();
    const commands = page.getByRole("table", { name: "Попытки команд" });
    for (const [id, goal] of [[pauseExecution, "приостановить"], [resumeExecution, "возобновить"]]) {
      const row = commands.getByRole("row").filter({
        has: page.locator(`a[href="#/history/commands/${id}"]`),
      });
      await expect(row).toContainText("Цель: " + goal);
      await expect(row).toContainText("Подтверждено");
      await expect(row).toContainText("demo-a");
    }
    await link("События Syncthing").click();
    await page.getByRole("combobox", { name: "Узел", exact: true }).selectOption(nodeId);
    const events = page.getByRole("table", { name: "События Syncthing" });
    await expect(events.getByRole("row").filter({ hasText: "demo-a" }).first()).toBeVisible();

    phase = "disable_and_logout";
    await link("Правила").click();
    const disabled = await saveRule("PATCH", rulePath, "Отключить расписание");
    assert.equal(disabled.enabled, false);
    await button("Выйти").click();
    await expect(heading("Войти в систему")).toBeVisible();
    assert.equal((await page.request.get(origin + "/api/events")).status(), 401);
    assert.ok(!(await context.cookies(origin)).some((cookie) => cookie.name === "scc_session"));
    assert.equal(offOrigin, 0);
    return { chromium: browser.version(), pause_execution: pauseExecution, resume_execution: resumeExecution };
  } finally {
    await browser.close();
  }
}

verify(JSON.parse(readFileSync(0, "utf8"))).then(
  (result) => process.stdout.write(JSON.stringify(result)),
  (error) => {
    // Do not emit request bodies, credentials, traces or Playwright call logs.
    process.stderr.write(`Browser scenario failed at ${phase} (${error.name})\n`);
    process.exitCode = 1;
  },
);
