# Autopilot

The Python engine behind `python tools/autopilot.py`. It finds vacancies, fills application forms and keeps a
SQLite record of everything (`autopilot/jobs.db`, created on first run).

## Setup

```sh
pip install -r requirements.txt
python -m playwright install chromium   # only if you do not use installed Google Chrome
cp autopilot/config.example.json autopilot/config.json   # also created automatically on first run
python tools/autopilot.py doctor
```

`browser.channel` is `"chrome"` (installed Chrome). Set it to `null` to use Playwright's bundled Chromium.
OCR for scanned CVs is optional and needs the [Tesseract](https://github.com/tesseract-ocr/tesseract) binary.

## Secrets

Credentials come from process environment variables or `~/.config/search-work-world/secrets.env`
(override the path with `AUTOPILOT_SECRETS_FILE`). Process environment wins. Legacy email/provider secrets in
`config.json` remain supported; keep new credentials in the secrets file. It requests mode `0600` when loaded
(Windows permissions depend on the filesystem ACL). Template: `autopilot/secrets.env.example`.

Keys: `OPENAI_API_KEY`, `AUTOPILOT_LLM_BASE_URL`, `AUTOPILOT_EMAIL_ADDRESS`, `AUTOPILOT_EMAIL_APP_PASSWORD`,
`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `JOOBLE_API_KEY`, `TAVILY_API_KEY`, plus whatever `key_env` your LLM providers
declare (`LLM7_API_KEY`, `GROQ_API_KEY`, ...). For Gmail use an app password, never your account password.

## Configure `config.json`

| Section | What to set |
|---|---|
| `profile` | Name, email, phone, `phone_country_code` (digits, no `+`), `location`, `country`, `languages`, links, `salary_expectation`, `notice_period`, `years_experience`, `school`, `degree`, `discipline`. Forms are filled from here. |
| `apply.require_sponsorship` | `true` if you need visa sponsorship, `false` if you do not. `null` means unknown, so visa and work-authorization questions are left for you. `true` also skips postings that do not offer sponsorship. |
| `apply.answers` | Your own answers by key: `visa`, `work_authorization`, `current_employer`, `why`, `open_to_relocation`, `open_to_onsite`. Empty means "ask me". |
| `apply.dropdown_answers` | Label fragment to option text, for dropdowns the rules do not cover. Ships with privacy/consent acknowledgements only; add your own facts. |
| `apply.max_per_run`, `max_per_company`, `workers` | Throughput limits. Start small. |
| `personas` | One entry per profession: `cv`, `search` terms (`keywords`, `required_title_terms`, `good_terms`, `bad_terms`, `excluded_title_terms`), and `profile.bio` (used in the fallback cover letter). A persona can override `profile` fields such as name and email. Manage them with the `resume` command. |
| `search` | Global filters: `bad_terms`, `no_relocation_terms`, `apply_block_terms`, `blocked_companies`. |
| `vendor_boards`, `workable_accounts` | Company job boards to poll (Greenhouse, Lever, Ashby, SmartRecruiters, Workable, ...). Add the companies you care about. |
| `jobspy` | Sites, locations and freshness for the aggregator search. |
| `llm.providers` | Chain of providers tried in order. All ship disabled: add a key, then `python tools/autopilot.py llm --enable <name>`. |

Keep your CV PDFs in `autopilot/resumes/` (gitignored). The CV must carry the same name as `profile.full_name`.

## Commands

```sh
python tools/autopilot.py discover                  # collect vacancies (--source tavily|workable|jobspy|...)
python tools/autopilot.py apply --dry-run --max 1   # fill a form, submit nothing
python tools/autopilot.py apply --review --submit --max 5    # assisted: you solve CAPTCHAs, answer extras, press Submit
python tools/autopilot.py apply --submit            # unattended submission (only after trusting --review)
python tools/autopilot.py run                       # discover + apply
python tools/autopilot.py status                    # counts and outcomes
python tools/autopilot.py doctor                    # checks config, CVs, LLM chain, mailbox
```

More: `apply-url <url>`, `rescore`, `recheck`, `keywords`, `ats <job>` (CV vs posting gap),
`qa --pending` / `qa --audit` (questions waiting for you, suspicious cached answers), `manual` (filled forms waiting
for a click), `inbox --apply` (read outcome emails), `outcome`, `followups`, `outreach`, `ghost` / `block`
(spot and skip reposting companies), `resume add|list|remove`, `llm --probe|--models|--enable|--disable`,
`clean` (prune old screenshots). Run `python tools/autopilot.py <command> -h` for flags.

`--review` opens a visible browser, fills the fields, attaches the CV and cover letter, then waits (up to
`apply.manual_wait_seconds`) for you. When the confirmation page appears the application is recorded as submitted.

## Answering company questions

Standard fields are filled by rules. Anything else goes through the chain in `llm.providers` (first provider that
answers wins); the answer is grounded in your CV text and cached in `qa_answers` for reuse. If no provider answers,
offline rules run, and what they do not cover appears in `qa --pending` and on the dashboard. API providers need a key
in `secrets.env`; CLI providers (`codex`, `claude`, `grok`) use the CLI you already have installed.

## Persistent browser login

```sh
python tools/autopilot.py login https://www.linkedin.com/login
```

The session is stored in `autopilot/browser-state.json`. Keep that file private.

## Discovery sources

The dashboard now includes a Sources tab with adapter health, source switches and queue-filter diagnostics.
Career-page JSON-LD crawling, remote/sponsorship boards, optional FindAll inputs and optional JobWatch exports
are covered in the [discovery and review guide](../docs/discovery-and-review.md). JobWatch and local FindAll imports
start disabled; configure your own input directories. No cloud delivery is bundled.

`vendor` boards, `workable`, `tavily` (set `TAVILY_API_KEY`; restricted to direct ATS posting URLs), `jobspy`
(Indeed, Google, LinkedIn search only: a LinkedIn posting is kept when the same title exists on the company's
own Greenhouse/Ashby/Workable board; Easy Apply is never used), `adzuna`, `jooble`, and several remote-job feeds.

## Files it creates (all gitignored)

`jobs.db` (+ `-wal`, `-shm`), `screenshots/`, `covers/`, `outreach/`, `chrome-profile*/`, `browser-state.json`,
`control.json`, `progress.json`, `run.log`.

The dashboard Apply button performs a dry run unless `apply.submit_without_review` is explicitly `true`. Candidate-specific offline answers default to empty; configure `apply.answers` or answer pending questions. `require_sponsorship` is a general preference, not a country-specific work permit record.
