import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import AutopilotClient from "@/app/dashboard/autopilot/AutopilotClient";

afterEach(() => vi.restoreAllMocks());

it("renders the CLI pending-question contract and saves its key, label and selected answer", async () => {
  const pending = [{
    key: "authorization",
    question: "Are you authorized to work in the job location?",
    options: ["Yes", "No", "I need sponsorship"],
    company: "Demo Cedar",
    job: "https://example.com/jobs/1",
    seen: 1,
  }];
  const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
    const body = init?.method === "POST" ? { ok: true } : { pending, providers: [], resumes: [], log: [] };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  render(<AutopilotClient />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: /Review/ }));
  expect(await screen.findByText(pending[0].question)).toBeVisible();
  await user.click(screen.getByRole("button", { name: "I need sponsorship" }));
  expect(screen.getByPlaceholderText("Your answer")).toHaveValue("I need sponsorship");
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/autopilot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "answer", key: "authorization", label: pending[0].question, answer: "I need sponsorship" }),
  }));
});

it("keeps run controls separate from resume and provider settings", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    status: { running: false, pending_above_score: 12 }, providers: [], resumes: [], log: ["Batch finished"],
  }), { status: 200, headers: { "Content-Type": "application/json" } }));
  render(<AutopilotClient />);
  expect(await screen.findByText("Ready to run")).toBeVisible();
  expect(screen.getByRole("button", { name: "Stop" })).toBeDisabled();
  expect(screen.getByText("Batch finished")).toBeVisible();
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: "Resumes" }));
  expect(screen.getByLabelText("Resume PDF (up to 10 MB)")).toBeVisible();
  expect(screen.queryByRole("button", { name: "Dry run 5" })).not.toBeInTheDocument();
  await user.click(screen.getByRole("tab", { name: "Run & activity" }));
  expect(screen.getByRole("button", { name: "Dry run 5" })).toBeVisible();
});
