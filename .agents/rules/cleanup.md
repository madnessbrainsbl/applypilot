---
description: Storage maintenance and automatic garbage collection rules
---

# Garbage Collection Rule

- Periodically monitor `autopilot/screenshots/`.
- Keep at most 10 recent screenshots through `prune_screenshots` / `python tools/autopilot.py clean`.
- Remove only temporary covers, CVs and upload copies owned by the current process.
- Never remove another session's artifacts or copy private runtime state into Git.
