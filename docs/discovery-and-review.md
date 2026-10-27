# Discovery and answer review

All commands run in your own installation. Search results, provider health, CVs and imported exports are private runtime data, excluded from Git.

## Inspect and select sources

```sh
python tools/autopilot.py sources --json
python tools/autopilot.py discover --source jsonld_careers --persona mycv
python tools/autopilot.py discover --source remote_visa_boards --persona mycv
python tools/autopilot.py queue --json --persona mycv
```

The dashboard's **Sources** tab shows source runs, stored results, submissions and interviews separately. Expand an adapter to see partial failures, disabled state or cooldowns. Queue preview explains filtering; it does not submit applications. Search sources may contact public employer pages and configured search APIs.

- `jsonld_careers` follows public company career pages, supported ATS boards and JobPosting JSON-LD. Seed lists are under `autopilot/seeds/`; request budgets, per-host gaps, age limits and robots.txt checks constrain crawling.
- `remote_visa_boards` has independent adapters, including FreeHire. Results still pass local profession, location, freshness and eligibility checks. Blocked responses pause an adapter rather than proving that no jobs exist.
- `findall_hosts` uses public host/archive evidence to locate employer pages. `findall_import` reads your separately configured FindAll project; it starts disabled and has no bundled workstation path.
- Security OSINT discovery can use its bundled Wikidata-derived company seed list. This public version does not refresh that list through the Wikidata API.
- `browser_study` acts later, when ordinary Apply selectors fail. It can choose an observed safe navigation link; it cannot supply scripts or authorize submission.

Use `python tools/autopilot.py <command> -h` for supported flags. To run discovery alongside a dry application batch:

```sh
python tools/autopilot.py run --multitask --dry-run --persona mycv --max 1
```

## Optional JobWatch import

Set `discovery.jobwatch.enabled` to `true` and configure `discovery.jobwatch.directory` or `JOBWATCH_EXPORT_DIR` to a local folder containing your exports. The default private inbox is `autopilot/jobwatch/`.

Supported input paths:

- `security/latest.json` and `creative/latest.json`
- `discovery/source_registry.json` and `health/status.json`
- `database/snapshot.json` for a canonical snapshot

Exports use `schema_version: "1.0"` and a timezone-aware `generated_at`. Jobs need stable IDs, a specific employer vacancy URL, a full description and a posting date before they can qualify for applications. Incomplete rows remain review records. Grants, social leads and source or worker diagnostics never become applications automatically.

```sh
python tools/autopilot.py jobwatch --kind job --limit 20
python tools/autopilot.py jobwatch --kind runtime
```

The importer does not configure Supabase, Drive synchronization, cloud workers or Tor pipelines. Runtime supervision is read-only advice. Worker IDs in the public module are generic integration labels: configure `WORKERS` for your own registry when using those diagnostics. No cloud credentials, automation IDs or real exports are bundled.

## CV evidence and manual review

Complex answers are checked against the selected persona's CV before filling or caching. The Review tab separates the original draft, checked answer, reason and literal CV evidence. A reviewer can distinguish past factual claims from future motivation. Missing evidence routes the answer to manual review; a saved legacy draft does not bypass the CV check.

```sh
python tools/autopilot.py qa --pending
python tools/autopilot.py qa --decisions --limit 30
```

Personal dates, contact details, citizenship and eligibility come from your own profile or explicit answers. No nationality-based visa exemption is assumed. Sponsor registers and location heuristics are discovery signals, not a guarantee of immigration eligibility. Optional persona-specific sponsorship waivers must be explicitly configured in `apply.sponsorship_waived`.

The dashboard keeps dry run as its default. Starting unattended submission requires `apply.submit_without_review: true` and a confirmation in the dashboard. The CLI also has explicit `--submit` and assisted `--review --submit` modes. Submission acknowledgement, per-company caps, atomic job claims and duplicate protection remain required.
