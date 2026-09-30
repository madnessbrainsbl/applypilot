"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toastError, toastSuccess } from "@/lib/toast";
import {
  AlertCircle,
  Briefcase,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Copy,
  ExternalLink,
  FileText,
  Layers,
  ListFilter,
  Loader2,
  Mail,
  Play,
  Search,
  Sparkles,
  Square,
  Trash2,
  Upload,
} from "lucide-react";

type PendingQuestion = {
  question_key: string;
  question_label: string;
  options: string;
  company: string;
  job_url: string;
  seen: number;
};

type Status = {
  submit_without_review?: boolean;
  jobs?: number;
  pending_above_score?: number;
  applied_log?: number;
  personas?: Record<string, { jobs: number; applied: number }>;
  apply_latency?: { submitted?: number; within_24h_pct?: number; median_days_to_apply?: number };
};

type Provider = {
  name: string;
  kind: string;
  model?: string;
  url?: string;
  key_env?: string | null;
  enabled: boolean;
  has_key?: boolean | null;
  ok?: boolean;
  detail?: string;
  installed?: boolean;
  path?: string | null;
};

type ProbeResult = Pick<Provider, "ok" | "detail" | "installed" | "path">;

type ManualItem = {
  job_id: string;
  company: string;
  title: string;
  reason: string;
  apply_url: string;
  fields_filled: number;
};

type Manual = { waiting_for_you?: number; results?: ManualItem[] };

type OutreachItem = {
  job_id: string;
  company: string;
  title: string;
  recruiter_search_url: string;
  manager_search_url: string;
  predicted_email?: string;
  mailto_url?: string;
  connection_invite_note?: string;
  inmail_template?: string;
};

type Outreach = { total_packages?: number; recent?: OutreachItem[] };

type FollowupItem = {
  job_id: string;
  company: string;
  title: string;
  url: string;
  applied_at: string;
  days_since_applied: number;
  status: string;
  recruiter_search_url?: string;
  followup_note?: string;
  mailto_url?: string;
};

type Followups = { due_for_followup?: number; results?: FollowupItem[] };

type Resume = {
  key: string;
  name: string;
  role_title: string;
  cv: string;
  cv_exists: boolean;
  keywords: number;
  jobs: number;
  applied: number;
};

type State = {
  status?: Status;
  manual?: Manual;
  pending?: PendingQuestion[];
  providers?: Provider[];
  log?: string[];
  discovering?: boolean;
  error?: string;
  outreach?: Outreach;
  followups?: Followups;
  // an array on success, { error } when `resume list` failed
  resumes?: Resume[] | { error: string };
};

const POLL_MS = 5000;
// Parallel Chrome workers per apply run; more is faster but stalls more often on a
// shared LLM proxy. Mirrors RECOMMENDED_WORKERS in tools/autopilot.py.
const RECOMMENDED_WORKERS = 3;

