# Translation quality work

For translation reviews, flags, regeneration, and cache audits, follow the
**Translation review procedure** in `README.md` before changing Discord posts
or approving cache entries.

- A generated candidate is not a reviewed translation. Never mark it reviewed
  through direct database edits or approve it solely because it is fluent.
- Record source-to-target clause alignment, meaning changes, ambiguity and the
  reason for each edit. Context may resolve ambiguity, not invent content.
- Preserve correct wording. Keep ambiguous source text distinct from a model
  error. Do not silently repair a questionable source verb into a different verb.
- Keep private audit material and evidence under ignored `data/`, never in
  public documentation, fixtures, commits or external translation services.
- Apply only approved evidence for the exact current source and candidate.
  Verify live output and reactions separately from linguistic correctness.
- Invalidate only affected exact sources and derived units. Never flush the
  entire cache for a patch. Do not call replay hits a live hit rate.

# Maintenance scheduling

Use the native `Discord Translator Maintenance Gate` task and
`scripts/run_maintenance.py` for recurring checks. The old five-minute Codex chat
heartbeat is paused intentionally: idle checks must not start AI or replay chat
history. Do not re-enable it alongside the native gate. See the README Scheduled
maintenance section and `docs/maintenance-reviewer.md` for the bounded reviewer.
