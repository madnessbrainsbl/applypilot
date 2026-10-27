"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { toastError } from "@/lib/toast";
import { Loader2, RefreshCw, Search } from "lucide-react";

export type SourceRow = {
  source: string;
  known: boolean;
  enabled: boolean | null;
  every_hours: number | null;
  runs: number;
  last_run: string | null;
  last_found: number;
  last_stored: number;
  last_errors: number;
  found_total: number;
  stored_total: number;
  jobs: number;
  queued: number;
  submitted: number;
  interviews: number;
  adapters?: {
    adapter: string;
    persona: string;
    status: "ok" | "partial" | "error" | "cooldown" | "disabled";
    raw: number;
    kept: number;
    errors: number;
    t: string;
    retry_at: string | null;
  }[];
};

type QueueGate = { gate: string; label: string; count: number; unit: string; hint: string };
type QueuePreview = {
  queued_status: number;
  eligible: number;
  passed_gates: number;
  queue: number;
  other_filters: number;
  gates: QueueGate[];
  by_persona: Record<string, number>;
};

const QUEUE_TIMEOUT_MS = 190_000;
const fmt = (n: number) => n.toLocaleString("en-US");

function when(iso: string | null) {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("en-GB", { dateStyle: "short", timeStyle: "short" });
}

