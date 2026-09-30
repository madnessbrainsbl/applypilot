# Security

## What stays on your machine

This project handles your CV, contact details and credentials. These files must never be committed or shared:

| File or folder | Holds |
|---|---|
| `autopilot/config.json` | Your profile and answers |
| `~/.config/search-work-world/secrets.env` | API keys, mailbox app password |
| `autopilot/resumes/`, `autopilot/covers/`, `autopilot/outreach/` | CVs and generated documents |
| `autopilot/jobs.db*` | Applications, cached answers, outcomes |
| `autopilot/browser-state.json`, `autopilot/chrome-profile*/` | Logged-in browser sessions |
| `.env`, `prisma/dev.db` | Dashboard secrets and data |

`.gitignore` excludes all of them. Before your first push, run `git status` and `git ls-files` and check that none is listed.

The supported distribution is a separate local installation for each user, with their own CVs, credentials and state.
Private runtime files are created after installation and excluded from Git; removing the author's personal files
from the source does not remove the ability to upload a user's own CV or configure their accounts.

Within one installation, the autopilot uses one shared local profile, mailbox and database. Every authenticated dashboard account can control
that same autopilot and view its answers and logs. Run it on localhost for a single operator; separate user accounts
do not isolate autopilot data. The Docker image contains the dashboard only, without the Python engine.

If a secret was committed, rotating it is the fix; deleting the commit is not enough because clones and caches keep
it. Gmail app passwords can be revoked at <https://myaccount.google.com/apppasswords>.

## Reporting a vulnerability

Please open a private security advisory on GitHub (Security tab, "Report a vulnerability") instead of a public issue.
Include steps to reproduce and the affected version.
