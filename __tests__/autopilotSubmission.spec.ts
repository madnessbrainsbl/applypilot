// @vitest-environment node
import { NextRequest } from "next/server";
import { EventEmitter } from "node:events";
let POST: typeof import("@/app/api/autopilot/route").POST;

const mocks = vi.hoisted(() => ({
  auth: vi.fn(), read: vi.fn(), spawn: vi.fn(), unref: vi.fn(),
}));
vi.mock("@/auth", () => ({ auth: mocks.auth }));
vi.mock("node:fs", () => ({ existsSync: vi.fn(), readFileSync: mocks.read, writeFileSync: vi.fn(), openSync: () => 42, closeSync: vi.fn() }));
vi.mock("node:child_process", () => ({
  execFile: (_file: string, _args: string[], _options: unknown, callback: Function) => callback(null, { stdout: '{"running":false}' }),
  spawn: mocks.spawn,
}));

beforeEach(async () => {
  vi.resetModules();
  ({ POST } = await import("@/app/api/autopilot/route"));
  mocks.auth.mockResolvedValue({ user: { id: "test" } });
  mocks.spawn.mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), { unref: mocks.unref });
    queueMicrotask(() => child.emit("spawn"));
    return child;
  });
});

it.each([undefined, false, "true", true])("submission requires boolean true (%s)", async (enabled) => {
  mocks.read.mockReturnValue(JSON.stringify({ apply: { submit_without_review: enabled } }));
  const response = await POST(new NextRequest("http://localhost/api/autopilot", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "apply", max: 1 }),
  }));
  expect(response.status).toBe(200);
  const args = mocks.spawn.mock.calls[0][1];
  expect(args).toContain(enabled === true ? "--submit" : "--dry-run");
  expect(args).not.toContain(enabled === true ? "--dry-run" : "--submit");
});

it("does not launch a process without authentication", async () => {
  mocks.auth.mockResolvedValue(null);
  const response = await POST(new NextRequest("http://localhost/api/autopilot", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "apply" }),
  }));
  expect(response.status).toBe(401);
  expect(mocks.spawn).not.toHaveBeenCalled();
});

it("defaults to dry run when the configuration cannot be parsed", async () => {
  mocks.read.mockReturnValue("invalid JSON");
  const response = await POST(new NextRequest("http://localhost/api/autopilot", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "apply" }),
  }));
  expect(response.status).toBe(200);
  expect(mocks.spawn.mock.calls[0][1]).toContain("--dry-run");
  expect(mocks.spawn.mock.calls[0][1]).not.toContain("--submit");
});