export default function SourcesPanel({
  sources,
  persona,
  locked,
  busy,
  onToggle,
  onRun,
}: {
  sources: SourceRow[];
  persona: string;
  // a run is active or starting: the start guard would refuse a second one
  locked: boolean;
  busy: boolean;
  onToggle: (name: string, enabled: boolean) => void;
  onRun: (names: string[]) => void;
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [queue, setQueue] = useState<QueuePreview | null>(null);
  const [queueState, setQueueState] = useState<"idle" | "loading" | "error">("idle");
  const [queueNote, setQueueNote] = useState("");
  const seq = useRef(0);

  const loadQueue = useCallback(
    async (refresh: boolean) => {
      const mine = ++seq.current;
      setQueueState("loading");
      try {
        const res = await fetch("/api/autopilot", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "queue", persona, refresh }),
          signal: AbortSignal.timeout(QUEUE_TIMEOUT_MS),
        });
        const data = await res.json();
        if (mine !== seq.current) return;
        if (!res.ok || data.error) throw new Error(data.error || `HTTP ${res.status}`);
        setQueue(data.queue as QueuePreview);
        setQueueState("idle");
      } catch (e) {
        if (mine !== seq.current) return;
        setQueueState("error");
        setQueueNote(e instanceof Error ? e.message : "Queue preview failed");
      }
    },
    [persona],
  );

  useEffect(() => {
    loadQueue(false);
  }, [loadQueue]);

  const known = sources.filter((s) => s.known);
  const others = sources.filter((s) => !s.known && s.jobs > 0);
  const biggest = queue?.gates.length ? [...queue.gates].filter((g) => g.unit === "vacancies").sort((a, b) => b.count - a.count)[0] : undefined;

  function toggle(name: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function run() {
    const names = picked.size ? [...picked] : known.filter((s) => s.enabled).map((s) => s.source);
    if (!names.length) {
      toastError("Every source is switched off");
      return;
    }
    onRun(names);
  }

  const head = (label: string, right = false) => (
    <TableHead scope="col" className={right ? "text-right" : undefined}>
      {label}
    </TableHead>
  );

  return (
    <>
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Queue preview</CardTitle>
            <CardDescription>
              What the next apply run would take, after every filter. Read-only: nothing is submitted or changed.
            </CardDescription>
          </div>
          <Button variant="outline" size="sm" disabled={queueState === "loading"} onClick={() => loadQueue(true)} className="gap-1.5">
            {queueState === "loading" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <RefreshCw className="h-4 w-4" aria-hidden />}
            Refresh queue
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4" aria-live="polite" aria-busy={queueState === "loading"}>
          {queueState === "error" ? <p role="alert" className="text-sm text-destructive">Queue preview failed: {queueNote}</p> : null}
          {queueState === "loading" && !queue ? <p className="text-sm text-muted-foreground">Checking the backlog against every filter. This takes about 15 seconds.</p> : null}
          {queue ? (
            <>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <div className="rounded-lg border border-border/70 p-3 sm:col-span-1">
                  <dt className="text-xs text-muted-foreground">Ready to apply</dt>
                  <dd className="text-3xl font-semibold tabular-nums">{fmt(queue.queue)}</dd>
                </div>
                <div className="rounded-lg border border-border/70 p-3">
                  <dt className="text-xs text-muted-foreground">Passed all filters</dt>
                  <dd className="text-xl font-semibold tabular-nums">{fmt(queue.passed_gates)}</dd>
                </div>
                <div className="rounded-lg border border-border/70 p-3">
                  <dt className="text-xs text-muted-foreground">Eligible to check</dt>
                  <dd className="text-xl font-semibold tabular-nums">{fmt(queue.eligible)}</dd>
                </div>
                <div className="rounded-lg border border-border/70 p-3">
                  <dt className="text-xs text-muted-foreground">Stored as queued</dt>
                  <dd className="text-xl font-semibold tabular-nums">{fmt(queue.queued_status)}</dd>
                </div>
              </dl>
              {biggest ? (
                <p className="text-sm">
                  Biggest reason the queue is small: {fmt(biggest.count)} vacancies are {biggest.label}
                  {biggest.hint ? ` (${biggest.hint})` : ""}.
                </p>
              ) : null}
              <div className="min-w-0 overflow-x-auto" role="region" aria-label="Filter counts, scrolls sideways" tabIndex={0}>
                <Table className="min-w-[32rem]">
                  <TableCaption className="sr-only">How many vacancies each filter removed</TableCaption>
                  <TableHeader>
                    <TableRow>
                      {head("Filter")}
                      {head("Removed", true)}
                      {head("Unit")}
                      {head("Setting")}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {[...queue.gates].sort((a, b) => b.count - a.count).map((g) => (
                      <TableRow key={g.gate}>
                        <TableCell>{g.label}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmt(g.count)}</TableCell>
                        <TableCell>{g.unit}</TableCell>
                        <TableCell className="text-xs text-muted-foreground">{g.hint || "-"}</TableCell>
                      </TableRow>
                    ))}
                    <TableRow>
                      <TableCell>Other filters (not a direct ATS form, title fit, disqualifying phrases, duplicates)</TableCell>
                      <TableCell className="text-right tabular-nums">{fmt(queue.other_filters)}</TableCell>
                      <TableCell>vacancies</TableCell>
                      <TableCell className="text-xs text-muted-foreground">-</TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>
            </>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div>
            <CardTitle className="text-base">Sources</CardTitle>
            <CardDescription>
              Run selected or enabled sources manually. Totals count the last 90 days of runs.
            </CardDescription>
          </div>
          <Button disabled={busy || locked} onClick={run} className="gap-1.5">
            <Search className="h-4 w-4" aria-hidden />
            Run discovery now{picked.size ? ` (${picked.size})` : ""}
          </Button>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <p className="text-xs text-muted-foreground sm:hidden">The table scrolls sideways for more columns.</p>
          <div className="min-w-0 overflow-x-auto" role="region" aria-label="Discovery sources, scrolls sideways" tabIndex={0}>
            <Table className="min-w-[52rem]">
              <TableCaption className="sr-only">
                Discovery sources with their switch, last run, postings found and stored, queue size and applications sent
              </TableCaption>
              <TableHeader>
                <TableRow>
                  {head("Run")}
                  {head("Source")}
                  {head("Enabled")}
                  {head("Last run")}
                  {head("Found", true)}
                  {head("Stored", true)}
                  {head("Queued", true)}
                  {head("Sent", true)}
                  {head("Interviews", true)}
                </TableRow>
              </TableHeader>
              <TableBody>
                {known.map((s) => (
                  <TableRow key={s.source}>
                    <TableCell>
                      <label className="inline-flex cursor-pointer p-1">
                        <input
                          type="checkbox"
                          className="h-5 w-5"
                          checked={picked.has(s.source)}
                          onChange={() => toggle(s.source)}
                          aria-label={`Include ${s.source} in the next manual run`}
                        />
                      </label>
                    </TableCell>
                    <TableCell className="font-medium">
                      {s.source}
                      {s.every_hours ? <span className="block text-xs font-normal text-muted-foreground">every {s.every_hours} h</span> : null}
                      {s.source === "remote_visa_boards" || s.source === "jobwatch" ? (
                        <details className="mt-2 text-xs font-normal">
                          <summary className="cursor-pointer">{s.source === "jobwatch" ? "JobWatch health" : "Adapter health"}</summary>
                          <p className="my-2 text-muted-foreground">Last observed results. Zero matches does not mean the source was reachable.</p>
                          {s.adapters?.length ? (
                            <ul className="flex flex-col gap-3" aria-label={s.source === "jobwatch" ? "JobWatch health" : "Remote and visa board health"}>
                              {s.adapters.map((a) => (
                                <li key={`${a.persona}:${a.adapter}`}>
                                  <span className="mr-2">{a.adapter} ({a.persona})</span>
                                  <Badge variant={a.status === "error" || a.status === "partial" || a.status === "cooldown" ? "destructive" : "outline"}>
                                    {a.status === "ok" ? "Checked" : a.status === "partial" ? "Partial results" : a.status === "cooldown" ? "Cooling down" : a.status === "disabled" ? "Disabled" : "Failed"}
                                  </Badge>
                                  <span className="block text-muted-foreground">{fmt(a.raw)} found · {fmt(a.kept)} kept · {fmt(a.errors)} errors · {when(a.t)}</span>
                                  {a.retry_at && a.status !== "disabled" ? <span className="block text-muted-foreground">Retry after {when(a.retry_at)}</span> : null}
                                </li>
                              ))}
                            </ul>
                          ) : <p className="text-muted-foreground">No adapter run recorded yet.</p>}
                        </details>
                      ) : null}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Switch
                          checked={Boolean(s.enabled)}
                          disabled={busy}
                          onCheckedChange={(v) => onToggle(s.source, v)}
                          aria-label={`Enable ${s.source}`}
                        />
                        <span className="text-xs">{s.enabled ? "On" : "Off"}</span>
                      </div>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      {when(s.last_run)}
                      {s.last_errors ? <span className="block text-destructive">{s.last_errors} error{s.last_errors === 1 ? "" : "s"}</span> : null}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {fmt(s.last_found)}
                      <span className="block text-xs text-muted-foreground">of {fmt(s.found_total)}</span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {fmt(s.last_stored)}
                      <span className="block text-xs text-muted-foreground">of {fmt(s.stored_total)}</span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(s.queued)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(s.submitted)}</TableCell>
                    <TableCell className="text-right tabular-nums">{fmt(s.interviews)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          {!known.length ? <p className="text-sm text-muted-foreground">No source data yet. Run discovery once to fill this table.</p> : null}
          {others.length ? (
            <details>
              <summary className="cursor-pointer py-1 text-sm font-medium">Other ingestion sources ({others.length})</summary>
              <div className="min-w-0 overflow-x-auto" role="region" aria-label="Other sources, scrolls sideways" tabIndex={0}>
                <Table className="min-w-[28rem]">
                  <TableCaption className="sr-only">Sources filled by the board harvesters, with queue size and applications sent</TableCaption>
                  <TableHeader>
                    <TableRow>
                      {head("Source")}
                      {head("Queued", true)}
                      {head("Sent", true)}
                      {head("Interviews", true)}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {others.map((s) => (
                      <TableRow key={s.source}>
                        <TableCell className="font-medium">{s.source}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmt(s.queued)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmt(s.submitted)}</TableCell>
                        <TableCell className="text-right tabular-nums">{fmt(s.interviews)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </details>
          ) : null}
        </CardContent>
      </Card>
    </>
  );
}
