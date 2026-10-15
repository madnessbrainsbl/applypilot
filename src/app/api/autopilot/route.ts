import "server-only";

import { auth } from "@/auth";
import { NextRequest, NextResponse } from "next/server";
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { closeSync, existsSync, openSync, readFileSync, writeFileSync } from "node:fs";
import { rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { promisify } from "node:util";

const run = promisify(execFile);

// The Python autopilot lives in this same project (tools/ + autopilot/); this
// route is only a remote control for it, so nothing here duplicates its logic.
// process.cwd() is the repo root under `next dev` and `next start`, but not under
// `output: "standalone"` (WORKDIR /app, which holds no tools/) or any process manager
// with its own WorkingDirectory. AUTOPILOT_ROOT is the escape hatch for those.
const AUTOPILOT_ROOT = process.env.AUTOPILOT_ROOT || process.cwd();
const CLI = path.join(AUTOPILOT_ROOT, "tools", "autopilot.py");
const CONFIG = path.join(AUTOPILOT_ROOT, "autopilot", "config.json");
const LOG = path.join(AUTOPILOT_ROOT, "autopilot", "run.log");
const CONTROL = path.join(AUTOPILOT_ROOT, "autopilot", "control.json");
const PYTHON = process.env.AUTOPILOT_PYTHON || "python";
// resume/persona keys as the CLI stores them; also keeps a "--flag" out of argv
const RESUME_KEY = /^[a-z0-9_]{2,30}$/;
const MAX_PDF_BYTES = 10 * 1024 * 1024;

// Every secret this route may write, taken from the providers the config actually
// declares. Without it, `{"action":"key","env":"GROK_BIN","value":"C:\\evil.exe"}`
// wrote an arbitrary env var that _cli_path then executed, and
// `AUTOPILOT_LLM_BASE_URL` redirected every CV-bearing LLM call to any host.
const EXTRA_ENV_NAMES = ["TAVILY_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY", "JOOBLE_API_KEY",
  "AUTOPILOT_EMAIL_ADDRESS", "AUTOPILOT_EMAIL_APP_PASSWORD"];

function allowedEnvNames(): Set<string> {
  const names = new Set(EXTRA_ENV_NAMES);
  try {
    const cfg = JSON.parse(readFileSync(CONFIG, "utf-8"));
    for (const p of cfg?.llm?.providers ?? []) if (p?.key_env) names.add(String(p.key_env));
  } catch {
    // an unreadable config must not widen the allowlist
  }
  return names;
}

async function cli(args: string[], timeout = 0) {
  const { stdout } = await run(PYTHON, [CLI, ...args], {
    cwd: AUTOPILOT_ROOT,
    timeout,
    maxBuffer: 16 * 1024 * 1024,
    env: { ...process.env, PYTHONIOENCODING: "utf-8" },
  });
  // the CLI prints human notes on stderr and one JSON document on stdout
  const start = [stdout.indexOf("{"), stdout.indexOf("[")].filter((i) => i >= 0).sort((a, b) => a - b)[0];
  // silently returning null here made a broken backend render as a healthy empty
  // dashboard: zero jobs, no providers, no error anywhere
  if (start === undefined) throw new Error(`${args[0]} printed no JSON`);
  return JSON.parse(stdout.slice(start));
}

function tailLog(lines = 120) {
  if (!existsSync(LOG)) return [];
  const text = readFileSync(LOG, "utf-8");
  return text.split(/\r?\n/).slice(-lines);
}

async function isRunning() {
  return Boolean((await cli(["status"])).running);
}

export async function GET() {
  const session = await auth();
  if (!session?.user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  if (!existsSync(CLI)) {
    return NextResponse.json({ error: `autopilot not found at ${CLI}` }, { status: 500 });
  }
  try {
    const [status, pending, providers, manual, outreach, followups, resumes, report] = await Promise.all([
      cli(["status"]),
      cli(["qa", "--pending", "--limit", "30"]),
      cli(["llm"]),
      cli(["manual", "--limit", "40"]),
      cli(["outreach", "--limit", "30"]).catch(() => ({ total_packages: 0, recent: [] })),
      cli(["followups", "--limit", "30"]).catch(() => ({ due_for_followup: 0, results: [] })),
      // an object (not []) on failure, so the UI can tell "none uploaded" from "broken"
      cli(["resume", "list"]).catch((e) => ({ error: String(e).slice(0, 300) })),
      // invitations to act on + rejection analysis from the mailbox (`inbox --apply` fills it)
      cli(["report", "--json"]).catch(() => ({ actions: [], rejections: { total: 0, kinds: {}, reasons: {}, recent: [] }, tips: [] })),
    ]);
    return NextResponse.json({
      status, pending, providers, manual, outreach, followups, resumes, report, log: tailLog(), discovering: Boolean(status.running),
    });
  } catch (error) {
    return NextResponse.json({ error: String(error).slice(0, 400) }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  const session = await auth();
  if (!session?.user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });

  if (request.headers.get("content-type")?.startsWith("multipart/form-data")) {
    return uploadResume(request);
  }
  const body = await request.json().catch(() => ({}));
  const action = String(body.action || "");
  try {
    return await dispatch(action, body);
  } catch (error) {
    // argparse rejects any value starting with "--", the CLI can exit non-zero for a
    // dozen other reasons, and writeFileSync throws when autopilot/ is missing. Without
    // this the rejection became a 500 HTML page and the UI said "not reachable".
    return NextResponse.json({ error: String(error).slice(0, 400) }, { status: 502 });
  }
}

async function uploadResume(request: NextRequest) {
  const form = await request.formData().catch(() => null);
  const file = form?.get("file");
  const name = String(form?.get("name") ?? "");
  const role = String(form?.get("role") ?? "").trim();
  const regen = form?.get("regen") === "1";
  if (!(file instanceof File) || file.type !== "application/pdf") {
    return NextResponse.json({ error: "file must be a PDF" }, { status: 400 });
  }
  if (file.size > MAX_PDF_BYTES) {
    return NextResponse.json({ error: "file must be 10 MB or smaller" }, { status: 400 });
  }
  if (!RESUME_KEY.test(name)) {
    return NextResponse.json({ error: "name must be 2-30 characters: a-z, 0-9, _" }, { status: 400 });
  }
  if (/[\r\n]/.test(role) || role.length > 120) {
    return NextResponse.json({ error: "role must be a single line up to 120 characters" }, { status: 400 });
  }
  const bytes = Buffer.from(await file.arrayBuffer());
  // the browser-sent MIME type is only a claim; a real PDF starts with %PDF-
  if (bytes.subarray(0, 5).toString("latin1") !== "%PDF-") {
    return NextResponse.json({ error: "file is not a valid PDF" }, { status: 400 });
  }
  const tmp = path.join(os.tmpdir(), `resume-${randomUUID()}.pdf`);
  try {
    await writeFile(tmp, bytes);
    // --opt=value: a role starting with "--" would otherwise be read as a flag
    const args = ["resume", "add", `--file=${tmp}`, `--name=${name}`];
    if (role) args.push(`--role=${role}`);
    if (regen) args.push("--regen");
    // the LLM call behind a new profile takes up to ~60s
    return NextResponse.json(await cli(args, 180_000));
  } catch (error) {
    const stderr = (error as { stderr?: string }).stderr?.trim();
    return NextResponse.json({ error: (stderr || String(error)).slice(-400) }, { status: 400 });
  } finally {
    await rm(tmp, { force: true });
  }
}

async function dispatch(action: string, body: Record<string, unknown>) {
  if (action === "resume_remove") {
    const name = String(body.name ?? "");
    if (!RESUME_KEY.test(name)) {
      return NextResponse.json({ error: "name must be 2-30 characters: a-z, 0-9, _" }, { status: 400 });
    }
    try {
      return NextResponse.json({ ok: true, ...(await cli(["resume", "remove", `--name=${name}`])) });
    } catch (error) {
      const stderr = (error as { stderr?: string }).stderr?.trim();
      return NextResponse.json({ error: (stderr || String(error)).slice(-400) }, { status: 400 });
    }
  }

  if (action === "answer") {
    const key = String(body.key ?? "");
    const answer = String(body.answer ?? "");
    const label = String(body.label ?? "") || key;
    if (!key || !answer) return NextResponse.json({ error: "key and answer are required" }, { status: 400 });
    // --opt=value, not --opt value: argparse rejects any separate value starting with
    // "--", and a free-text answer beginning with "--" is entirely plausible.
    await run(PYTHON, [CLI, "qa", `--key=${key}`, `--set=${answer}`, `--label=${label}`],
      { cwd: AUTOPILOT_ROOT, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    return NextResponse.json({ ok: true });
  }

  if (action === "probe") {
    const only = String(body.name || "");
    const providers = await cli(only ? ["llm", "--probe", `--only=${only}`] : ["llm", "--probe"]);
    return NextResponse.json({ ok: true, providers });
  }

  if (action === "models") {
    const name = String(body.name || "");
    if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });
    const result = await cli(["llm", `--models=${name}`]);
    return NextResponse.json({ ok: true, ...result });
  }

  if (action === "model") {
    const name = String(body.name ?? "");
    const model = String(body.model ?? "");
    if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });
    const result = await cli(["llm", "--set-model", `${name}=${model}`]);
    return NextResponse.json({ ok: true, ...result });
  }

  if (action === "toggle") {
    const name = String(body.name || "");
    if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });
    const result = await cli(["llm", body.enabled ? `--enable=${name}` : `--disable=${name}`]);
    return NextResponse.json({ ok: true, ...result });
  }

  if (action === "key") {
    const env = String(body.env || "");
    const value = String(body.value ?? "");
    if (!env) return NextResponse.json({ error: "env is required" }, { status: 400 });
    if (!allowedEnvNames().has(env)) {
      return NextResponse.json({ error: `not a configured secret: ${env}` }, { status: 400 });
    }
    // secrets.env is one NAME=value per line, so a newline in the value writes a
    // second variable the UI never offered a field for.
    if (/[\r\n]/.test(value)) {
      return NextResponse.json({ error: "value must be a single line" }, { status: 400 });
    }
    const result = await cli(["llm", "--set-key", `${env}=${value}`]);
    return NextResponse.json({ ok: true, ...result });
  }

  if (action === "action_done") {
    const jobId = String(body.jobId || "");
    if (!/^[a-f0-9]{16}$/.test(jobId)) return NextResponse.json({ error: "invalid jobId" }, { status: 400 });
    await cli(["report", "--done", jobId, "--json"]);
    return NextResponse.json({ ok: true });
  }

  if (action === "dismiss") {
    const jobId = String(body.jobId || "");
    if (!jobId) return NextResponse.json({ error: "jobId is required" }, { status: 400 });
    await run(PYTHON, [CLI, "manual", `--done=${jobId}`],
      { cwd: AUTOPILOT_ROOT, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
    return NextResponse.json({ ok: true });
  }

  if (action === "stop") {
    // control.json is latched-then-cleared by the next read, so writing "quit" with
    // nothing running left it on disk for the NEXT run to pick up and abort on.
    if (!await isRunning()) return NextResponse.json({ ok: true, note: "nothing is running" });
    writeFileSync(CONTROL, JSON.stringify({ action: "quit" }), "utf-8");
    return NextResponse.json({ ok: true });
  }

  if (action === "apply" || action === "discover") {
    if (await isRunning()) return NextResponse.json({ error: "A run is already active" }, { status: 409 });
    const max = Math.min(Math.max(Number(body.max) || 5, 1), 25);
    const workers = Number(body.workers) ? ["--workers", String(Math.min(Math.max(Number(body.workers), 1), 8))] : [];
    // the CLI accepts any resume key (checked against config.json); the regex only
    // keeps arbitrary strings, "--flags" included, out of argv
    const persona = typeof body.persona === "string" && RESUME_KEY.test(body.persona)
      ? ["--persona", body.persona] : [];
    const locRaw = typeof body.location === "string" ? body.location.trim() : "";
    const locationArg = (locRaw && locRaw.toLowerCase() !== "all" && /^[a-zA-Z0-9_ ,.-]{2,60}$/.test(locRaw))
      ? ["--location", locRaw] : [];
    const cfg = action === "apply" ? JSON.parse(readFileSync(CONFIG, "utf-8")) : null;
    const args = action === "apply"
      ? ["apply", cfg?.apply?.submit_without_review === true ? "--submit" : "--dry-run", "--ats-only", "--max", String(max), ...workers, ...persona, ...locationArg]
      : ["discover", ...persona, ...locationArg];
    const log = openSync(LOG, "a");
    try {
      const child = spawn(PYTHON, [CLI, ...args], {
        cwd: AUTOPILOT_ROOT,
        detached: true,
        stdio: ["ignore", log, log],
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUNBUFFERED: "1" },
      });
      await new Promise<void>((resolve, reject) => {
        child.once("spawn", resolve);
        child.once("error", reject);
      });
      child.unref();
    } finally {
      closeSync(log);
    }
    return NextResponse.json({ ok: true, started: args.join(" ") });
  }

  return NextResponse.json({ error: `unknown action: ${action}` }, { status: 400 });
}
