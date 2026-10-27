import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import AutopilotClient from "@/app/dashboard/autopilot/AutopilotClient";

afterEach(() => vi.restoreAllMocks());

it("shows the reason for a manual answer and expands verified CV evidence", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
    providers: [], resumes: [], log: [],
    pending: [{ key: "unknown", question: "Describe your certification project", company: "Demo", seen: 1,
      options: [], job: "https://example.invalid/job", reason: "Certification is not supported by the CV" }],
    answerDecisions: [
      { question_key: "project", job_url: "https://example.invalid/job", question: "Describe a project",
        company: "Demo", persona: "appsec", source: "llm", method: "cv_review", status: "revised",
        cv: "appsec.pdf", reason: "Removed unsupported team leadership", draft: "I led a team.",
        answer: "I built Python review tools.", evidence: ["Built Python review tools at Cedar Labs."] },
      { question_key: "unknown", job_url: "https://example.invalid/job", question: "Describe your certification project",
        company: "Demo", persona: "creative", source: "cache", method: "cv_review", status: "manual",
        cv: "creative.pdf", reason: "Certification is not supported by the CV", draft: "I hold an OSCP.", answer: "", evidence: [] },
    ],
  })));
  render(<AutopilotClient />);
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: /Review/ }));
  expect(await screen.findByText("Answer review")).toBeVisible();
  expect(screen.getByText("Why an answer is needed: Certification is not supported by the CV")).toBeVisible();
  expect(screen.getByText("Revised")).toBeVisible();
  expect(screen.getByText("Needs review")).toBeVisible();
  expect(screen.queryByText("Built Python review tools at Cedar Labs.")).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Evidence: Describe a project" }));
  expect(await screen.findByText("Built Python review tools at Cedar Labs.")).toBeVisible();
  expect(screen.getByText("I built Python review tools.")).toBeVisible();
  expect(screen.getByText("I led a team.")).toBeVisible();
});
