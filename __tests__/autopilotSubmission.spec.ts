// @vitest-environment node
import { NextRequest } from "next/server";
import { POST } from "@/app/api/autopilot/route";

const mocks = vi.hoisted(() => ({
  auth: vi.fn(), read: vi.fn(), spawn: vi.fn(), unref: vi.fn(),
}));
vi.mock("@/auth", () => ({ auth: mocks.auth }));
vi.mock("node:fs", () => ({ existsSync: vi.fn(), readFileSync: mocks.read, writeFileSync: vi.fn() }));
vi.mock("node:child_process", () => ({ execFile: vi.fn(), spawn: mocks.spawn }));

beforeEach(() => {
  mocks.auth.mockResolvedValue({ user: { id: "test" } });
  mocks.spawn.mockReturnValue({ unref: mocks.unref });
});

it.each([undefined, false, "true", true])("submission requires boolean true (%s)", async (enabled) => {
  mocks.read.mockReturnValue(JSON.stringify({ apply: { submit_without_review: enabled } }));
  const response = await POST(new NextRequest("http://localhost/api/autopilot", {
    method: "POST", body: JSON.stringify({ action: "apply", max: 1 }),
  }));
  expect(response.status).toBe(200);
  const args = mocks.spawn.mock.calls[0][1];
  expect(args).toContain(enabled === true ? "--submit" : "--dry-run");
  expect(args).not.toContain(enabled === true ? "--dry-run" : "--submit");
});

it("does not launch a process without authentication", async () => {
  mocks.auth.mockResolvedValue(null);
  const response = await POST(new NextRequest("http://localhost/api/autopilot", {
    method: "POST", body: JSON.stringify({ action: "apply" }),
  }));
  expect(response.status).toBe(401);
  expect(mocks.spawn).not.toHaveBeenCalled();
});
