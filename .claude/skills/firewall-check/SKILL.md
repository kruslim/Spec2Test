---
name: firewall-check
description: Check pending changes against the Spec2Test data firewall (public documents only, no employer data, fixtures traceable to a public source or invented). Use before committing, before opening a PR, or when new fixtures, gold sets or documents are added.
---

# Data firewall check

Review what is about to be committed: `git status --short`, `git diff --cached`, and
`git diff` (staged and unstaged). Include new untracked files.

Check every added or changed file:

1. **Source documents**: nothing from `corpora/` other than `manifest.toml` and
   `README.md`; no PDF, DOCX or XLSX anywhere in the diff; no gold set for a document
   that stays out of git (`gold/unece-r158.*`, `gold/fsae-rules.*`). Run
   `git check-ignore -v` on anything suspicious.
2. **Employer data**: no employer specs, test cases, file formats, YAML layouts, code,
   internal names, part numbers or program codes, in code, fixtures, prompts, docs or
   commit messages. Naming the employer in `docs/PLAN.md` planning text is fine; its
   content is not.
3. **Traceability**: each new fixture or gold line either quotes a public document listed
   in `corpora/manifest.toml` (FMVSS 111, NASA-STD-5001, ...) or is visibly invented
   (like the `widget_lamp` spec in `tests/conftest.py`). A quote must not come from a
   document that is not in the manifest.
4. **Secrets**: no API keys, tokens or `.env` contents.

Report one line per file: OK, or the problem and the fix. If anything fails, stop and
tell the user before any commit.
