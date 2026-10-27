import "server-only";

import { auth } from "@/auth";
import { NextRequest, NextResponse } from "next/server";
import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { closeSync, existsSync, fstatSync, openSync, readFileSync, readSync, writeFileSync } from "node:fs";
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
const LOG_TAIL_BYTES = 64 * 1024;
// safe provider-model charset; no whitespace, no leading "-"
const MODEL_NAME = /^[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,99}$/;
// unicode letters/digits, spaces and a few separators; no leading "-" so it cannot become a flag
const LOCATION = /^[\p{L}\p{N}][\p{L}\p{N}_ ,.'\u2019-]{1,59}$/u;
// discovery source names accepted by `discover --source` / `sources --set` (mirror of KNOWN_SOURCES in tools/autopilot.py)
const SOURCES = new Set(["jobwatch", "vendor", "startups", "medical_startups", "workspace_findall", "workspace_poisk", "simplify", "remotive",
  "jobicy", "remoteok", "arbeitnow", "wwr", "djinni", "relocate", "google", "himalayas", "themuse", "workable", "adzuna", "jooble",
  "jobspy", "tavily", "linkedin", "hn", "cryptojobs", "sponsors", "security_osint", "telegram", "creative_eu", "jsonld_careers", "remote_visa_boards",
  "arbeitsagentur", "eu_portals", "hn_hiring", "dorks", "community", "findall_import", "findall_hosts"]);
// `queue` replays every apply gate over the whole backlog (~15 s); a short cache keeps reloads cheap
const QUEUE_TTL_MS = 120_000;
let queueCache: { at: number; persona: string; data: unknown } | null = null;
// set while a run is being spawned, so a double click cannot start two bots
const START_GRACE_MS = 15_000;
let startingUntil = 0;
let warnedNoOwner = false;

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

function submitEnabled(): boolean {
  try {
    return JSON.parse(readFileSync(CONFIG, "utf-8"))?.apply?.submit_without_review === true;
  } catch {
    return false;
  }
}

// The bot gets an allowlisted environment, not the whole Next.js process env. UI-managed
// secret names are deliberately absent: load_secrets() uses setdefault, so an inherited
// value would silently beat the key just saved in secrets.env.
const ENV_KEEP = new Set(["PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "USERPROFILE",
  "USERNAME", "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
  "PROGRAMFILES(X86)", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "TZ", "XDG_CONFIG_HOME", "XDG_CACHE_HOME",
  "DISPLAY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]);

function childEnv(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  const managed = allowedEnvNames();
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) {
    const up = k.toUpperCase();
    if (v === undefined || managed.has(k)) continue;
    if (ENV_KEEP.has(up) || up.startsWith("PYTHON") || up.startsWith("AUTOPILOT_") || up.startsWith("PLAYWRIGHT_")) env[k] = v;
  }
  return { ...env, PYTHONIOENCODING: "utf-8", ...extra } as unknown as NodeJS.ProcessEnv;
}

// Never echo a command line (the --set-key value is in it) or a secret back to the browser.
function errMsg(error: unknown): string {
  const e = error as { cmd?: string; stderr?: string; killed?: boolean; code?: unknown };
  let text: string;
  if (e?.cmd !== undefined) {
    text = e.killed ? "autopilot command timed out" : (e.stderr?.trim() || `autopilot command failed (exit ${String(e.code ?? "?")})`);
  } else {
    text = error instanceof Error ? error.message : String(error);
  }
  return text
    .replace(/\b([A-Za-z][A-Za-z0-9_]*(?:KEY|PASSWORD|TOKEN|SECRET|_ID))=\S+/g, "$1=[redacted]")
    .replace(/--set-key\s+\S+/g, "--set-key [redacted]")
    .slice(-400);
}

function exec(args: string[], timeout = 30_000) {
  return run(PYTHON, [CLI, ...args], {
    cwd: AUTOPILOT_ROOT,
    timeout,
    maxBuffer: 16 * 1024 * 1024,
    env: childEnv(),
  });
}

async function cli(args: string[], timeout = 30_000) {
  const { stdout } = await exec(args, timeout);
  // the CLI prints human notes on stderr and one JSON document on stdout
  const start = [stdout.indexOf("{"), stdout.indexOf("[")].filter((i) => i >= 0).sort((a, b) => a - b)[0];
  // silently returning null here made a broken backend render as a healthy empty
  // dashboard: zero jobs, no providers, no error anywhere
  if (start === undefined) throw new Error(`${args[0]} printed no JSON`);
  return JSON.parse(stdout.slice(start));
}

// read only the end of the file: run.log grows for the whole life of the install
function tailLog(lines = 120) {
  if (!existsSync(LOG)) return [];
  const fd = openSync(LOG, "r");
  try {
    const size = fstatSync(fd).size;
    const len = Math.min(size, LOG_TAIL_BYTES);
    const buf = Buffer.alloc(len);
    readSync(fd, buf, 0, len, size - len);
    const rows = buf.toString("utf-8").split(/\r?\n/);
    if (size > len) rows.shift(); // first row starts mid-line
    return rows.slice(-lines);
  } finally {
    closeSync(fd);
  }
}

// Session check plus owner lock: this route can start live job applications, so a
// merely valid login (e.g. a second self-registered account) is not enough.
async function authorize(): Promise<NextResponse | null> {
  const session = await auth();
  if (!session?.user) return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  const owner = process.env.AUTOPILOT_OWNER_EMAIL?.trim().toLowerCase();
  if (owner) {
    if (session.user.email?.toLowerCase() !== owner) return NextResponse.json({ error: "forbidden" }, { status: 403 });
  } else if (!warnedNoOwner) {
    warnedNoOwner = true;
    console.warn("[autopilot] AUTOPILOT_OWNER_EMAIL is not set: any signed-in user can control the bot");
  }
  return null;
}

// CSRF: a cross-site form/fetch carries the session cookie, so require same-origin
function isSameOrigin(request: NextRequest) {
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin") return false;
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === (request.headers.get("x-forwarded-host") ?? request.headers.get("host"));
  } catch {
    return false;
  }
}

async function isRunning() {
  return Boolean((await cli(["status"])).running);
}

export async function GET() {
  const denied = await authorize();
  if (denied) return denied;
  if (!existsSync(CLI)) {
    return NextResponse.json({ error: `autopilot not found at ${CLI}` }, { status: 500 });
  }
  try {
    const [status, pending, providers, manual, outreach, followups, resumes, report, sources, answerDecisions] = await Promise.all([
      cli(["status"]),
      cli(["qa", "--pending", "--limit", "30"]),
      cli(["llm"]),
      cli(["manual", "--limit", "40"]),
      cli(["outreach", "--limit", "30"]).catch(() => ({ total_packages: 0, recent: [] })),
      cli(["followups", "--limit", "30"]).catch(() => ({ due_for_followup: 0, results: [] })),
      // an object (not []) on failure, so the UI can tell "none uploaded" from "broken"
      cli(["resume", "list"]).catch((e) => ({ error: errMsg(e) })),
      // invitations to act on + rejection analysis from the mailbox (`inbox --apply` fills it)
      cli(["report", "--json"]).catch(() => ({ actions: [], rejections: { total: 0, kinds: {}, reasons: {}, recent: [] }, tips: [], segments: [], paused_segments: [] })),
      // per-source switches and statistics; [] (not an error) when this CLI predates the command
      cli(["sources", "--json"]).catch(() => []),
      cli(["qa", "--decisions", "--limit", "30"]),
    ]);
    if (status.running) startingUntil = 0; // the run is visible now; isRunning() guards from here
    status.submit_without_review = submitEnabled();
    return NextResponse.json({
      status, pending, providers, manual, outreach, followups, resumes, report, sources, answerDecisions, log: tailLog(), discovering: Boolean(status.running),
    });
  } catch (error) {
    return NextResponse.json({ error: errMsg(error) }, { status: 500 });
  }
}

export async function POST(request: NextRequest) {
  const denied = await authorize();
  if (denied) return denied;
  if (!isSameOrigin(request)) return NextResponse.json({ error: "cross-origin request refused" }, { status: 403 });

  const type = request.headers.get("content-type") ?? "";
  if (type.startsWith("multipart/form-data")) {
    return uploadResume(request);
  }
  if (!type.startsWith("application/json")) {
    return NextResponse.json({ error: "content-type must be application/json" }, { status: 415 });
  }
  const body = await request.json().catch(() => ({}));
  const action = String(body.action || "");
  try {
    return await dispatch(action, body);
  } catch (error) {
    // argparse rejects any value starting with "--", the CLI can exit non-zero for a
    // dozen other reasons, and writeFileSync throws when autopilot/ is missing. Without
    // this the rejection became a 500 HTML page and the UI said "not reachable".
    return NextResponse.json({ error: errMsg(error) }, { status: 502 });
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
    return NextResponse.json({ error: errMsg(error) }, { status: 400 });
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
      return NextResponse.json({ error: errMsg(error) }, { status: 400 });
    }
  }

  if (action === "answer") {
    const key = String(body.key ?? "");
    const answer = String(body.answer ?? "");
    const label = String(body.label ?? "") || key;
    if (!key || !answer) return NextResponse.json({ error: "key and answer are required" }, { status: 400 });
    // --opt=value, not --opt value: argparse rejects any separate value starting with
    // "--", and a free-text answer beginning with "--" is entirely plausible.
    await exec(["qa", `--key=${key}`, `--set=${answer}`, `--label=${label}`]);
    return NextResponse.json({ ok: true });
  }

  if (action === "probe") {
    const only = String(body.name || "");
    // probing every provider can legitimately take a while
    const providers = await cli(only ? ["llm", "--probe", `--only=${only}`] : ["llm", "--probe"], 120_000);
    return NextResponse.json({ ok: true, providers });
  }

  if (action === "models") {
    const name = String(body.name || "");
    if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });
    const result = await cli(["llm", `--models=${name}`], 60_000);
    return NextResponse.json({ ok: true, ...result });
  }

  if (action === "model") {
    const name = String(body.name ?? "");
    const model = String(body.model ?? "");
    if (!name) return NextResponse.json({ error: "name is required" }, { status: 400 });
    if (model && !MODEL_NAME.test(model)) {
      return NextResponse.json({ error: "model contains characters that are not allowed" }, { status: 400 });
    }
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
    if (!/^[a-f0-9]{16}$/.test(jobId)) return NextResponse.json({ error: "invalid jobId" }, { status: 400 });
    await exec(["manual", `--done=${jobId}`]);
    return NextResponse.json({ ok: true });
  }

  if (action === "source_toggle") {
    const name = String(body.name ?? "");
    if (!SOURCES.has(name)) return NextResponse.json({ error: `unknown source: ${name}` }, { status: 400 });
    if (typeof body.enabled !== "boolean") return NextResponse.json({ error: "enabled must be true or false" }, { status: 400 });
    const sources = await cli(["sources", `--set=${name}`, `--enabled=${body.enabled}`, "--json"]);
    return NextResponse.json({ ok: true, sources });
  }

  if (action === "queue") {
    const persona = typeof body.persona === "string" && RESUME_KEY.test(body.persona) ? body.persona : "all";
    if (!body.refresh && queueCache && queueCache.persona === persona && Date.now() - queueCache.at < QUEUE_TTL_MS) {
      return NextResponse.json({ ok: true, cached: true, queue: queueCache.data });
    }
    const data = await cli(["queue", "--json", `--persona=${persona}`], 180_000);
    queueCache = { at: Date.now(), persona, data };
    return NextResponse.json({ ok: true, queue: data });
  }

  if (action === "stop") {
    // control.json is latched-then-cleared by the next read, so writing "quit" with
    // nothing running left it on disk for the NEXT run to pick up and abort on.
    if (!await isRunning()) return NextResponse.json({ ok: true, note: "nothing is running" });
    writeFileSync(CONTROL, JSON.stringify({ action: "quit" }), "utf-8");
    return NextResponse.json({ ok: true });
  }

  if (action === "apply" || action === "discover") {
    // a bad persona/location used to be dropped silently, which widened the run to
    // every persona or every country; refuse instead
    if ((body.persona != null && typeof body.persona !== "string") || (body.location != null && typeof body.location !== "string")) {
      return NextResponse.json({ error: "persona and location must be strings" }, { status: 400 });
    }
    const personaRaw = typeof body.persona === "string" ? body.persona.trim() : "";
    if (personaRaw && personaRaw !== "all" && !RESUME_KEY.test(personaRaw)) {
      return NextResponse.json({ error: "persona must be 2-30 characters: a-z, 0-9, _" }, { status: 400 });
    }
    const locRaw = typeof body.location === "string" ? body.location.trim() : "";
    const locAll = !locRaw || locRaw.toLowerCase() === "all";
    if (!locAll && !LOCATION.test(locRaw)) {
      return NextResponse.json(
        { error: "location must be 2-60 characters: letters, digits, spaces and , . ' - _ (not starting with -)" },
        { status: 400 },
      );
    }
    const persona = personaRaw && personaRaw !== "all" ? ["--persona", personaRaw] : [];
    // optional subset of sources for a manual discover; unknown names are refused, not dropped
    const picked = Array.isArray(body.sources) ? body.sources.map(String) : [];
    if (picked.some((s) => !SOURCES.has(s))) return NextResponse.json({ error: "unknown discovery source" }, { status: 400 });
    const sourceArgs = action === "discover" ? [...new Set(picked)].map((s) => `--source=${s}`) : [];
    const locationArg = locAll ? [] : ["--location", locRaw];

    // claim the start slot before the first await so two near-simultaneous requests
    // cannot both pass the isRunning() check
    if (Date.now() < startingUntil) return NextResponse.json({ error: "A run is already starting" }, { status: 409 });
    startingUntil = Date.now() + START_GRACE_MS;
    try {
      if (await isRunning()) {
        startingUntil = 0;
        return NextResponse.json({ error: "A run is already active" }, { status: 409 });
      }
      const max = Math.min(Math.max(Number(body.max) || 5, 1), 25);
      const workers = Number(body.workers) ? ["--workers", String(Math.min(Math.max(Number(body.workers), 1), 8))] : [];
      const mode = submitEnabled() ? "--submit" : "--dry-run";
      const args = action === "apply"
        ? ["apply", mode, "--ats-only", "--max", String(max), ...workers, ...persona, ...locationArg]
        : ["discover", ...persona, ...locationArg, ...sourceArgs];
      // a stale "quit" left by an earlier stop would abort this run at once
      writeFileSync(CONTROL, "{}", "utf-8");
      const log = openSync(LOG, "a");
      try {
        const child = spawn(PYTHON, [CLI, ...args], {
          cwd: AUTOPILOT_ROOT,
          detached: true,
          stdio: ["ignore", log, log],
          env: childEnv({ PYTHONUNBUFFERED: "1" }),
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
    } catch (error) {
      startingUntil = 0;
      throw error;
    }
  }

  return NextResponse.json({ error: `unknown action: ${action}` }, { status: 400 });
}
