"""`spec2test` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from spec2test import corpora, evals
from spec2test.extract import Extraction, extract
from spec2test.ingest import Document, UnsupportedFormatError, ingest
from spec2test.lint import lint
from spec2test.llm import PRESETS, AnthropicLLM, FakeLLM, LLMClient, OpenAICompatLLM


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Windows consoles default to a legacy code page that cannot print '§' or '±'.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return args.run(args)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spec2test", description=__doc__)
    commands = parser.add_subparsers(required=True, metavar="COMMAND")

    p = commands.add_parser("ingest", help="split a document into anchored clause sections")
    p.add_argument("path", type=Path)
    p.add_argument("--doc-id", help="document ID (default: from the file name)")
    p.add_argument("--end-marker", help="drop everything from the block starting with this")
    p.add_argument("--json", action="store_true", help="print the whole Document as JSON")
    p.set_defaults(run=_ingest)

    p = commands.add_parser("extract", help="extract cited requirements from a document")
    p.add_argument("path", type=Path, help="a spec, or a Document saved by `ingest --json`")
    p.add_argument("--doc-id", help="document ID (default: from the file name)")
    p.add_argument("--end-marker", help="drop everything from the block starting with this")
    _add_llm_args(p)
    p.add_argument("--json", action="store_true", help="print the whole Extraction as JSON")
    p.set_defaults(run=_extract)

    p = commands.add_parser("lint", help="flag requirements that are hard to verify")
    p.add_argument("path", type=Path, help="an Extraction saved by `extract --json`")
    _add_llm_args(p)
    p.add_argument(
        "--rules-only", action="store_true", help="run the deterministic rules only; no model"
    )
    p.add_argument("--json", action="store_true", help="print the whole Lint as JSON")
    p.set_defaults(run=_lint)

    p = commands.add_parser("eval", help="score extraction against a labeled gold set")
    p.add_argument("corpus", help="corpus ID; its gold set is gold/CORPUS.jsonl")
    p.add_argument("--gold", type=Path, help="gold set JSONL (default: gold/CORPUS.jsonl)")
    p.add_argument(
        "--doc",
        type=Path,
        help="a spec, or a Document saved by `ingest --json` (default: CORPUS from the "
        "corpora manifest)",
    )
    p.add_argument("--dir", type=Path, default=corpora.DEFAULT_DIR, help="corpora folder")
    _add_llm_args(p)
    p.add_argument(
        "--rescore",
        type=Path,
        metavar="RESULTS",
        help="score the extraction saved in a previous run's results.json; no model calls",
    )
    p.add_argument(
        "--lint",
        choices=["full", "rules", "off"],
        default="full",
        help="lint with the rules and the LLM pass (default), the rules only, or not at all; "
        "with --rescore, 'full' keeps the saved run's LLM flags",
    )
    p.add_argument("--out", type=Path, help="report folder (default: eval-reports/CORPUS/TIME)")
    p.set_defaults(run=_eval)

    p = commands.add_parser("corpora", help="manage the gold-set source documents")
    p.add_argument("action", choices=["status", "fetch", "parse"])
    p.add_argument("ids", nargs="*", metavar="ID", help="documents to act on (default: all)")
    p.add_argument("--dir", type=Path, default=corpora.DEFAULT_DIR, help="corpora folder")
    p.set_defaults(run=_corpora)
    return parser


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--provider",
        choices=["anthropic", *PRESETS],
        default="kimi-code",
        help="model provider (default: kimi-code)",
    )
    p.add_argument("--model", help="model ID (default: the provider's default)")
    p.add_argument("--fake", type=Path, metavar="DIR", help="replay FakeLLM fixtures from DIR")


def _llm(args: argparse.Namespace) -> LLMClient:
    if args.fake is not None:
        return FakeLLM.from_dir(args.fake)
    if args.provider == "anthropic":
        return AnthropicLLM(args.model) if args.model else AnthropicLLM()
    model = {"model": args.model} if args.model else {}
    return OpenAICompatLLM.preset(args.provider, **model)


def _load_document(path: Path, doc_id: str | None, end_marker: str | None) -> Document:
    """A spec to ingest, or a Document saved by `ingest --json`."""
    if path.suffix.lower() == ".json":
        return Document.model_validate_json(path.read_text(encoding="utf-8"))
    return ingest(path, doc_id, end_marker=end_marker)


def _ingest(args: argparse.Namespace) -> int:
    try:
        doc = ingest(args.path, args.doc_id, end_marker=args.end_marker)
    except (UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(doc.model_dump_json(indent=2))
        return 0
    for s in doc.sections:
        indent = "  " * max(s.level - 1, 0)
        print(f"p{s.page:<4} {indent}{s.anchor or '-'}  {s.title or ''}".rstrip())
    print(_summary(doc))
    return 0


def _extract(args: argparse.Namespace) -> int:
    try:
        doc = _load_document(args.path, args.doc_id, args.end_marker)
    except (UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = extract(doc, _llm(args))
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        for r in result.requirements:
            method = r.verification_method.value if r.verification_method else "-"
            print(f"{r.id}  p{r.source_anchor.page}  {method:13} {r.text}")
        for rej in result.rejected:
            print(f"rejected  {doc.sections[rej.section].anchor or '-'}  {rej.draft.text}")
            for e in rej.errors:
                print(f"    {e}")
        for f in result.failed_sections:
            print(f"failed  {doc.sections[f.section].anchor or '-'}  {f.error}")
        print(
            f"{len(result.requirements)} requirements, {len(result.rejected)} rejected, "
            f"{result.retried} retried, {len(result.failed_sections)} sections failed, "
            f"{len(result.skipped_sections)} skipped"
        )
    return 1 if result.failed_sections else 0


def _lint(args: argparse.Namespace) -> int:
    try:
        extraction = Extraction.model_validate_json(args.path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = lint(extraction.requirements, None if args.rules_only else _llm(args))
    if args.json:
        print(result.model_dump_json(indent=2))
        return 1 if result.failed else 0
    flagged = [r for r in result.requirements if r.testability_flags]
    for r in flagged:
        print(f"{r.id}  {r.text}")
        for f in r.testability_flags:
            print(f"    {f.kind.value}: {f.reason}")
            if f.suggested_rewrite:
                print(f"        rewrite: {f.suggested_rewrite}")
    for rej in result.rejected:
        print(f"rejected  {rej.draft.requirement_id}  {rej.draft.kind.value}: {rej.error}")
    for f in result.failed:
        print(f"failed  {f.section or '-'}  {f.error}")
    flags = sum(len(r.testability_flags) for r in result.requirements)
    print(
        f"{len(flagged)} of {len(result.requirements)} requirements flagged, {flags} flags, "
        f"{len(result.rejected)} LLM flags rejected, {len(result.failed)} batches failed"
    )
    return 1 if result.failed else 0


def _eval(args: argparse.Namespace) -> int:
    try:
        if args.doc is not None:
            doc = _load_document(args.doc, args.corpus, None)
        else:
            entry = next((d for d in corpora.load_manifest(args.dir) if d.id == args.corpus), None)
            if entry is None:
                raise ValueError(
                    f"{args.corpus!r} is not in {args.dir / corpora.MANIFEST}; pass --doc"
                )
            doc = corpora.parse(entry, args.dir)
        gold = evals.load_gold(args.gold or evals.gold_path(args.corpus), doc)
        previous = None
        if args.rescore is not None:
            previous = evals.EvalRun.model_validate_json(args.rescore.read_text(encoding="utf-8"))
    except (corpora.CorpusError, UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        # GoldError and pydantic's ValidationError are ValueErrors too.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if previous is not None:
        run = evals.rescore(doc, gold, previous, corpus=args.corpus, lint=args.lint)
    else:
        run = evals.evaluate(doc, gold, _llm(args), corpus=args.corpus, lint=args.lint)
    out = args.out or Path("eval-reports", args.corpus, datetime.now().strftime("%Y%m%d-%H%M%S"))
    html, results = evals.write_report(run, out)
    print(evals.text_report(run))
    print(f"\nreport:  {html}\nresults: {results}")
    return 0


def _corpora(args: argparse.Namespace) -> int:
    docs = corpora.load_manifest(args.dir)
    if args.ids:
        unknown = set(args.ids) - {d.id for d in docs}
        if unknown:
            print(f"unknown corpus document(s): {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        docs = [d for d in docs if d.id in args.ids]

    failed = False
    for doc in docs:
        try:
            if args.action == "status":
                state = corpora.status(doc, args.dir)
                line = f"{doc.id:16} {state.value}"
                if state is corpora.Status.UNPINNED:
                    line += f"  sha256 = {corpora.sha256_of(args.dir / doc.file)}"
                if state is corpora.Status.MISSING and doc.fetch == "manual":
                    line += f"  ({doc.notes or doc.url})"
                failed |= state in (corpora.Status.MISSING, corpora.Status.MISMATCH)
            elif args.action == "fetch":
                line = f"{doc.id:16} {corpora.fetch(doc, args.dir)}"
            else:
                line = f"{doc.id:16} {_summary(corpora.parse(doc, args.dir))}"
        except corpora.CorpusError as exc:
            line, failed = f"{doc.id:16} error: {exc}", True
        print(line)
    return 1 if failed else 0


def _summary(doc: Document) -> str:
    numbered = sum(1 for s in doc.sections if s.section_number is not None)
    return (
        f"{len(doc.pages)} pages, {len(doc.sections)} sections "
        f"({numbered} numbered), {len(doc.text):,} characters"
    )


if __name__ == "__main__":
    sys.exit(main())
