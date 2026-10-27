"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toastError, toastInfo, toastSuccess } from "@/lib/toast";
import SourcesPanel, { type SourceRow } from "./SourcesPanel";
import AnswerDecisionsPanel, { type AnswerDecision } from "./AnswerDecisionsPanel";
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
  Globe,
  Layers,
  ListFilter,
  Loader2,
  Mail,
  MapPin,
  Play,
  Search,
  Sparkles,
  Square,
  Trash2,
  Upload,
} from "lucide-react";
import { POPULAR_COUNTRIES, ALL_COUNTRIES } from "@/lib/countries";

type PendingQuestion = {
  key: string;
  question: string;
  options: string[];
  company: string;
  job: string;
  seen: number;
  reason?: string;
};

type Status = {
  submit_without_review?: boolean;
  running?: boolean;
  progress?: { percent: number; label: string } | null;
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

type ProbeResult = Pick<Provider, "model" | "ok" | "detail" | "installed" | "path">;

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

type OutcomeItem = {
  job_id: string;
  status: string;
  company: string;
  title: string;
  email_date: string | null;
  subject: string | null;
  link: string | null;
  url: string | null;
  kind: string | null;
  reason: string | null;
  hours: number | null;
};

type Report = {
  actions: OutcomeItem[];
  rejections: { total: number; kinds: Record<string, number>; reasons: Record<string, number>; recent: OutcomeItem[] };
  tips: string[];
  segments?: Segment[];
  paused_segments?: string[];
};

// persona x level x region x source x company size, from `report --json`
type Segment = {
  segment: string;
  persona: string;
  level: string;
  region: string;
  source: string;
  size: string;
  applied: number;
  responses: number;
  positive: number;
  rejected: number;
  response_rate: number;
  positive_rate: number;
  rejection_rate: number;
};

const pct = (x: number) => `${Math.round(x * 100)}%`;

// Links come from scraped postings and mail, so only http(s) (and mailto where a mail
// field is expected) may reach an href; "javascript:" and "data:" never do.
function safeHref(url?: string | null, mail = false): string | undefined {
  if (!url) return undefined;
  try {
    const protocol = new URL(url).protocol;
    return protocol === "https:" || protocol === "http:" || (mail && protocol === "mailto:") ? url : undefined;
  } catch {
    return undefined;
  }
}

const clampInt = (text: string, lo: number, hi: number) => Math.max(lo, Math.min(hi, Math.trunc(Number(text)) || lo));

const ACTION_LABEL: Record<string, string> = { interview: "Interview", assessment: "Test / next stage", offer: "Offer" };
const KIND_LABEL: Record<string, string> = { auto: "auto", template: "template after review", manual: "manual", unclear: "unclear" };

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
  answerDecisions?: AnswerDecision[];
  providers?: Provider[];
  log?: string[];
  discovering?: boolean;
  error?: string;
  outreach?: Outreach;
  followups?: Followups;
  // an array on success, { error } when `resume list` failed
  resumes?: Resume[] | { error: string };
  report?: Report;
  // per-source switches and statistics from `sources --json`
  sources?: SourceRow[];
};

const POLL_MS = 5000;
// how long Apply / Find stay locked after a start while the bot has not reported running yet
const START_WAIT_MS = 15000;
const FETCH_TIMEOUT_MS = 35000;
// Parallel Chrome workers per apply run; more is faster but stalls more often on a
// shared LLM proxy. Mirrors RECOMMENDED_WORKERS in tools/autopilot.py.
const RECOMMENDED_WORKERS = 3;

