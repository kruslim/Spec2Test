# Corpora

Source documents for the gold sets: FMVSS 111, UN R158, NASA-STD-5001 and the Formula
SAE rules. `manifest.toml` lists where each one comes from, which edition it is, and its
sha256. The documents themselves are never committed: everything in this folder except
the manifest and this README is git-ignored.

```bash
uv run spec2test corpora fetch    # download the public ones (FMVSS 111, NASA-STD-5001)
uv run spec2test corpora status   # what is here, and whether it matches the manifest
uv run spec2test corpora parse    # ingest each document and print its section counts
```

Two documents have to be downloaded by hand (see `notes` in the manifest):

- `unece-r158.pdf`: unece.org blocks scripted downloads, so open the URL in a browser.
  The pinned file is the original 2021 text; later supplements change it.
- `fsae-rules.pdf`: the 2026 rules, Version 1.0. FSAE rules stay out of the public repo
  (docs/PLAN.md).

Every edition is pinned by its sha256, so a swapped or updated file fails `status`. To
move to a new edition, replace the file, then put the hash `status` prints into the
manifest, and recheck the spot checks in `tests/test_corpora.py`.

The tests that parse all four documents are opt-in:

```bash
SPEC2TEST_CORPORA=1 uv run pytest -m corpora
```

Besides hand-picked clauses, they check each document against its own table of contents:
every entry must be a section starting on the page the contents give.
