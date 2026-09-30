# ApplyPilot

[![CI](https://github.com/madnessbrainsbl/applypilot/actions/workflows/ci.yml/badge.svg)](https://github.com/madnessbrainsbl/applypilot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Find ATS jobs, match them to your CV and prepare applications — locally, under your control.**

A local job-search assistant in two parts, each with its own SQLite database:

- **Autopilot** (`tools/`, `autopilot/`): a Python + Playwright engine. It discovers vacancies from many sources,
  scores them against your search profile, opens the application form, fills it from your profile and CV, and
  hands the final click to you (or submits, if you switch that on). Questions it cannot answer by rules go to a
  chain of LLM providers; whatever is left lands in a queue for you.
- **Dashboard** (`src/`): a Next.js app forked from [JobSync](https://github.com/Gsync/jobsync) (MIT). It tracks
  applications and resumes, and at `/dashboard/autopilot` it controls the engine: start a run, answer pending
  questions, test and toggle LLM providers, watch the live log.

Everything runs on your machine. Your CV, answers and credentials never leave it except to the sites you apply to
and the LLM providers you enable.

Each user downloads the project and runs their own copy on their own machine. They add their own CVs,
profile, keys and accounts after installation; this data is excluded from Git and is not part of the source.
Dashboard accounts within one installation control that installation's shared autopilot.

> **Responsible use.** Automated submissions can break a site's terms of service and can send applications you did
> not intend. The assisted mode (`--review --submit`) never submits by itself: you check the filled form and press Submit.
> The engine attempts checkbox challenges and audio reCAPTCHA using a configured speech API; unresolved challenges require manual review. You are responsible for every
> application sent in your name.

## Requirements

- Python 3.10+ and Google Chrome (or Playwright's Chromium)
- Node.js 24+ (dashboard only)
- Developed and tested on Windows. Linux and macOS should work, but a few helpers (process cleanup, Chrome
  detection) have Windows-specific paths. Please report issues.

## Quick start (autopilot)

```sh
git clone https://github.com/madnessbrainsbl/applypilot.git
cd applypilot

pip install -r requirements.txt
python -m playwright install chromium          # skip if you use installed Chrome

cp autopilot/config.example.json autopilot/config.json     # the CLI also creates it on first run
```

1. Edit `autopilot/config.json`:
   - `profile`: your name, email, phone, links, `country`, `languages`, salary, notice period.
   - `apply.require_sponsorship`: set `true` or `false`. While it is `null`, the engine will not answer
     visa/work-authorization questions by itself.
   - `apply.answers`: your own answers (`open_to_relocation`, `open_to_onsite`, ...).
   - `personas`: one entry per profession you apply for (title terms, search keywords, CV path).
2. Add your CV as a persona (an LLM provider drafts the search terms from the PDF, using only what the CV says;
   without one, fill in `personas.<name>.search` by hand):

   ```sh
   python tools/autopilot.py resume add --file my-cv.pdf --name mycv --role "Backend Engineer"
   ```

3. Put credentials in `~/.config/search-work-world/secrets.env` (template: `autopilot/secrets.env.example`).
   Use the secrets file for credentials; legacy email/provider credentials in `config.json` are still accepted for compatibility.
4. Check the setup, then run:

   ```sh
   python tools/autopilot.py doctor                     # what is missing or misconfigured
   python tools/autopilot.py discover                   # collect vacancies into autopilot/jobs.db
   python tools/autopilot.py apply --dry-run --max 1    # fill one form, submit nothing
   python tools/autopilot.py apply --review --submit --max 5     # assisted mode: you press Submit
   python tools/autopilot.py status
   ```

`--submit` exists for unattended runs. Use it only after you have watched several `--review` runs behave correctly. Full command list and every config key: [autopilot/README.md](autopilot/README.md).

## Dashboard

```sh
npm ci
cp .env.example .env         # then set DATABASE_URL="file:./dev.db" and a random AUTH_SECRET
python -c "import sqlite3; sqlite3.connect('prisma/dev.db').close()"
npx prisma generate
npx prisma migrate deploy
npm run dev                  # http://localhost:3737
```

Create an account on first visit, then open `/dashboard/autopilot`. The page runs `python tools/autopilot.py`; set
`AUTOPILOT_PYTHON` if your interpreter is not called `python`. `docker compose up` starts the upstream JobSync image,
which does not include the autopilot page. Dashboard features and settings: [docs/jobsync-dashboard.md](docs/jobsync-dashboard.md).

## Tests

```sh
python tools/run_tests.py    # isolated example profile and temporary database
npm test                     # dashboard unit tests (vitest)
```

## Privacy and safety

- Do not commit `autopilot/config.json`, `autopilot/resumes/`, `autopilot/jobs.db`, `autopilot/browser-state.json`
  or your secrets file. `.gitignore` covers them. See [SECURITY.md](SECURITY.md).
- Offline candidate facts require profile values or explicit answers. LLM answers are checked for selected unsupported claims; review is still necessary. It declines or leaves demographic questions
  blank and routes anything unclear to `python tools/autopilot.py qa --pending` instead of guessing.
- Review the offline answer rules (`_OFFLINE_RULES` in `tools/autopilot.py`) once, so you know what is answered
  without asking you.

## Credits and license

The dashboard is built on [JobSync](https://github.com/Gsync/jobsync) by Gsync. MIT licensed, see [LICENSE](LICENSE).
Contributions are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md).

## From the author

ApplyPilot helps you search for jobs and prepare applications on the way to an interview. For help during the
interview itself, try [Talkaside](https://app.talkaside.workers.dev/): a Windows app with answer suggestions based on
your CV and the job description, live translation in 27 languages, and conversation transcription.

Talkaside is a paid application with a **5-day trial**. **ApplyPilot is free and open source.** Both tools let you
choose your own providers; any fees charged by those providers are separate.

---

**Keywords:** job search automation, job application bot, auto apply, ATS application automation, Greenhouse,
Ashby, Workable, AI cover letters, resume matching, application tracking, local job-search assistant,
self-hosted career tools, Python, Playwright.
