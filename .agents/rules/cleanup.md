---
description: Storage maintenance and automatic garbage collection rules
---

# Garbage Collection Rule

- Periodically monitor `autopilot/screenshots/`.
- If file count exceeds 100 or when finishing runs, ensure `prune_screenshots` / `python tools/autopilot.py clean` runs.
- Keep active `needs_manual = 1` screenshots for dashboard inspection.
- Purge orphaned files, dead listing screenshots, and old submissions.
