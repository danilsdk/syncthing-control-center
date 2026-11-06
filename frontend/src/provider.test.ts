import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiProvider } from "./provider";
import { MockProvider } from "./mock";
import type { Rule } from "./types";
const session = {
  user: { id: "admin-id", login: "admin" },
  csrf_token: "test-csrf",
  expires_at: "2099-01-01T00:00:00Z",
};
const json = (value: unknown, status = 200) =>
  new Response(JSON.stringify(value), { status });
afterEach(() => vi.unstubAllGlobals());

describe("API boundary", () => {
  it("restores CSRF from the existing HttpOnly session and sends the expected version", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(json({ version: 4 }));
    vi.stubGlobal("fetch", fetch);
    const provider = new ApiProvider();
    await provider.currentSession();
    await provider.setRuleEnabled({ id: "rule-id", version: 3 } as Rule, false);
    const [url, request] = fetch.mock.calls[1];
    expect(url).toBe("/api/rules/rule-id");
    expect(request.credentials).toBe("same-origin");
    expect(request.headers.get("X-CSRF-Token")).toBe("test-csrf");
    expect(JSON.parse(request.body)).toEqual({
      expected_version: 3,
      enabled: false,
    });
  });
  it("reads every registry page instead of silently dropping items after 100", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(
        json({
          items: Array.from({ length: 100 }, (_, id) => ({ id })),
          total: 101,
        }),
      )
      .mockResolvedValueOnce(json({ items: [{ id: 100 }], total: 101 }));
    vi.stubGlobal("fetch", fetch);
    expect(await new ApiProvider().listNodes()).toHaveLength(101);
    expect(fetch.mock.calls[1][0]).toBe("/api/nodes?limit=100&offset=100");
  });
  it("passes history filters and offsets to the backend, including encoded UTC periods", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(json({ items: [], total: 23, limit: 20, offset: 20 }));
    vi.stubGlobal("fetch", fetch);
    await new ApiProvider().listExecutions({
      offset: 20,
      status: "unknown",
      from: "2026-11-01T00:00:00Z",
    });
    const url = new URL(fetch.mock.calls[0][0], "http://localhost");
    expect(url.searchParams.get("offset")).toBe("20");
    expect(url.searchParams.get("status")).toBe("unknown");
    expect(url.searchParams.get("from")).toBe("2026-11-01T00:00:00Z");
  });
  it("keeps the idempotency key after a lost write response", async () => {
    const fetch = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("raw private URL"))
      .mockResolvedValueOnce(json({ id: "execution-id", status: "pending" }));
    vi.stubGlobal("fetch", fetch);
    const provider = new ApiProvider();
    await expect(provider.resume("folder-id")).rejects.toMatchObject({
      code: "network_error",
    });
    await provider.resume("folder-id");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(
      JSON.parse(fetch.mock.calls[1][1].body),
    );
  });
  it("clears CSRF on auth loss and never shows an upstream error body", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(json(session))
      .mockResolvedValueOnce(
        json(
          { error: { code: "authentication_required", message: "SECRET" } },
          401,
        ),
      )
      .mockResolvedValueOnce(json({ version: 4 }));
    vi.stubGlobal("fetch", fetch);
    const provider = new ApiProvider();
    await provider.currentSession();
    await expect(provider.listRules()).rejects.toMatchObject({
      status: 401,
      message: "Сессия завершена. Войдите снова.",
    });
    await provider.setRuleEnabled({ id: "rule-id", version: 3 } as Rule, false);
    expect(fetch.mock.calls[2][1].headers.has("X-CSRF-Token")).toBe(false);
  });
  it("rejects HTML login/proxy pages returned with HTTP 200", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("<html>proxy</html>")),
    );
    await expect(new ApiProvider().currentSession()).rejects.toMatchObject({
      code: "invalid_response",
    });
  });
  it("keeps a version conflict distinct from service unavailability", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValue(json({ error: { code: "version_conflict" } }, 409)),
    );
    await expect(
      new ApiProvider().setRuleEnabled({ id: "r", version: 1 } as Rule, false),
    ).rejects.toMatchObject({ code: "version_conflict", status: 409 });
  });
});
describe("isolated demo provider", () => {
  it("keeps fixtures per instance and preserves unknown observations after disabling a rule", async () => {
    const provider = new MockProvider(),
      other = new MockProvider();
    const rule = (await provider.listRules())[0];
    const disabled = await provider.setRuleEnabled(rule, false);
    expect(disabled.version).toBe(3);
    expect((await other.listRules())[0].enabled).toBe(true);
    const folder = (
      await provider.listFolders((await provider.listNodes())[0].id)
    )[0];
    expect(folder.active_rule_id).toBeNull();
    expect(folder.actual_paused).toBeNull();
    expect(folder.is_stale).toBe(true);
    const attempt = await provider.resume(folder.id);
    expect(attempt.status).toBe("pending");
    expect((await provider.listExecutions()).total).toBe(2);
  });
  it("rejects stale versions and duplicate active rules", async () => {
    const provider = new MockProvider(),
      rule = (await provider.listRules())[0];
    await expect(provider.createRule(rule)).rejects.toMatchObject({
      code: "active_rule_conflict",
    });
    await provider.setRuleEnabled(rule, false);
    await expect(provider.setRuleEnabled(rule, false)).rejects.toMatchObject({
      code: "version_conflict",
    });
  });
});