export default function AutopilotClient() {
  const [state, setState] = useState<State>({});
  const [batch, setBatch] = useState(5);
  const [workers, setWorkers] = useState(RECOMMENDED_WORKERS);
  const [personaChoice, setPersona] = useState("all");
  const [busy, setBusy] = useState(false);
  const [manualPage, setManualPage] = useState(1);
  const MANUAL_PAGE_SIZE = 8;
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [keys, setKeys] = useState<Record<string, string>>({});
  // Only the probe VERDICT, keyed by provider name. Holding a whole provider list
  // here froze the card: once "Test all" had filled it, the 5s poll could no longer
  // update enabled/model, so Turn off still read "Turn off" and the badge stayed
  // WORKING until a full page reload.
  const [probes, setProbes] = useState<Record<string, ProbeResult>>({});
  const [models, setModels] = useState<Record<string, string[]>>({});
  const [testing, setTesting] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadMsg, setUploadMsg] = useState<{ ok: boolean; text: string } | null>(null);

  function mergeProvider(next: Provider) {
    setProbes((prev) => ({
      ...prev,
      [next.name]: { ok: next.ok, detail: next.detail, installed: next.installed, path: next.path },
    }));
  }

  async function testOne(name: string) {
    setTesting(name);
    try {
      const res = await fetch("/api/autopilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "probe", name }),
      });
      const data = await res.json();
      if (data.providers?.[0]) mergeProvider(data.providers[0]);
      else toastError(data.error || "Test failed");
    } catch {
      toastError("Autopilot is not reachable");
    } finally {
      setTesting(null);
    }
  }

  async function loadModels(name: string) {
    // a failed call stores [], and [] is truthy — the old check made "List models"
    // permanently dead for that provider until a page reload
    if (models[name]?.length) return;
    try {
      const res = await fetch("/api/autopilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "models", name }),
      });
      const data = await res.json();
      setModels((prev) => ({ ...prev, [name]: data.models || [] }));
      if (!data.ok && data.error) toastError(`${name}: ${data.error}`);
    } catch {
      toastError("Could not list models");
    }
  }
  const logRef = useRef<HTMLPreElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/autopilot", { cache: "no-store" });
      const data = await res.json();
      // keep the last good data on a transient failure — replacing the whole state
      // blanked every card because one of the four CLI calls hiccuped
      if (!res.ok) setState((prev) => ({ ...prev, error: data.error || `HTTP ${res.status}` }));
      else setState(data);
    } catch {
      setState((prev) => ({ ...prev, error: "Autopilot is not reachable" }));
    }
  }, []);

  useEffect(() => {
    load();
    const timer = setInterval(load, POLL_MS);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    // follow the tail only while the reader is already at the bottom — the 5s poll
    // hands back a fresh array every time, so an unconditional scroll made it
    // impossible to read anything above the last line
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [state.log]);

  async function send(action: string, extra: Record<string, unknown> = {}) {
    setBusy(true);
    try {
      const res = await fetch("/api/autopilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, ...extra }),
      });
      const data = await res.json();
      if (!res.ok || data.error) {
        toastError(data.error || "Action failed");
        return false;
      }
      return true;
    } catch {
      toastError("Autopilot is not reachable");
      return false;
    } finally {
      setBusy(false);
      load();
    }
  }

  async function saveAnswer(q: PendingQuestion) {
    const answer = (answers[q.question_key] || "").trim();
    if (!answer) {
      toastError("Type an answer first");
      return;
    }
    const ok = await send("answer", { key: q.question_key, label: q.question_label, answer });
    if (ok) {
      toastSuccess("Saved — the bot reuses this on every future application");
      setAnswers((prev) => ({ ...prev, [q.question_key]: "" }));
    }
  }

  async function uploadResume(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (uploading) return;
    const form = e.currentTarget;
    const data = new FormData(form);
    const file = data.get("file");
    if (file instanceof File && file.size > 10 * 1024 * 1024) {
      setUploadMsg({ ok: false, text: "File is larger than 10 MB. Export a smaller PDF and try again." });
      return;
    }
    setUploading(true);
    setUploadMsg(null);
    try {
      const res = await fetch("/api/autopilot", { method: "POST", body: data });
      const out = await res.json();
      if (!res.ok || out.error) {
        setUploadMsg({ ok: false, text: `Upload failed: ${out.error || `HTTP ${res.status}`}` });
      } else {
        const kw = (out.keywords as string[] | undefined)?.join(", ");
        setUploadMsg({
          ok: true,
          text: `${out.updated ? "Updated" : "Added"} "${out.key}"${out.role_title ? ` (${out.role_title})` : ""}${kw ? `. Keywords: ${kw}` : ""}.`,
        });
        form.reset();
      }
    } catch {
      setUploadMsg({ ok: false, text: "Upload failed: autopilot is not reachable." });
    } finally {
      setUploading(false);
      load();
    }
  }

  async function removeResume(r: Resume) {
    if (!window.confirm(`Remove resume "${r.name || r.key}"? Its search profile is deleted from config.json.`)) return;
    if (await send("resume_remove", { name: r.key })) toastSuccess(`Removed "${r.key}"`);
  }

  const resumes = Array.isArray(state.resumes) ? state.resumes : [];
  const resumesError = state.resumes && !Array.isArray(state.resumes) ? state.resumes.error : null;
  // a removed resume must not stay the hidden target of Apply / Find
  const persona = resumes.some((r) => r.key === personaChoice) ? personaChoice : "all";
  const status = state.status || {};
  const latency = status.apply_latency || {};
  const pending = state.pending || [];
  const manualItems = state.manual?.results || [];
  const totalManualPages = Math.ceil(manualItems.length / MANUAL_PAGE_SIZE) || 1;
  const paginatedManualItems = manualItems.slice(
    (manualPage - 1) * MANUAL_PAGE_SIZE,
    manualPage * MANUAL_PAGE_SIZE
  );

  const stats = [
    {
      label: "In queue",
      description: "Match score ≥ threshold",
      value: status.pending_above_score ?? 0,
      icon: ListFilter,
      color: "text-blue-500 bg-blue-500/10",
    },
    {
      label: "Submitted",
      description: "Successfully submitted",
      value: latency.submitted ?? 0,
      icon: CheckCircle2,
      color: "text-emerald-500 bg-emerald-500/10",
    },
    {
      label: "Attempts logged",
      description: "Total apply attempts",
      value: status.applied_log ?? 0,
      icon: Clock,
      color: "text-amber-500 bg-amber-500/10",
    },
    {
      label: "Jobs known",
      description: "Saved across boards",
      value: status.jobs ?? 0,
      icon: Briefcase,
      color: "text-purple-500 bg-purple-500/10",
    },
  ];

  return (
    <div className="flex flex-col gap-6 py-2">
      <div className="flex flex-col gap-4 rounded-xl border border-border/70 bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <div className="flex items-center gap-2.5">
              <h1 className="text-2xl font-bold tracking-tight">Autopilot</h1>
              {state.discovering ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                  <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                  Active Run
                </span>
              ) : (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground">
                  <span className="h-2 w-2 rounded-full bg-muted-foreground/40" />
                  Idle
                </span>
              )}
            </div>
            <p className="text-muted-foreground mt-1 text-sm">
              Multi-agent automation across ATS boards, tailored resumes, and direct form submissions.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2.5">
            <div className="flex items-center rounded-lg border border-input bg-background px-3 py-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
              <label htmlFor="batch" className="text-muted-foreground mr-2 text-xs font-medium">Batch</label>
              <input
                id="batch"
                type="number"
                min={1}
                max={25}
                value={batch}
                onChange={(e) => setBatch(Math.max(1, Math.min(25, Number(e.target.value) || 1)))}
                className="w-12 bg-transparent text-sm font-semibold focus:outline-none"
              />
            </div>
            <div className="flex items-center rounded-lg border border-input bg-background px-3 py-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
              <label htmlFor="workers" className="text-muted-foreground mr-2 text-xs font-medium">Workers</label>
              <input
                id="workers"
                type="number"
                min={1}
                max={8}
                value={workers}
                aria-describedby="workers-hint"
                onChange={(e) => setWorkers(Math.max(1, Math.min(8, Number(e.target.value) || 1)))}
                className="w-10 bg-transparent text-sm font-semibold focus:outline-none"
              />
              <span id="workers-hint" className="text-muted-foreground text-xs">
                {workers === RECOMMENDED_WORKERS ? "recommended" : `${RECOMMENDED_WORKERS} recommended`}
              </span>
            </div>
            <Button
              disabled={busy || state.discovering}
              onClick={() => send("apply", { max: batch, workers, persona: persona === "all" ? undefined : persona })}
              className="gap-1.5 font-medium shadow-sm"
            >
              {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
              {status.submit_without_review ? "Apply to" : "Dry run for"} {batch}
            </Button>
            <Button
              variant="secondary"
              disabled={busy || state.discovering}
              onClick={() => send("discover", { persona: persona === "all" ? undefined : persona })}
              className="gap-1.5 font-medium"
            >
              <Search className="h-4 w-4" />
              Find new jobs
            </Button>
            <Button
              variant="destructive"
              disabled={busy}
              onClick={() => send("stop")}
              className="gap-1.5 font-medium"
            >
              <Square className="h-4 w-4 fill-current" />
              Stop
            </Button>
          </div>
        </div>

        {/* Multi-Agent Persona Switcher */}
        <div className="flex flex-wrap items-center justify-between border-t border-border/50 pt-3 gap-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground font-medium">
            <Layers className="h-3.5 w-3.5 text-primary" />
            <span>Target Persona:</span>
          </div>
          <div className="flex flex-wrap items-center gap-1.5">
            {[{ key: "all", label: "All Personas", jobs: resumes.reduce((n, r) => n + r.jobs, 0) },
              ...resumes.map((r) => ({ key: r.key, label: r.role_title || r.name || r.key, jobs: r.jobs }))].map((p) => (
              <button
                key={p.key}
                type="button"
                aria-pressed={persona === p.key}
                onClick={() => setPersona(p.key)}
                className={`inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                  persona === p.key
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "bg-muted/50 text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                {p.key === "all" ? <Layers className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
                <span>{p.label}</span>
                <span className={`ml-1 rounded-full px-1.5 py-0.2 text-[10px] ${
                  persona === p.key ? "bg-primary-foreground/20 text-primary-foreground" : "bg-muted-foreground/15 text-muted-foreground"
                }`}>
                  {p.jobs}
                </span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {state.error ? (
        <Card className="border-destructive/30 bg-destructive/5">
          <CardHeader className="py-4">
            <CardTitle className="text-base flex items-center gap-2 text-destructive">
              <AlertCircle className="h-4 w-4" />
              Autopilot unreachable
            </CardTitle>
            <CardDescription className="break-words text-destructive/80">{state.error}</CardDescription>
          </CardHeader>
        </Card>
      ) : null}

      <Card className="border border-border/70 shadow-sm">
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-semibold flex items-center gap-2">
            <FileText className="h-4 w-4 text-primary" />
            Resumes
          </CardTitle>
          <CardDescription className="mt-1">
            One search persona per resume. Upload a PDF and the bot builds its keywords and target titles from it.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {resumesError ? (
            <p role="alert" className="flex items-start gap-2 text-sm text-destructive break-words">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>Could not load resumes: {resumesError}</span>
            </p>
          ) : resumes.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              {state.resumes ? "No resumes yet. Upload your first PDF below to start searching." : "Loading resumes…"}
            </p>
          ) : (
            <ul className="divide-y divide-border/60 rounded-lg border border-border/60">
              {resumes.map((r) => (
                <li key={r.key} className="flex flex-col justify-between gap-3 p-3.5 sm:flex-row sm:items-center">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold break-words">{r.name || r.key}</span>
                      <span className="font-mono text-xs text-muted-foreground">({r.key})</span>
                      {r.cv_exists ? (
                        <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/15 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-400">
                          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                          PDF present
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-400">
                          <AlertCircle className="h-3 w-3" aria-hidden="true" />
                          PDF missing
                        </span>
                      )}
                    </div>
                    <p className="text-muted-foreground mt-0.5 text-xs break-words">
                      {r.role_title || "No role title"} · {r.keywords} keywords · {r.jobs} jobs · {r.applied} applied
                    </p>
                  </div>
                  <Button
                    variant="destructive"
                    size="sm"
                    disabled={busy || resumes.length <= 1}
                    title={resumes.length <= 1 ? "The last resume cannot be removed" : undefined}
                    onClick={() => removeResume(r)}
                    className="gap-1.5 self-start sm:self-center"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Remove
                    <span className="sr-only"> resume {r.key}</span>
                  </Button>
                </li>
              ))}
            </ul>
          )}

          <form onSubmit={uploadResume} className="grid grid-cols-1 gap-3 rounded-lg border border-dashed border-border p-3.5 sm:grid-cols-2">
            <div className="flex min-w-0 flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="resume-file">Resume PDF (up to 10 MB)</Label>
              <Input id="resume-file" name="file" type="file" accept=".pdf,application/pdf" required className="min-w-0" />
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor="resume-name">Key</Label>
              <Input
                id="resume-name"
                name="name"
                required
                pattern="[a-z0-9_]{2,30}"
                placeholder="e.g. appsec"
                aria-describedby="resume-name-hint"
                className="min-w-0"
              />
              <span id="resume-name-hint" className="text-muted-foreground text-xs">
                2-30 lowercase letters, digits or _. Reusing a key replaces its PDF.
              </span>
            </div>
            <div className="flex min-w-0 flex-col gap-1.5">
              <Label htmlFor="resume-role">Role title (optional)</Label>
              <Input id="resume-role" name="role" maxLength={120} placeholder="e.g. Application Security Engineer" className="min-w-0" />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-3 sm:col-span-2">
              <label htmlFor="resume-regen" className="flex items-center gap-2 text-sm">
                <input id="resume-regen" name="regen" type="checkbox" value="1" className="h-4 w-4 accent-primary" />
                Rebuild the search profile for an existing key
              </label>
              <Button type="submit" aria-busy={uploading} className="gap-1.5 font-medium">
                {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}
                {uploading ? "Analyzing resume…" : "Upload"}
              </Button>
            </div>
            <p aria-live="polite" role="status" className="text-sm break-words sm:col-span-2 empty:hidden">
              {uploadMsg ? (
                <span className={`inline-flex items-start gap-1.5 ${uploadMsg.ok ? "text-emerald-700 dark:text-emerald-400" : "text-destructive"}`}>
                  {uploadMsg.ok
                    ? <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                    : <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />}
                  {uploadMsg.text}
                </span>
              ) : uploading ? "Analyzing resume, this can take up to a minute." : null}
            </p>
          </form>
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((s) => {
          const Icon = s.icon;
          return (
            <Card key={s.label} className="overflow-hidden border border-border/70 shadow-sm transition-all hover:shadow-md">
              <CardContent className="p-5">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                    {s.label}
                  </span>
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${s.color}`}>
                    <Icon className="h-4 w-4" />
                  </div>
                </div>
                <div className="mt-2 text-3xl font-extrabold tracking-tight">
                  {s.value.toLocaleString()}
                </div>
                <div className="text-muted-foreground mt-1 text-xs truncate">
                  {s.description}
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <Card className="border border-border/70 shadow-sm">
        <CardHeader className="flex flex-row items-start justify-between pb-3">
          <div>
            <CardTitle className="text-base font-semibold flex items-center gap-2">
              Needs your click
              {manualItems.length > 0 && (
                <span className="rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-bold text-amber-600 dark:text-amber-400">
                  {state.manual?.waiting_for_you ?? manualItems.length}
                </span>
              )}
            </CardTitle>
            <CardDescription className="mt-1">
              Filled in, but requires manual submit confirmation. Open, complete, then dismiss.
            </CardDescription>
          </div>
          {manualItems.length > 0 && (
            <div className="text-muted-foreground text-xs font-medium">
              Page {manualPage} of {totalManualPages}
            </div>
          )}
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {manualItems.length === 0 ? (
            <p className="text-muted-foreground py-2 text-sm">Nothing waiting on you.</p>
          ) : (
            <>
              <div className="divide-y divide-border/60 rounded-lg border border-border/60">
                {paginatedManualItems.map((m) => (
                  <div
                    key={m.job_id}
                    className="flex flex-col justify-between gap-3 p-3.5 sm:flex-row sm:items-center hover:bg-muted/30 transition-colors"
                  >
                    <div className="min-w-0 flex-1">
                      <a
                        href={m.apply_url}
                        target="_blank"
                        rel="noreferrer"
                        className="group inline-flex items-center gap-1.5 text-sm font-semibold text-primary break-words hover:underline"
                      >
                        <span>{m.company} — {m.title}</span>
                        <ExternalLink className="h-3.5 w-3.5 opacity-60 group-hover:opacity-100" />
                      </a>
                      <p className="text-muted-foreground mt-0.5 text-xs break-words">
                        <span className="font-medium text-foreground/80">{m.reason}</span> · {m.fields_filled} fields pre-filled
                      </p>
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      disabled={busy}
                      onClick={() => send("dismiss", { jobId: m.job_id })}
                      className="self-start sm:self-center font-medium"
                    >
                      Dismiss
                    </Button>
                  </div>
                ))}
              </div>
              {totalManualPages > 1 && (
                <div className="flex items-center justify-between pt-2">
                  <span className="text-muted-foreground text-xs">
                    Showing {(manualPage - 1) * MANUAL_PAGE_SIZE + 1}–{Math.min(manualPage * MANUAL_PAGE_SIZE, manualItems.length)} of {manualItems.length}
                  </span>
                  <div className="flex items-center gap-1.5">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={manualPage <= 1}
                      onClick={() => setManualPage((p) => Math.max(1, p - 1))}
                      className="h-8 gap-1 px-2.5 text-xs"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" />
                      Prev
                    </Button>
                    <span className="text-muted-foreground px-2 text-xs font-medium">
                      {manualPage} / {totalManualPages}
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={manualPage >= totalManualPages}
                      onClick={() => setManualPage((p) => Math.min(totalManualPages, p + 1))}
                      className="h-8 gap-1 px-2.5 text-xs"
                    >
                      Next
                      <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card className="border border-border/70 shadow-sm">
        <CardHeader className="flex flex-row items-start justify-between pb-3">
          <div>
            <CardTitle className="text-base font-semibold flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-primary" />
              Recruiter Outreach (Reverse Recruiting)
              {(state.outreach?.recent?.length ?? 0) > 0 && (
                <span className="rounded-full bg-primary/15 px-2.5 py-0.5 text-xs font-bold text-primary">
                  {state.outreach?.total_packages ?? state.outreach?.recent?.length} ready
                </span>
              )}
            </CardTitle>
            <CardDescription className="mt-1">
              Direct LinkedIn & cold email targets for recently submitted applications. Send connection note to 5-10x your interview conversion.
            </CardDescription>
          </div>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {(state.outreach?.recent?.length ?? 0) === 0 ? (
            <p className="text-muted-foreground py-2 text-sm">No outreach packages generated yet. Submit applications to generate direct outreach links.</p>
          ) : (
            <div className="divide-y divide-border/60 rounded-lg border border-border/60">
              {state.outreach?.recent?.map((item) => (
                <div key={item.job_id} className="flex flex-col justify-between gap-3 p-3.5 sm:flex-row sm:items-center hover:bg-muted/30 transition-colors">
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold text-sm text-foreground">{item.company}</span>
                      <span className="text-xs text-muted-foreground">·</span>
                      <span className="text-xs text-muted-foreground truncate">{item.title}</span>
                      {item.predicted_email && (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-mono text-muted-foreground">
                          {item.predicted_email}
                        </span>
                      )}
                    </div>
                    {item.connection_invite_note && (
                      <p className="text-muted-foreground mt-1 text-xs line-clamp-1 italic bg-muted/40 p-1.5 rounded">
                        &ldquo;{item.connection_invite_note}&rdquo;
                      </p>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5 shrink-0">
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 text-xs gap-1"
                      asChild
                    >
                      <a href={item.recruiter_search_url} target="_blank" rel="noreferrer">
                        <Search className="h-3 w-3" />
                        Recruiter
                        <ExternalLink className="h-2.5 w-2.5 opacity-60" />
                      </a>
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      className="h-8 text-xs gap-1"
                      asChild
                    >
                      <a href={item.manager_search_url} target="_blank" rel="noreferrer">
                        Manager
                        <ExternalLink className="h-2.5 w-2.5 opacity-60" />
                      </a>
                    </Button>
                    {item.mailto_url && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs gap-1 text-emerald-600 dark:text-emerald-400"
                        asChild
                      >
                        <a href={item.mailto_url}>
                          <Mail className="h-3 w-3" />
                          Email
                        </a>
                      </Button>
                    )}
                    {item.connection_invite_note && (
                      <Button
                        variant="secondary"
                        size="sm"
                        className="h-8 text-xs gap-1"
                        onClick={() => {
                          navigator.clipboard.writeText(item.connection_invite_note || "");
                          toastSuccess("Invite note copied (220 chars)!");
                        }}
                      >
                        <Copy className="h-3 w-3" />
                        Copy Note
                      </Button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Questions waiting {pending.length ? `(${pending.length})` : ""}
          </CardTitle>
          <CardDescription>
            Answers the bot could not work out on its own. Answer once and it reuses them everywhere.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {pending.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              Nothing waiting. New questions appear here after a run.
            </p>
          ) : (
            pending.map((q) => {
              let options: string[] = [];
              try {
                options = JSON.parse(q.options || "[]");
              } catch {
                options = [];
              }
              return (
                <div key={q.question_key} className="border-border flex flex-col gap-2 border-t pt-4 first:border-t-0 first:pt-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-sm font-medium break-words">{q.question_label}</span>
                    <span className="text-muted-foreground text-xs">
                      {q.company || "any company"} · asked {q.seen}x
                    </span>
                  </div>
                  {options.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {options.map((o) => (
                        <Button
                          key={o}
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => setAnswers((prev) => ({ ...prev, [q.question_key]: o }))}
                        >
                          {o}
                        </Button>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <Input
                      value={answers[q.question_key] || ""}
                      placeholder="Your answer"
                      onChange={(e) =>
                        setAnswers((prev) => ({ ...prev, [q.question_key]: e.target.value }))
                      }
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveAnswer(q);
                      }}
                    />
                    <Button disabled={busy} onClick={() => saveAnswer(q)}>
                      Save
                    </Button>
                  </div>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>

      <Card className="border border-border/70 shadow-sm">
        <CardHeader className="flex flex-col gap-3 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <CardTitle className="text-base font-semibold">AI providers</CardTitle>
              <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                A → Z Priority
              </span>
            </div>
            <CardDescription className="mt-1">
              Провайдеры отсортированы по алфавиту и опрашиваются сверху вниз. Первый ответивший выполняет запрос. Если сервис недоступен, автопилот мгновенно переходит к следующему.
            </CardDescription>
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            className="shrink-0 font-medium"
            onClick={async () => {
              setBusy(true);
              try {
                const res = await fetch("/api/autopilot", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ action: "probe" }),
                });
                const data = await res.json();
                if (data.providers) (data.providers as Provider[]).forEach(mergeProvider);
                else toastError(data.error || "Probe failed");
              } catch {
                toastError("Autopilot is not reachable");
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
            Проверить все (Test all)
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col divide-y divide-border/60 p-0">
          {[...(state.providers || [])]
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((polled) => {
              const p = { ...polled, ...probes[polled.name] };
              const missing = p.kind === "cli" && p.installed === false;
              const noKey = !!p.key_env && p.has_key === false;

              const meta: { label: string; desc: string; tag: string } = {
                antigravity: {
                  label: "Antigravity Bridge",
                  desc: "Локальный мост к моделям Google Gemini через прокси Antigravity IDE (порт 8080).",
                  tag: "Local Bridge",
                },
                claude: {
                  label: "Anthropic Claude (CLI)",
                  desc: "Консольный клиент Claude. Использует подписку Anthropic без ввода отдельного ключа.",
                  tag: "CLI Tool",
                },
                codex: {
                  label: "OpenAI Codex (CLI)",
                  desc: "Официальный агент OpenAI CLI. Работает локально через активную консольную сессию.",
                  tag: "CLI Tool",
                },
                grok: {
                  label: "xAI Grok (CLI)",
                  desc: "Консольный агент xAI Grok. Прямой вызов исполняемого файла grok.exe.",
                  tag: "CLI Tool",
                },
                groq: {
                  label: "Groq Cloud (LPU)",
                  desc: "Сверхскоростной облачный инференс (Qwen 3.8 / Llama 3) с минимальной задержкой.",
                  tag: "Cloud API",
                },
                llm7: {
                  label: "LLM7.io Gateway",
                  desc: "Провайдер новейших моделей (GPT-5.5, DeepSeek V4 Pro, Claude 4.5/4.7, GLM-5.3).",
                  tag: "Cloud API",
                },
                lmstudio: {
                  label: "LM Studio (Offline)",
                  desc: "Автономный локальный сервер LLM (127.0.0.1:1234). Работает офлайн без расхода квот.",
                  tag: "Local LLM",
                },
                openrouter: {
                  label: "OpenRouter Gateway",
                  desc: "Универсальный облачный шлюз ко всем мировым моделям (Claude, GPT, Gemini).",
                  tag: "Cloud API",
                },
                tokenharbor: {
                  label: "Token Harbor Hub",
                  desc: "Единый хаб моделей с поддержкой бесплатных тарифов (DeepSeek V4 Flash, Qwen Flash).",
                  tag: "Cloud API",
                },
              }[p.name] || {
                label: p.name,
                desc: p.kind === "cli" ? "Консольный провайдер." : "OpenAI-совместимый API сервис.",
                tag: p.kind === "cli" ? "CLI" : "API",
              };

              const borderColor =
                !p.enabled ? "border-l-muted-foreground/30"
                : missing ? "border-l-amber-500"
                : noKey ? "border-l-amber-500"
                : p.ok === true ? "border-l-emerald-500"
                : p.ok === false ? "border-l-red-500"
                : "border-l-blue-500";

              const badge =
                !p.enabled ? { text: "ВЫКЛЮЧЕН", cls: "bg-muted text-muted-foreground" }
                : missing ? { text: "НЕ УСТАНОВЛЕН", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" }
                : noKey ? { text: "ТРЕБУЕТ КЛЮЧ", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400" }
                : p.ok === true ? { text: "РАБОТАЕТ (ONLINE)", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" }
                : p.ok === false ? { text: "ОШИБКА", cls: "bg-red-500/15 text-red-600 dark:text-red-400" }
                : { text: "ГОТОВ К ТЕСТУ", cls: "bg-blue-500/15 text-blue-600 dark:text-blue-400" };

              const keyStatus = !p.key_env
                ? { text: "Без API-ключа", cls: "text-muted-foreground/70" }
                : p.has_key
                ? { text: "✓ Ключ настроен", cls: "text-emerald-600 dark:text-emerald-400 font-medium" }
                : { text: "⚠ Ключ отсутствует", cls: "text-amber-600 dark:text-amber-400 font-medium" };

              return (
                <div
                  key={p.name}
                  className={`border-l-4 ${borderColor} p-4 transition-colors hover:bg-muted/20`}
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold tracking-tight text-foreground">
                          {meta.label}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">({p.name})</span>
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground uppercase">
                          {meta.tag}
                        </span>
                        <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold tracking-wider ${badge.cls}`}>
                          {badge.text}
                        </span>
                        <span className={`text-[11px] ${keyStatus.cls}`}>
                          {keyStatus.text}
                        </span>
                      </div>
                      <p className="text-muted-foreground text-xs leading-relaxed">
                        {meta.desc}
                      </p>
                      <div className="text-muted-foreground flex flex-wrap items-center gap-1.5 text-[11px]">
                        <span className="font-medium text-foreground/80">
                          {p.kind === "cli" ? (p.path || "CLI не обнаружен в PATH") : p.url}
                        </span>
                        {p.model && <span>· модель: <span className="font-mono text-foreground">{p.model}</span></span>}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0 self-start sm:self-center">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={testing === p.name || !p.enabled}
                        onClick={() => testOne(p.name)}
                        className="h-8 px-3 text-xs font-medium"
                      >
                        {testing === p.name ? (
                          <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Тестирование…</>
                        ) : "Тест"}
                      </Button>
                      <Button
                        variant={p.enabled ? "default" : "secondary"}
                        size="sm"
                        disabled={busy}
                        onClick={() => send("toggle", { name: p.name, enabled: !p.enabled })}
                        className={`h-8 px-3 text-xs font-semibold ${p.enabled ? "bg-emerald-600 hover:bg-emerald-700 text-white" : ""}`}
                      >
                        {p.enabled ? "Включен" : "Выключен"}
                      </Button>
                    </div>
                  </div>

                  {missing && (
                    <div className="mt-2.5 rounded-md border border-amber-500/30 bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">
                      Утилита {p.name} не найдена в системном PATH. Установите её через терминал и выполните разовый вход.
                    </div>
                  )}

                  {p.detail && (
                    <div className={`mt-2.5 rounded-md p-2 text-xs font-mono break-all ${p.ok ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-red-500/10 text-red-600 dark:text-red-400"}`}>
                      {p.ok ? `✓ Ответ сервиса: ${p.detail}` : `⚠ Ошибка: ${p.detail}`}
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-2.5">
                    <span className="text-muted-foreground text-xs font-medium">Модель:</span>
                    <select
                      className="border-input bg-background h-8 min-w-44 rounded-md border px-2.5 text-xs font-medium focus:outline-none focus:ring-1 focus:ring-primary"
                      value={p.model || ""}
                      onFocus={() => loadModels(p.name)}
                      onChange={(e) => send("model", { name: p.name, model: e.target.value })}
                    >
                      <option value={p.model || ""}>{p.model || "Модель по умолчанию"}</option>
                      {(models[p.name] || [])
                        .filter((m) => m !== p.model)
                        .map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                    </select>
                    <Button variant="ghost" size="sm" onClick={() => loadModels(p.name)} className="h-8 px-2.5 text-xs">
                      Загрузить список моделей
                    </Button>
                    {models[p.name] && (
                      <span className="text-muted-foreground text-[11px]">
                        доступно: {models[p.name].length}
                      </span>
                    )}
                  </div>

                  {p.key_env && (
                    <div className="mt-2.5 flex items-center gap-2">
                      <div className="relative flex-1">
                        <Input
                          type="password"
                          value={keys[p.key_env] || ""}
                          placeholder={p.has_key ? `${p.key_env} настроен (вставьте новый для замены)` : `Вставьте ключ (${p.key_env})`}
                          onChange={(e) => setKeys((prev) => ({ ...prev, [p.key_env!]: e.target.value }))}
                          className="h-8 text-xs pr-16"
                        />
                        {p.has_key && (
                          <span className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded bg-emerald-500/15 px-1.5 py-0.5 text-[9px] font-bold text-emerald-600 dark:text-emerald-400 uppercase">
                            АКТИВЕН
                          </span>
                        )}
                      </div>
                      <Button
                        variant="secondary"
                        size="sm"
                        disabled={busy || !(keys[p.key_env] || "").trim()}
                        onClick={async () => {
                          const ok = await send("key", { env: p.key_env, value: keys[p.key_env!] });
                          if (ok) {
                            toastSuccess(`${p.key_env} успешно сохранен`);
                            setKeys((prev) => ({ ...prev, [p.key_env!]: "" }));
                          }
                        }}
                        className="h-8 px-3.5 text-xs font-medium shrink-0"
                      >
                        Сохранить ключ
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Activity</CardTitle>
          <CardDescription>Live tail of the autopilot log.</CardDescription>
        </CardHeader>
        <CardContent>
          <pre
            ref={logRef}
            className="bg-muted max-h-72 overflow-auto rounded-md p-3 text-xs leading-relaxed whitespace-pre-wrap"
          >
            {(state.log || []).join("\n") || "No activity yet."}
          </pre>
        </CardContent>
      </Card>
    </div>
  );
}
