# Workspace Guidelines & Memory: search_work_world

## Garbage Collection & Storage Maintenance
- **Periodic Screenshot Cleanup**:
  - Automatically prune and clean `autopilot/screenshots/` periodically whenever it accumulates old files or exceeds ~100 items.
  - Command: `python tools/autopilot.py clean` (or call `prune_screenshots(con)`).
  - Retention policy:
    - ALWAYS KEEP: active `needs_manual = 1` screenshots for dashboard review.
    - KEEP: recent 20 submitted application screenshots and 1-hour active buffer for in-flight tasks.
    - PURGE: orphaned test files, dead listing screenshots (`dead_listing = 1`), and old completed submissions.

## Autopilot Multi-Worker Architecture
- **Concurrency**: 2-3 parallel workers via multiprocessing with isolated profiles (`autopilot/chrome-profile-w1`, `w2`, etc.).
- **Queue Prioritization**: Fresh direct ATS vacancies (Greenhouse, Workable) are prioritized. Vacancies with unresolved captchas/manual walls are routed to `needs_manual = 1` for human review and deprioritized so they do not block automated batch throughput.
- **Control & stalls**: Only the parent process reads `autopilot/control.json` (a read clears it); workers see quit through `stop_event`, and skip through a shared counter (only a worker waiting on a human acts on it). Each worker runs its own stall watchdog and `os._exit(3)`s on a hung vacancy; the parent restarts it (max `workers*3`). Non-review runs always use the pool, even with 1 worker; `--review` stays in-process.
- **Process Hygiene**: Call `in_queue.cancel_join_thread()` in queue-worker exit paths on Windows to avoid process joins hanging. Cancel `out_queue` only once `stop_event` is set — cancelling it on a normal exit drops the result put just before the worker exits.

- **Pre-flight & limits**: `tools/ats_schema.py` reads the form schema from the public Greenhouse/Ashby/Workable APIs before the browser opens (`job["_schema"]`) and skips postings with a hard knock-out (active clearance/polygraph, required US citizenship). `db.recent_company_counts` caps submissions per company over a 30-day window (3; Ashby 1). Lever is excluded from non-review runs (hCaptcha rejects automated sessions).

## Resume Library (one persona per profession)
- `python tools/autopilot.py resume add --file X.pdf --name <key> [--role "<title>"] [--regen]` copies the PDF to `autopilot/resumes/<key>.pdf` and asks the LLM for a search profile drawn only from the resume (keywords, required/excluded title terms, good/bad/niche terms), stored as `personas.<key>` in config.json. `resume list` / `resume remove --name <key>`.
- A vacancy's persona comes from `classify_persona` (best match on each persona's required title terms); `pick_cv` uploads that persona's own CV. Vacancies whose persona CV file is missing are never applied to.
- Config is written back through `_cfg_raw`/`_cfg_write` only — `read_json` injects secrets from the env and must never be dumped to disk.
- LinkedIn is a search source only (jobspy `sites`): a posting is kept when the same title exists on the company's Greenhouse/Ashby/Workable board (`ats_schema.find_on_company_board`); Easy Apply is never used.

## SQLite Database & QA Memory Caching
- **High-Performance SQL Storage**:
  - Relational SQLite store at `autopilot/jobs.db` using `PRAGMA journal_mode=WAL` and `PRAGMA busy_timeout=30000`.
  - Enables concurrent multi-process reads and writes across workers and dashboard with sub-millisecond query latency (~0.002 ms).
- **Vacancy Ingestion**:
  - All discovered listings from Greenhouse, Workable, Lever, etc. are UPSERTed into `jobs` table keyed by `id = sha(url)`.
- **Hybrid QA Caching & LLM Answering**:
  - Form questions are checked against `qa_answers` first; existing answers are injected instantly with 0 tokens spent.
  - Unique/open-ended questions are routed to LLM (`llm7` / `codestral-latest`) with the candidate's CV text and role description.
  - Validated answers are automatically inserted into `qa_answers` for instant reuse across all future applications.
  - Unresolved questions are logged to `pending_questions` for review via `python tools/autopilot.py qa --pending`.
