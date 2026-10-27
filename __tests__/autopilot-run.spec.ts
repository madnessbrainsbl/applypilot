// @vitest-environment node
import { EventEmitter } from "node:events";
import { NextRequest } from "next/server";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  running: false, submit: false,
  open: vi.fn(() => 42), close: vi.fn(), spawn: vi.fn(), unref: vi.fn(),
}));
vi.mock("@/auth", () => ({ auth: async () => ({ user: { id: "test-user" } }) }));
vi.mock("node:fs", () => ({
  existsSync: () => true, readFileSync: () => JSON.stringify({ apply: { submit_without_review: mocks.submit } }), writeFileSync: vi.fn(),
  openSync: mocks.open, closeSync: mocks.close,
}));
vi.mock("node:child_process", () => ({
  execFile: (_file: string, _args: string[], _options: unknown, callback: Function) => {
    callback(null, { stdout: JSON.stringify({ running: mocks.running }) });
  },
  spawn: mocks.spawn,
}));
let POST: typeof import("@/app/api/autopilot/route").POST;

beforeEach(async () => {
  vi.resetModules();
  ({ POST } = await import("@/app/api/autopilot/route"));
  vi.clearAllMocks();
  mocks.running = false;
  mocks.submit = false;
  mocks.spawn.mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), { unref: mocks.unref });
    queueMicrotask(() => child.emit("spawn"));
    return child;
  });
});
const request = () => new NextRequest("http://localhost/api/autopilot", {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ action: "apply", max: 5 }),
});

it("records worker output in the live log and closes the parent handle", async () => {
  expect((await POST(request())).status).toBe(200);
  expect(mocks.spawn).toHaveBeenCalledWith(expect.any(String), expect.arrayContaining(["--ats-only"]), expect.objectContaining({ stdio: ["ignore", 42, 42] }));
  expect(mocks.close).toHaveBeenCalledWith(42);
  expect(mocks.unref).toHaveBeenCalledOnce();
});

it("rejects a second run before opening a log or starting a worker", async () => {
  mocks.running = true;
  expect((await POST(request())).status).toBe(409);
  expect(mocks.spawn).not.toHaveBeenCalled();
  expect(mocks.open).not.toHaveBeenCalled();
});

it("keeps the launch grace period active between requests in the same run", async () => {
  expect((await POST(request())).status).toBe(200);
  expect((await POST(request())).status).toBe(409);
  expect(mocks.spawn).toHaveBeenCalledOnce();
});

it("reports a worker launch failure and still closes the log", async () => {
  mocks.spawn.mockImplementation(() => {
    const child = Object.assign(new EventEmitter(), { unref: mocks.unref });
    queueMicrotask(() => child.emit("error", new Error("Python unavailable")));
    return child;
  });
  expect((await POST(request())).status).toBe(502);
  expect(mocks.close).toHaveBeenCalledWith(42);
  expect(mocks.unref).not.toHaveBeenCalled();
});

it("accepts JobWatch discovery from the dashboard without starting applications", async () => {
  const req = new NextRequest("http://localhost/api/autopilot", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "discover", sources: ["jobwatch"], persona: "creative" }),
  });
  expect((await POST(req)).status).toBe(200);
  const args = mocks.spawn.mock.calls[0][1] as string[];
  expect(args).toEqual(expect.arrayContaining(["discover", "--source=jobwatch", "--persona", "creative"]));
  expect(args).not.toContain("--submit");
});

it("keeps applications in dry-run mode until configuration explicitly opts in", async () => {
  await POST(request());
  expect(mocks.spawn.mock.calls[0][1]).toContain("--dry-run");
  expect(mocks.spawn.mock.calls[0][1]).not.toContain("--submit");
});
it("uses submission only with explicit boolean configuration", async () => {
  mocks.submit = true;
  await POST(request());
  expect(mocks.spawn.mock.calls[0][1]).toContain("--submit");
});
