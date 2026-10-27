import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import SourcesPanel, { type SourceRow } from "@/app/dashboard/autopilot/SourcesPanel";

afterEach(() => vi.restoreAllMocks());

it("shows partial adapter results and cooldown evidence without changing source controls", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ queue: {
    queued_status: 0, eligible: 0, passed_gates: 0, queue: 0, other_filters: 0, gates: [], by_persona: {},
  } })));
  const onRun = vi.fn();
  const source: SourceRow = {
    source: "remote_visa_boards", known: true, enabled: true, every_hours: 6, runs: 1,
    last_run: "2026-10-17T05:00:00Z", last_found: 4, last_stored: 2, last_errors: 1,
    found_total: 4, stored_total: 2, jobs: 2, queued: 2, submitted: 0, interviews: 0,
    adapters: [{ adapter: "freehire", persona: "appsec", status: "partial", raw: 4, kept: 2, errors: 1,
      t: "2026-10-17T05:00:00Z", retry_at: "2026-10-17T17:00:00Z" }],
  };
  render(<SourcesPanel sources={[source]} persona="appsec" locked={false} busy={false} onToggle={vi.fn()} onRun={onRun} />);
  const user = userEvent.setup();
  await user.click(screen.getByText("Adapter health"));
  const health = screen.getByRole("list", { name: "Remote and visa board health" });
  expect(within(health).getByText("freehire (appsec)")).toBeVisible();
  expect(within(health).getByText("Partial results")).toBeVisible();
  expect(within(health).getByText(/4 found · 2 kept · 1 errors/)).toBeVisible();
  expect(within(health).getByText(/Retry after/)).toBeVisible();
  await user.click(screen.getByRole("button", { name: /Run discovery now/ }));
  expect(onRun).toHaveBeenCalledWith(["remote_visa_boards"]);
});

it("does not imply successful checks when no adapter history exists", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ queue: {
    queued_status: 0, eligible: 0, passed_gates: 0, queue: 0, other_filters: 0, gates: [], by_persona: {},
  } })));
  const source: SourceRow = { source: "remote_visa_boards", known: true, enabled: true, every_hours: 6,
    runs: 0, last_run: null, last_found: 0, last_stored: 0, last_errors: 0, found_total: 0,
    stored_total: 0, jobs: 0, queued: 0, submitted: 0, interviews: 0 };
  render(<SourcesPanel sources={[source]} persona="appsec" locked={false} busy={false} onToggle={vi.fn()} onRun={vi.fn()} />);
  await userEvent.click(screen.getByText("Adapter health"));
  expect(screen.getByText("No adapter run recorded yet.")).toBeVisible();
  expect(screen.queryByText("Checked")).not.toBeInTheDocument();
});

it("renders JobWatch canonical snapshot and runtime warnings in Sources", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ queue: {
    queued_status: 0, eligible: 0, passed_gates: 0, queue: 0, other_filters: 0, gates: [], by_persona: {},
  } })));
  const source: SourceRow = { source: "jobwatch", known: true, enabled: true, every_hours: 0,
    runs: 0, last_run: null, last_found: 0, last_stored: 0, last_errors: 0, found_total: 0,
    stored_total: 0, jobs: 0, queued: 0, submitted: 0, interviews: 0, adapters: [
      { adapter: "JobWatch creative: canonical DB snapshot", persona: "creative", status: "ok", raw: 10, kept: 0, errors: 0, t: null, retry_at: null },
      { adapter: "JobWatch watchdog: stale_running", persona: "all", status: "partial", raw: 0, kept: 0, errors: 1, t: null, retry_at: null },
      { adapter: "Bitbucket/Tor: no_accepted_targets", persona: "all", status: "disabled", raw: 0, kept: 0, errors: 0, t: null, retry_at: null },
    ] };
  render(<SourcesPanel sources={[source]} persona="all" locked={false} busy={false} onToggle={vi.fn()} onRun={vi.fn()} />);
  await userEvent.click(screen.getByText("JobWatch health"));
  const health = screen.getByRole("list", { name: "JobWatch health" });
  expect(within(health).getByText(/canonical DB snapshot/)).toBeVisible();
  expect(within(health).getByText(/stale_running/)).toBeVisible();
  expect(within(health).getByText(/no_accepted_targets/)).toBeVisible();
});
