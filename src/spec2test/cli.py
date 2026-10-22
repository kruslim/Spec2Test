"""`spec2test` command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import ValidationError

from spec2test import corpora, evals, judge
from spec2test.extract import Extraction, extract
from spec2test.generate import Generation, generate, render_generation
from spec2test.ingest import Document, UnsupportedFormatError, ingest
from spec2test.lint import Lint, lint
from spec2test.llm import PRESETS, AnthropicLLM, FakeLLM, LLMClient, OpenAICompatLLM
from spec2test.schema import Requirement, VerificationItemKind


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

    p = commands.add_parser("generate", help="write a verification item for each requirement")
    p.add_argument(
        "path",
        type=Path,
        help="an Extraction saved by `extract --json`, or a Lint by `lint --json`",
    )
    p.add_argument(
        "--doc",
        type=Path,
        help="the spec, or a Document saved by `ingest --json`, to give the model each "
        "requirement's clause and the clauses it refers to",
    )
    _add_llm_args(p)
    p.add_argument("--json", action="store_true", help="print the whole Generation as JSON")
    p.set_defaults(run=_generate)

    p = commands.add_parser("judge", help="grade generated items against the rubric")
    p.add_argument("path", type=Path, help="a Generation saved by `generate --json`")
    p.add_argument("--doc", type=Path, help="the spec, as for `generate --doc`")
    p.add_argument(
        "--kind",
        action="append",
        choices=[k.value for k in VerificationItemKind],
        help="grade only items of this kind (repeatable; default: all)",
    )
    _add_llm_args(p, default="anthropic")
    p.add_argument("--json", action="store_true", help="print the whole Judging as JSON")
    p.set_defaults(run=_judge)

    p = commands.add_parser("calibrate", help="report judge-vs-person agreement on a graded sample")
    p.add_argument(
        "path",
        type=Path,
        help="a sample graded by a person (JSONL), or with --new-sample a Generation to draw "
        "one from",
    )
    p.add_argument(
        "--new-sample",
        action="store_true",
        help="print an ungraded sample of the Generation at PATH as JSONL, to grade by hand",
    )
    p.add_argument("--size", type=int, help="with --new-sample: items to draw (default: all)")
    p.add_argument("--seed", type=int, default=0, help="with --new-sample: random seed")
    p.add_argument("--doc", type=Path, help="the spec, as for `generate --doc`")
    p.add_argument(
        "--rescore",
        type=Path,
        metavar="RESULTS",
        help="reuse the judge's grades in a calibration saved by `calibrate --json`; no "
        "model calls",
    )
    _add_llm_args(p, default="anthropic")
    p.add_argument("--json", action="store_true", help="print the whole Calibration as JSON")
    p.set_defaults(run=_calibrate)

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


def _add_llm_args(p: argparse.ArgumentParser, default: str = "kimi-code") -> None:
    p.add_argument(
        "--provider",
        choices=["anthropic", *PRESETS],
        default=default,
        help=f"model provider (default: {default})",
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


def _load_requirements(path: Path) -> list[Requirement]:
    """The records in a Lint (with their flags) or an Extraction saved as JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return Lint.model_validate(data).requirements
    except ValidationError:
        return Extraction.model_validate(data).requirements


def _context_doc(path: Path | None, requirements: list[Requirement]) -> Document | None:
    """The document `--doc` names, ingested under the requirements' document ID so their
    anchors find their clauses; a warning when none of them is from it."""
    if path is None:
        return None
    ids = list(dict.fromkeys(r.source_anchor.doc for r in requirements))
    doc = _load_document(path, ids[0] if ids else None, None)
    if ids and doc.doc_id not in ids:
        print(
            f"warning: {path} is {doc.doc_id!r}, but the requirements are from "
            f"{', '.join(map(repr, ids))}: no source clauses will be sent",
            file=sys.stderr,
        )
    return doc


def _generate(args: argparse.Namespace) -> int:
    try:
        requirements = _load_requirements(args.path)
        doc = _context_doc(args.doc, requirements)
    except (UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = generate(requirements, _llm(args), doc=doc)
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(render_generation(result))
        print(
            f"{len(result.items)} items from {len(result.requirements)} requirements, "
            f"{len(result.rejected)} rejected, {result.retried} retried, "
            f"{len(result.failed)} failed, {len(result.skipped)} skipped (no method)"
        )
    return 1 if result.failed else 0


def _judge(args: argparse.Namespace) -> int:
    try:
        generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
        doc = _context_doc(args.doc, generation.requirements)
    except (UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    kinds = [VerificationItemKind(k) for k in args.kind] if args.kind else None
    result = judge.judge(generation, _llm(args), doc=doc, kinds=kinds)
    print(result.model_dump_json(indent=2) if args.json else judge.judging_report(result))
    return 1 if result.failed else 0


def _calibrate(args: argparse.Namespace) -> int:
    try:
        if args.new_sample:
            generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
            for line in judge.new_sample(generation, args.size, args.seed):
                print(line.model_dump_json())
            return 0
        sample = judge.load_sample(args.path)
        doc = _context_doc(args.doc, [r for line in sample.lines for r in line.requirements])
        previous = None
        if args.rescore is not None:
            previous = judge.Calibration.model_validate_json(
                args.rescore.read_text(encoding="utf-8")
            )
    except (UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        # SampleError and pydantic's ValidationError are ValueErrors too.
        print(f"error: {exc}", file=sys.stderr)
        return 2

    llm = None if previous is not None else _llm(args)
    result = judge.calibrate(sample, llm, doc=doc, previous=previous)
    print(result.model_dump_json(indent=2) if args.json else judge.calibration_report(result))
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
