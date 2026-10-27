import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import AutopilotClient from "@/app/dashboard/autopilot/AutopilotClient";

afterEach(() => vi.restoreAllMocks());

it("shows the configured fallback order including Claude Code and Antigravity", async () => {
  const names = ["opencode", "groq", "claude", "antigravity"];
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    providers: names.map((name) => ({ name, kind: name === "claude" || name === "opencode" ? "cli" : "openai", enabled: true })),
    resumes: [], pending: [], log: [],
  })));
  render(<AutopilotClient />);
  await userEvent.click(screen.getByRole("tab", { name: "AI providers" }));
  expect(await screen.findByText("Fallback order")).toBeVisible();
  expect(screen.getByText(/then moves to the next provider/)).toBeVisible();
  await screen.findByLabelText("Model for antigravity");
  expect(screen.getAllByRole("combobox").map((select) => select.getAttribute("aria-label")))
    .toEqual(names.map((name) => `Model for ${name}`));
});

it("loads OpenCode models and keeps a Free verdict attached to the tested model", async () => {
  let model = "opencode/big-pickle";
  const provider = () => ({ name: "opencode", kind: "cli", model, enabled: true, installed: true });
  vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    const body = init?.method === "POST" ? JSON.parse(String(init.body)) : {};
    let result;
    if (body.action === "probe") result = { providers: [{ ...provider(), ok: true, detail: "ok" }] };
    else if (body.action === "models") result = { ok: true, models: [model, "opencode-go/gpt-6-luna"] };
    else if (body.action === "model") { model = body.model; result = { ok: true }; }
    else result = { providers: [provider()], resumes: [], pending: [], log: [] };
    return new Response(JSON.stringify(result));
  });
  render(<AutopilotClient />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "AI providers" }));
  expect(await screen.findByText("OpenCode (Free / Go)")).toBeVisible();
  await user.click(screen.getByRole("button", { name: "List models", exact: true }));
  await user.click(screen.getByRole("button", { name: "Test", exact: true }));
  expect(await screen.findByText("WORKING")).toBeVisible();
  await user.selectOptions(screen.getByLabelText("Model for opencode"), "opencode-go/gpt-6-luna");
  await waitFor(() => expect(screen.queryByText("WORKING")).not.toBeInTheDocument());
  expect(screen.getByText("READY TO TEST")).toBeVisible();
  expect(screen.queryByText("✓ Response: ok")).not.toBeInTheDocument();
});
