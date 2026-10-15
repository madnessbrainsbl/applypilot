// @vitest-environment node
import { EventEmitter } from "node:events";
import { NextRequest } from "next/server";
import { beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  running: false,
  open: vi.fn(() => 42), close: vi.fn(), spawn: vi.fn(), unref: vi.fn(),
}));
vi.mock("@/auth", () => ({ auth: async () => ({ user: { id: "test-user" } }) }));
vi.mock("node:fs", () => ({
  existsSync: () => true, readFileSync: () => "{}", writeFileSync: vi.fn(),
  openSync: mocks.open, closeSync: mocks.close,
}));
vi.mock("node:child_process", () => ({
  execFile: (_file: string, _args: string[], _options: unknown, callback: Function) => {
    callback(null, { stdout: JSON.stringify({ running: mocks.running }) });
  },
  spawn: mocks.spawn,
}));
import { POST } from "@/app/api/autopilot/route";

beforeEach(() => {
  vi.clearAllMocks();
  mocks.running = false;
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
