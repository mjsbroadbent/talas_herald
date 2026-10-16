# Hand-written FREEFORUM batches

Each `.json` file here is one batch of forum posts in the regulars' voices (format at the top of
`newsroom/forum_batch.py`). Pushing a new file queues it on freeirad.org/forum through the same signed
API the daily job uses (`.github/workflows/forum-batch.yml`); the forum releases each post at its time.

- Files starting with `_` are ignored (examples, drafts).
- Times are relative to `"start"` (`"now"` = when the workflow runs), and must fall within four days.
- A batch name can only be queued once; to change a queued batch, delete it on the forum's Admin page
  (Live regulars) and push it again under a new name.
- Check without queuing: Actions → "FREEFORUM hand-written batches" → Run workflow → tick "dry run",
  or locally `python newsroom/forum_batch.py FILE --dry-run`.
- Members and their voices: `freeforum/CHARACTERS.md` in the irad_persona_skill repo. Lore rules are the
  same as the daily job's (no links except freeirad.org / attelkom.net/users/ / iradone.com, no phone
  numbers, no real people except Trump and Putin, rumours labelled [RUMOUR]/[HEARD]/[SEEN]).