export default function AutopilotClient() {
  const [tab, setTab] = useState("run");
  const [state, setState] = useState<State>({});
  // kept as text so clearing the field to retype does not snap it back to 1
  const [batchText, setBatchText] = useState("5");
  const [workersText, setWorkersText] = useState(String(RECOMMENDED_WORKERS));
  const batch = clampInt(batchText, 1, 25);
  const workers = clampInt(workersText, 1, 8);
  const [personaChoice, setPersona] = useState("all");
  const [selectedCountry, setSelectedCountry] = useState("all");
  const [customLocation, setCustomLocation] = useState("");
  const isCustom = selectedCountry === "custom";
  const activeLocation = isCustom ? (customLocation.trim() || undefined) : (selectedCountry === "all" ? undefined : selectedCountry);
  const [busy, setBusy] = useState(false);
  // true from a successful start until the poll reports running (or START_WAIT_MS passes)
  const [starting, setStarting] = useState(false);
  const [modelDraft, setModelDraft] = useState<Record<string, string>>({});
  const arrowModel = useRef(false);
  const committing = useRef(new Set<string>());
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
      [next.name]: { model: next.model, ok: next.ok, detail: next.detail, installed: next.installed, path: next.path },
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
  const inFlight = useRef(false);

  const load = useCallback(async () => {
    // a slow backend must not stack up overlapping polls
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const res = await fetch("/api/autopilot", { cache: "no-store", signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
      const data = await res.json();
      // keep the last good data on a transient failure — replacing the whole state
      // blanked every card because one of the four CLI calls hiccuped
      if (!res.ok) setState((prev) => ({ ...prev, error: data.error || `HTTP ${res.status}` }));
      else setState(data);
    } catch {
      setState((prev) => ({ ...prev, error: "Autopilot is not reachable" }));
    } finally {
      inFlight.current = false;
    }
  }, []);

  useEffect(() => {
    load();
    // no polling for a hidden tab; catch up as soon as it is visible again
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, POLL_MS);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  useEffect(() => {
    if (!starting) return;
    if (state.status?.running) {
      setStarting(false);
      return;
    }
    const t = setTimeout(() => setStarting(false), START_WAIT_MS);
    return () => clearTimeout(t);
  }, [starting, state.status?.running]);

  useEffect(() => {
    const el = logRef.current;
    if (!el) return;
    // follow the tail only while the reader is already at the bottom — the 5s poll
    // hands back a fresh array every time, so an unconditional scroll made it
    // impossible to read anything above the last line
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    if (atBottom) el.scrollTop = el.scrollHeight;
  }, [state.log, tab]);

  // resolves to the response body on success, null on failure (a toast was shown)
  async function send(action: string, extra: Record<string, unknown> = {}): Promise<Record<string, unknown> | null> {
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
        return null;
      }
      return data;
    } catch {
      toastError("Autopilot is not reachable");
      return null;
    } finally {
      setBusy(false);
      load();
    }
  }

  async function saveAnswer(q: PendingQuestion) {
    const answer = (answers[q.key] || "").trim();
    if (!answer) {
      toastError("Type an answer first");
      return;
    }
    const ok = await send("answer", { key: q.key, label: q.question, answer });
    if (ok) {
      toastSuccess("Saved — the bot reuses this on every future application");
      setAnswers((prev) => ({ ...prev, [q.key]: "" }));
    }
  }

  async function startRun(kind: "apply" | "discover") {
    const personaLabel = persona === "all" ? "all personas" : (resumes.find((r) => r.key === persona)?.role_title || persona);
    const where = activeLocation ?? "worldwide";
    if (
      kind === "apply" && state.status?.submit_without_review === true &&
      !window.confirm(
        `Submit up to ${batch} real applications now?\nPersona: ${personaLabel}\nLocation: ${where}\nWorkers: ${workers}\n\nSubmitted applications cannot be undone.`,
      )
    ) {
      return;
    }
    const data = await send(kind, { max: batch, workers, persona: persona === "all" ? undefined : persona, location: activeLocation });
    if (data) {
      setStarting(true);
      toastSuccess(kind === "apply" ? `Apply run started (up to ${batch}, ${where})` : `Job search started (${where})`);
    }
  }

  async function runSources(names: string[]) {
    const data = await send("discover", { persona: persona === "all" ? undefined : persona, sources: names });
    if (data) {
      setStarting(true);
      toastSuccess(`Discovery started (${names.length} source${names.length === 1 ? "" : "s"})`);
    }
  }

  async function stopRun() {
    const data = await send("stop");
    if (!data) return;
    if (data.note) toastInfo("Nothing is running");
    else toastSuccess("Stop requested. The run ends after the current application.");
  }

  async function copyNote(note: string) {
    if (!navigator.clipboard?.writeText) {
      toastError("Copying is not available here. Select the note and copy it by hand.");
      return;
    }
    try {
      await navigator.clipboard.writeText(note);
      toastSuccess(`Invite note copied (${note.length} chars)`);
    } catch {
      toastError("Could not copy the note");
    }
  }

  // a model change writes config.json, so it must be a decision, not an arrow-key pass-through
  function commitModel(name: string, value: string, current: string) {
    arrowModel.current = false;
    const clear = () => setModelDraft((prev) => {
      const next = { ...prev };
      delete next[name];
      return next;
    });
    if (value === current) clear();
    else if (!committing.current.has(name)) {
      committing.current.add(name);
      void send("model", { name, model: value }).then(() => {
        committing.current.delete(name);
        clear();
      });
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
      color: "text-primary bg-primary/10",
    },
    {
      label: "Submitted",
      description: "Successfully submitted",
      value: latency.submitted ?? 0,
      icon: CheckCircle2,
      color: "text-primary bg-primary/10",
    },
    {
      label: "Attempts logged",
      description: "Total apply attempts",
      value: status.applied_log ?? 0,
      icon: Clock,
      color: "text-primary bg-primary/10",
    },
    {
      label: "Jobs known",
      description: "Saved across boards",
      value: status.jobs ?? 0,
      icon: Briefcase,
      color: "text-primary bg-primary/10",
    },
  ];

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 py-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex flex-col gap-2">
          <p className="text-sm font-medium text-primary">APPLICATION WORKSPACE</p>
          <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">Autopilot</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">Your queue, applications and next steps in one place.</p>
        </div>
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          {status.running ? <Loader2 className="size-4 animate-spin text-primary" aria-hidden="true" /> : <Clock className="size-4" aria-hidden="true" />}
          {status.running ? "Run in progress" : state.status ? "Ready to run" : "Connecting…"}
        </p>
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

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {stats.map((s) => {
          const Icon = s.icon;
          return (
            <Card key={s.label} className="overflow-hidden border border-border/70 shadow-sm transition-all hover:shadow-md">
              <CardContent className="p-4 sm:p-5">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">
                    {s.label}
                  </span>
                  <div className={`hidden size-8 items-center justify-center rounded-lg sm:flex ${s.color}`}>
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

      <Tabs value={tab} onValueChange={setTab} className="min-w-0">
        <TabsList aria-label="Autopilot sections" className="h-auto w-full flex-wrap justify-start gap-1">
          <TabsTrigger value="run">Run & activity</TabsTrigger>
          <TabsTrigger value="review">Review ({state.manual?.waiting_for_you ?? 0})</TabsTrigger>
          <TabsTrigger value="replies">Replies ({state.report?.actions?.length ?? 0})</TabsTrigger>
          <TabsTrigger value="sources">Sources</TabsTrigger>
          <TabsTrigger value="resumes">Resumes</TabsTrigger>
          <TabsTrigger value="providers">AI providers</TabsTrigger>
        </TabsList>
        <TabsContent value="run" className="flex flex-col gap-6 data-[state=inactive]:hidden">
      <div className="flex flex-col gap-4 rounded-xl border border-border/70 bg-card p-5 shadow-sm">
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex items-center gap-2.5">
              <h2 className="text-xl font-semibold tracking-tight">Run settings</h2>
              {status.running ? (
                <span className="inline-flex items-center gap-1.5 rounded-full bg-primary/15 px-2.5 py-0.5 text-xs font-semibold text-primary">
                  <span className="h-2 w-2 rounded-full bg-primary animate-pulse" />
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
              Choose a profile and location, then start a batch.
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2.5 xl:max-w-3xl">
            <div className="flex items-center rounded-lg border border-input bg-background px-3 py-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
              <label htmlFor="batch" className="text-muted-foreground mr-2 text-xs font-medium">Batch</label>
              <input
                id="batch"
                type="number"
                min={1}
                max={25}
                value={batchText}
                onChange={(e) => setBatchText(e.target.value)}
                onBlur={() => setBatchText(String(batch))}
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
                value={workersText}
                aria-describedby="workers-hint"
                onChange={(e) => setWorkersText(e.target.value)}
                onBlur={() => setWorkersText(String(workers))}
                className="w-10 bg-transparent text-sm font-semibold focus:outline-none"
              />
              <span id="workers-hint" className="text-muted-foreground text-xs">
                {workers === RECOMMENDED_WORKERS ? "recommended" : `${RECOMMENDED_WORKERS} recommended`}
              </span>
            </div>
            {/* Country / Location Selector */}
            <div className="flex items-center rounded-lg border border-input bg-background px-2.5 py-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
              <Globe className="h-4 w-4 text-primary mr-1.5 shrink-0" />
              <label htmlFor="country-select" className="text-muted-foreground mr-1.5 text-xs font-medium">Country</label>
              <select
                id="country-select"
                value={selectedCountry}
                onChange={(e) => setSelectedCountry(e.target.value)}
                className="bg-transparent text-sm font-semibold focus:outline-none cursor-pointer max-w-44 truncate"
              >
                <optgroup label="Popular & Target">
                  {POPULAR_COUNTRIES.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
                </optgroup>
                <optgroup label="All Countries (A-Z)">
                  {ALL_COUNTRIES.map((c) => (
                    <option key={c.code} value={c.code}>
                      {c.name}
                    </option>
                  ))}
                </optgroup>
                <option value="custom">Custom City / Region...</option>
              </select>
            </div>
            {isCustom && (
              <div className="flex items-center rounded-lg border border-input bg-background px-2.5 py-1.5 shadow-sm focus-within:ring-2 focus-within:ring-ring">
                <MapPin className="h-4 w-4 text-primary mr-1.5 shrink-0" />
                <input
                  type="text"
                  aria-label="Custom city or region"
                  placeholder="e.g. Belgrade, Novi Sad"
                  value={customLocation}
                  onChange={(e) => setCustomLocation(e.target.value)}
                  className="bg-transparent text-sm font-semibold focus:outline-none w-40"
                />
              </div>
            )}
            <Button
              disabled={busy || starting || status.running}
              onClick={() => startRun("apply")}
              className="gap-1.5 font-medium shadow-sm"
            >
              {busy || starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4 fill-current" />}
              {status.submit_without_review === true ? `Apply to ${batch}` : `Dry run ${batch}`}
            </Button>
            <Button
              variant="secondary"
              disabled={busy || starting || status.running}
              onClick={() => startRun("discover")}
              className="gap-1.5 font-medium"
            >
              <Search className="h-4 w-4" />
              Find new jobs
            </Button>
            <Button
              variant="destructive"
              disabled={busy || !status.running}
              onClick={() => stopRun()}
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
          <div role="group" aria-label="Target persona" className="flex flex-wrap items-center gap-1.5">
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
                <span className={`ml-1 rounded-full px-1.5 py-0.5 text-xs ${
                  persona === p.key ? "bg-foreground/10 text-primary-foreground" : "bg-muted-foreground/15 text-muted-foreground"
                }`}>
                  {p.jobs}
                </span>
              </button>
            ))}
          </div>
        </div>

        {/* Multi-Agent Country Quick-Pick Switcher */}
        <div className="flex flex-wrap items-center justify-between border-t border-border/50 pt-3 gap-2">
          <div className="flex items-center gap-2 text-xs text-muted-foreground font-medium">
            <Globe className="h-3.5 w-3.5 text-primary" />
            <span>Target Country:</span>
          </div>
          <div role="group" aria-label="Target country" className="flex flex-wrap items-center gap-1.5">
            {[
              { code: "serbia", label: "Serbia" },
              { code: "all", label: "Worldwide" },
              { code: "remote", label: "Remote" },
              { code: "germany", label: "Germany" },
              { code: "united kingdom", label: "UK" },
              { code: "united states", label: "USA" },
              { code: "netherlands", label: "Netherlands" },
              { code: "cyprus", label: "Cyprus" },
              { code: "poland", label: "Poland" },
              { code: "spain", label: "Spain" },
              { code: "france", label: "France" },
              { code: "canada", label: "Canada" },
              { code: "united arab emirates", label: "UAE" },
            ].map((c) => (
              <button
                key={c.code}
                type="button"
                aria-pressed={selectedCountry === c.code}
                onClick={() => setSelectedCountry(c.code)}
                className={`inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                  selectedCountry === c.code
                    ? "bg-primary text-primary-foreground shadow-sm font-semibold"
                    : "bg-muted/60 text-muted-foreground hover:bg-muted hover:text-foreground"
                }`}
              >
                <span>{c.label}</span>
              </button>
            ))}
          </div>
        </div>
      </div>

      {status.running && status.progress ? (
        <div className="flex flex-col gap-2" role="status">
          <p className="text-sm text-muted-foreground">{status.progress.label}</p>
          <Progress value={status.progress.percent} aria-label="Batch progress" />
        </div>
      ) : null}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Live activity</CardTitle>
          <CardDescription>Updates every five seconds. Only confirmed submissions count as sent.</CardDescription>
        </CardHeader>
        <CardContent>
          <pre
            ref={logRef}
            tabIndex={0}
            role="region"
            aria-label="Live activity log"
            className="bg-muted h-72 overflow-auto rounded-md p-3 text-xs leading-relaxed whitespace-pre-wrap break-words focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {(state.log || []).join("\n") || "No activity yet."}
          </pre>
        </CardContent>
      </Card>
        </TabsContent>
        <TabsContent value="review" className="flex flex-col gap-6 data-[state=inactive]:hidden">
      <Card className="border border-border/70 shadow-sm">
        <CardHeader className="flex flex-row items-start justify-between pb-3">
          <div>
            <CardTitle className="text-base font-semibold flex items-center gap-2">
              Needs your click
              {manualItems.length > 0 && (
                <span className="rounded-full bg-warning/15 px-2.5 py-0.5 text-xs font-bold text-warning">
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
                        href={safeHref(m.apply_url)}
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
              const options = q.options || [];
              return (
                <div key={q.key} className="border-border flex flex-col gap-2 border-t pt-4 first:border-t-0 first:pt-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="text-sm font-medium break-words">{q.question}</span>
                    <span className="text-muted-foreground text-xs">
                      {q.company || "any company"} · asked {q.seen}x
                    </span>
                  </div>
                  {q.reason ? <p lang="en" className="text-sm text-muted-foreground">Why an answer is needed: {q.reason}</p> : null}
                  {options.length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {options.map((o) => (
                        <Button
                          key={o}
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => setAnswers((prev) => ({ ...prev, [q.key]: o }))}
                        >
                          {o}
                        </Button>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2">
                    <Input
                      value={answers[q.key] || ""}
                      aria-label={`Your answer: ${q.question}`}
                      placeholder="Your answer"
                      onChange={(e) =>
                        setAnswers((prev) => ({ ...prev, [q.key]: e.target.value }))
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

        <AnswerDecisionsPanel decisions={Array.isArray(state.answerDecisions) ? state.answerDecisions : []} />
        </TabsContent>
        <TabsContent value="replies" className="flex flex-col gap-6 data-[state=inactive]:hidden">
      <Card
        className={
          (state.report?.actions?.length ?? 0) > 0
            ? "border-2 border-primary shadow-sm"
            : "border border-border/70 shadow-sm"
        }
      >
        <CardHeader className="pb-3">
          <CardTitle className="text-base font-semibold flex items-center gap-2">
            <Mail className="h-4 w-4 text-primary" aria-hidden="true" />
            Replies from employers
          </CardTitle>
          <CardDescription className="mt-1">
            From your mailbox (run <code>inbox --apply</code>). Invitations need you: open the link and do the next step.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <section aria-labelledby="actions-heading">
            <h3 id="actions-heading" className="font-semibold">
              Needs your action ({state.report?.actions?.length ?? 0})
            </h3>
            {(state.report?.actions?.length ?? 0) === 0 ? (
              <p className="text-muted-foreground">No invitations yet. Rejections and their reasons are listed below.</p>
            ) : (
              <ul className="mt-2 space-y-2">
                {state.report!.actions.map((a) => (
                  <li key={`${a.job_id}-${a.status}`} className="rounded-md border-2 border-primary bg-primary/10 p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold">
                        <CheckCircle2 className="mr-1 inline h-4 w-4 text-primary" aria-hidden="true" />
                        {ACTION_LABEL[a.status] ?? a.status}: {a.company} — {a.title}
                      </span>
                      <span className="text-muted-foreground text-xs">{(a.email_date ?? "").slice(0, 10)}</span>
                    </div>
                    {a.subject ? <p className="mt-1 text-xs text-muted-foreground">{a.subject}</p> : null}
                    <div className="mt-2 flex flex-wrap gap-2">
                      {safeHref(a.link) ?? safeHref(a.url) ? (
                        <a
                          href={safeHref(a.link) ?? safeHref(a.url)}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                          Open {safeHref(a.link) ? "invitation" : "posting"}
                        </a>
                      ) : null}
                      <Button size="sm" variant="secondary" disabled={busy} onClick={() => send("action_done", { jobId: a.job_id })}>
                        Mark as done
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="rejections-heading">
            <h3 id="rejections-heading" className="font-semibold">
              Rejections: {state.report?.rejections?.total ?? 0}
            </h3>
            {state.report && state.report.rejections.total > 0 ? (
              <>
                <p className="text-muted-foreground mt-1">
                  {Object.entries(state.report.rejections.kinds)
                    .map(([k, v]) => `${KIND_LABEL[k] ?? k}: ${v}`)
                    .join(" · ")}
                </p>
                <p className="text-muted-foreground">
                  Reasons:{" "}
                  {Object.entries(state.report.rejections.reasons)
                    .map(([k, v]) => `${k} ${v}`)
                    .join(" · ")}
                </p>
                <ul className="mt-2 divide-y divide-border/60">
                  {state.report.rejections.recent.map((r) => (
                    <li key={`${r.job_id}-${r.status}`} className="flex flex-wrap justify-between gap-2 py-1.5">
                      <span className="min-w-0 break-words">
                        {r.company} — {r.title}
                      </span>
                      <span className="text-muted-foreground text-xs">
                        {KIND_LABEL[r.kind ?? "unclear"] ?? r.kind} · {r.reason ?? "—"}
                        {r.hours != null ? ` · ${Math.round(r.hours)} h` : ""}
                      </span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </section>

          {(state.report?.segments?.length ?? 0) > 0 ? (
            <section aria-labelledby="segments-heading">
              <h3 id="segments-heading" className="font-semibold">Results by segment</h3>
              <p className="text-muted-foreground mt-1">
                A segment with 30+ applications and no interview, test or offer is paused: new applications skip it.
                Paused now: {state.report!.paused_segments?.length ?? 0}.
              </p>
              <div
                className="mt-2 overflow-x-auto rounded-md border border-border/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                role="region"
                aria-labelledby="segments-heading"
                tabIndex={0}
              >
                <table className="w-full min-w-[40rem] text-xs">
                  <caption className="sr-only">
                    Response, interview and rejection rates per persona, level, region, source and company size. Top 15
                    segments by applications.
                  </caption>
                  <thead className="bg-muted/50 text-left">
                    <tr>
                      <th scope="col" className="px-2 py-1.5 font-medium">Segment</th>
                      <th scope="col" className="px-2 py-1.5 text-right font-medium">Applied</th>
                      <th scope="col" className="px-2 py-1.5 text-right font-medium">Replied</th>
                      <th scope="col" className="px-2 py-1.5 text-right font-medium">Interview</th>
                      <th scope="col" className="px-2 py-1.5 text-right font-medium">Rejected</th>
                      <th scope="col" className="px-2 py-1.5 font-medium">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {state.report!.segments!.slice(0, 15).map((s) => {
                      const paused = state.report!.paused_segments?.includes(s.segment);
                      return (
                        <tr key={s.segment}>
                          <th scope="row" className="px-2 py-1.5 text-left font-normal break-words">
                            {s.persona} · {s.level} · {s.region} · {s.source}
                            {s.size !== "?" ? ` · ${s.size}` : ""}
                          </th>
                          <td className="px-2 py-1.5 text-right tabular-nums">{s.applied}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{pct(s.response_rate)}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{pct(s.positive_rate)}</td>
                          <td className="px-2 py-1.5 text-right tabular-nums">{pct(s.rejection_rate)}</td>
                          <td className="px-2 py-1.5">
                            {paused ? <span className="rounded border border-border px-1.5 py-0.5 font-medium">Paused</span> : "Active"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-muted-foreground mt-1 text-xs">
                Showing {Math.min(15, state.report!.segments!.length)} of {state.report!.segments!.length} segments. The
                table scrolls sideways on narrow screens.
              </p>
            </section>
          ) : null}

          {(state.report?.tips?.length ?? 0) > 0 ? (
            <section aria-labelledby="tips-heading">
              <h3 id="tips-heading" className="font-semibold">What to improve</h3>
              <ul className="mt-1 list-disc space-y-1 pl-5">
                {state.report!.tips.map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ul>
            </section>
          ) : null}
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
              Recruiter search links and message drafts for recently submitted applications. Review the recipient and content before sending.
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
                        <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-mono text-muted-foreground">
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
                      <a href={safeHref(item.recruiter_search_url)} target="_blank" rel="noreferrer">
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
                      <a href={safeHref(item.manager_search_url)} target="_blank" rel="noreferrer">
                        Manager
                        <ExternalLink className="h-2.5 w-2.5 opacity-60" />
                      </a>
                    </Button>
                    {safeHref(item.mailto_url, true) && (
                      <Button
                        variant="outline"
                        size="sm"
                        className="h-8 text-xs gap-1 text-primary"
                        asChild
                      >
                        <a href={safeHref(item.mailto_url, true)}>
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
                        onClick={() => copyNote(item.connection_invite_note || "")}
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

        </TabsContent>
        <TabsContent value="sources" className="flex flex-col gap-6 data-[state=inactive]:hidden">
          <SourcesPanel
            sources={Array.isArray(state.sources) ? state.sources : []}
            persona={persona}
            locked={starting || Boolean(status.running)}
            busy={busy}
            onToggle={(name, enabled) => void send("source_toggle", { name, enabled })}
            onRun={runSources}
          />
        </TabsContent>
        <TabsContent value="resumes" className="flex flex-col gap-6 data-[state=inactive]:hidden">
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
                        <span className="inline-flex items-center gap-1 rounded-full bg-primary/15 px-2 py-0.5 text-xs font-medium text-primary">
                          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                          PDF present
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-xs font-medium text-warning">
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
                <span className={`inline-flex items-start gap-1.5 ${uploadMsg.ok ? "text-primary" : "text-destructive"}`}>
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

        </TabsContent>
        <TabsContent value="providers" className="flex flex-col gap-6 data-[state=inactive]:hidden">
      <Card className="border border-border/70 shadow-sm">
        <CardHeader className="flex flex-col gap-3 pb-4 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <div className="flex items-center gap-2">
              <CardTitle className="text-base font-semibold">AI providers</CardTitle>
              <span className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
                Fallback order
              </span>
            </div>
            <CardDescription lang="en" className="mt-1">
              Auto tries available models with fallback on errors, timeouts or limits, then moves to the next provider. Cooldowns persist between requests and processes.
            </CardDescription>
          </div>
          <Button
            variant="secondary"
            size="sm"
            disabled={busy}
            lang="en"
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
            Test all
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col divide-y divide-border/60 p-0">
          {[...(state.providers || [])]
            .map((polled) => {
              const probe = probes[polled.name];
              const p = { ...polled, ...(probe?.model === polled.model ? probe : {}) };
              const missing = p.kind === "cli" && p.installed === false;
              const noKey = !!p.key_env && p.has_key === false;

              const meta: { label: string; desc: string; tag: string } = {
                antigravity: {
                  label: "Antigravity Tools (Local API)",
                  desc: "Local model bridge through an Antigravity-compatible proxy.",
                  tag: "Local Bridge",
                },
                claude: {
                  label: "Claude Code (CLI)",
                  desc: "Uses your locally installed Claude CLI and its configured authentication.",
                  tag: "CLI Tool",
                },
                codex: {
                  label: "OpenAI Codex (CLI)",
                  desc: "Uses your locally installed Codex CLI and its configured authentication.",
                  tag: "CLI Tool",
                },
                grok: {
                  label: "xAI Grok (CLI)",
                  desc: "Uses your locally installed Grok CLI.",
                  tag: "CLI Tool",
                },
                groq: {
                  label: "Groq Cloud (LPU)",
                  desc: "Cloud inference through the Groq API. Available models depend on your account.",
                  tag: "Cloud API",
                },
                llm7: {
                  label: "LLM7.io Gateway",
                  desc: "OpenAI-compatible model gateway. List models to see current availability.",
                  tag: "Cloud API",
                },
                lmstudio: {
                  label: "LM Studio (Offline)",
                  desc: "Local model server. Load a model in LM Studio and enable its API server.",
                  tag: "Local LLM",
                },
                openrouter: {
                  label: "OpenRouter Gateway",
                  desc: "Model gateway through OpenRouter. Availability and pricing depend on the selected model.",
                  tag: "Cloud API",
                },
                opencode: {
                  label: "OpenCode (Free / Go)",
                  desc: "Auto uses the configured free-model pool with fallback. Select a specific model explicitly; paid Go models require separate setup and explicit selection.",
                  tag: "CLI Tool",
                },
                tokenharbor: {
                  label: "Token Harbor Hub",
                  desc: "OpenAI-compatible model gateway using your own account and credentials.",
                  tag: "Cloud API",
                },
              }[p.name] || {
                label: p.name,
                desc: p.kind === "cli" ? "Locally installed CLI provider." : "OpenAI-compatible API service.",
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
                !p.enabled ? { text: "DISABLED", cls: "bg-muted text-muted-foreground" }
                : missing ? { text: "NOT INSTALLED", cls: "bg-warning/15 text-warning" }
                : noKey ? { text: "KEY REQUIRED", cls: "bg-warning/15 text-warning" }
                : p.ok === true ? { text: "WORKING", cls: "bg-primary/15 text-primary" }
                : p.ok === false ? { text: "ERROR", cls: "bg-destructive/15 text-destructive" }
                : { text: "READY TO TEST", cls: "bg-primary/15 text-primary" };

              const keyStatus = !p.key_env
                ? { text: "No API key needed", cls: "text-muted-foreground" }
                : p.has_key
                ? { text: "✓ Key configured", cls: "text-primary font-medium" }
                : { text: "⚠ Key missing", cls: "text-warning font-medium" };

              return (
                <div
                  key={p.name}
                  className={`border-t ${borderColor} p-4 transition-colors hover:bg-muted/20`}
                >
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="flex flex-col gap-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-sm font-semibold tracking-tight text-foreground">
                          {meta.label}
                        </span>
                        <span className="font-mono text-xs text-muted-foreground">({p.name})</span>
                        <span className="rounded bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground uppercase">
                          {meta.tag}
                        </span>
                        <span lang="en" className={`rounded-full px-2 py-0.5 text-xs font-bold tracking-wider ${badge.cls}`}>
                          {badge.text}
                        </span>
                        <span lang="en" className={`text-xs ${keyStatus.cls}`}>
                          {keyStatus.text}
                        </span>
                      </div>
                      <p lang="en" className="text-muted-foreground text-xs leading-relaxed">
                        {meta.desc}
                      </p>
                      <div className="text-muted-foreground flex flex-wrap items-center gap-1.5 text-xs">
                        <span className="font-medium text-foreground/80">
                          {p.kind === "cli" ? (p.path || "CLI not found in PATH") : p.url}
                        </span>
                        {p.model && <span lang="en">· model: <span className="font-mono text-foreground">{p.model}</span></span>}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0 self-start sm:self-center">
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={testing === p.name || !p.enabled}
                        onClick={() => testOne(p.name)}
                        lang="en"
                        className="h-8 px-3 text-xs font-medium"
                      >
                        {testing === p.name ? (
                          <><Loader2 className="mr-1.5 h-3 w-3 animate-spin" />Testing…</>
                        ) : "Test"}
                      </Button>
                      <Button
                        variant={p.enabled ? "default" : "secondary"}
                        size="sm"
                        disabled={busy}
                        onClick={() => send("toggle", { name: p.name, enabled: !p.enabled })}
                        lang="en"
                        className={`h-8 px-3 text-xs font-semibold ${p.enabled ? "bg-primary hover:bg-primary text-primary-foreground" : ""}`}
                      >
                        {p.enabled ? "Turn off" : "Turn on"}
                      </Button>
                    </div>
                  </div>

                  {missing && (
                    <div lang="en" className="mt-2.5 rounded-md border border-warning/30 bg-warning/10 p-2 text-xs text-warning">
                      {p.name} was not found in PATH. Install the CLI and sign in before using it.
                    </div>
                  )}

                  {p.detail && (
                    <div className={`mt-2.5 rounded-md p-2 text-xs font-mono break-all ${p.ok ? "bg-primary/10 text-primary" : "bg-destructive/10 text-destructive"}`}>
                      {p.ok ? `✓ Response: ${p.detail}` : `⚠ Error: ${p.detail}`}
                    </div>
                  )}

                  <div className="mt-3 flex flex-wrap items-center gap-2.5">
                    <span lang="en" className="text-muted-foreground text-xs font-medium">Model:</span>
                    <select
                      aria-label={`Model for ${p.name}`}
                      className="border-input bg-background h-8 min-w-44 rounded-md border px-2.5 text-xs font-medium focus:outline-none focus:ring-1 focus:ring-primary"
                      value={modelDraft[p.name] ?? (p.model || "")}
                      onFocus={() => loadModels(p.name)}
                      onKeyDown={(e) => {
                        if (e.key === "ArrowUp" || e.key === "ArrowDown") arrowModel.current = true;
                        if (e.key === "Enter") commitModel(p.name, e.currentTarget.value, p.model || "");
                      }}
                      onBlur={(e) => commitModel(p.name, e.currentTarget.value, p.model || "")}
                      onChange={(e) => {
                        const value = e.target.value;
                        setModelDraft((prev) => ({ ...prev, [p.name]: value }));
                        // a mouse pick is a decision; arrow keys only browse until Enter or blur
                        if (!arrowModel.current) commitModel(p.name, value, p.model || "");
                      }}
                    >
                      <option lang="en" value={p.model || ""}>{p.model || "Default model"}</option>
                      {(models[p.name] || [])
                        .filter((m) => m !== p.model)
                        .map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                    </select>
                    <Button variant="ghost" size="sm" lang="en" onClick={() => loadModels(p.name)} className="h-8 px-2.5 text-xs">
                      List models
                    </Button>
                    {models[p.name] && (
                      <span lang="en" className="text-muted-foreground text-xs">
                        available: {models[p.name].filter((model) => model !== "auto").length}
                      </span>
                    )}
                  </div>

                  {p.key_env && (
                    <div className="mt-2.5 flex items-center gap-2">
                      <div className="relative flex-1">
                        <Input
                          type="password"
                          aria-label={`API key ${p.key_env}`}
                          autoComplete="off"
                          value={keys[p.key_env] || ""}
                          placeholder={p.has_key ? `${p.key_env} configured (paste to replace)` : `Paste key (${p.key_env})`}
                          onChange={(e) => setKeys((prev) => ({ ...prev, [p.key_env!]: e.target.value }))}
                          className="h-8 text-xs pr-16"
                        />
                        {p.has_key && (
                          <span className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded bg-primary/15 px-1.5 py-0.5 text-xs font-bold text-primary uppercase">
                            SET
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
                            toastSuccess(`${p.key_env}: key saved in secrets.env; applies to the next run`);
                            setKeys((prev) => ({ ...prev, [p.key_env!]: "" }));
                          }
                        }}
                        lang="en"
                        className="h-8 px-3.5 text-xs font-medium shrink-0"
                      >
                        Save key
                      </Button>
                    </div>
                  )}
                </div>
              );
            })}
        </CardContent>
      </Card>

        </TabsContent>
      </Tabs>
    </div>
  );
}
