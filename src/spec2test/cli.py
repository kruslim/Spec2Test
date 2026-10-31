"""`spec2test` command line."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from spec2test import corpora, evals
from spec2test.extract import Extraction, extract
from spec2test.generate import Generation, generate, render
from spec2test.ingest import Document, UnsupportedFormatError, ingest
from spec2test.judge import (
    Judging,
    calibrate,
    calibration_report,
    judge,
    load_sample,
    make_sample,
    quality_report,
    write_sample,
)
from spec2test.lint import Lint, lint
from spec2test.llm import PRESETS, AnthropicLLM, FakeLLM, LLMClient, OpenAICompatLLM, cost_line
from spec2test.schema import Requirement
from spec2test.trace import diff, diff_report, matrix_csv, matrix_report, trace


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

    p = commands.add_parser(
        "generate", help="write a checklist item, test case or evidence item per requirement"
    )
    p.add_argument(
        "path",
        type=Path,
        help="requirements: an Extraction (`extract --json`), a Lint (`lint --json`) or an "
        "eval run's results.json",
    )
    _add_doc_args(p, "the source, so the clauses requirements reference are sent too")
    _add_llm_args(p)
    p.add_argument("--json", action="store_true", help="print the whole Generation as JSON")
    p.set_defaults(run=_generate)

    p = commands.add_parser("judge", help="grade generated items against the rubric")
    p.add_argument("path", type=Path, help="a Generation saved by `generate --json`")
    _add_doc_args(p, "the source, so the judge sees the referenced clauses too")
    _add_llm_args(p, default="gemini")
    p.add_argument("--json", action="store_true", help="print the whole Judging as JSON")
    p.set_defaults(run=_judge)

    p = commands.add_parser("calibrate", help="measure judge-human agreement")
    actions = p.add_subparsers(required=True, metavar="ACTION")
    s = actions.add_parser("sample", help="draw items from a Generation for a person to label")
    s.add_argument("path", type=Path, help="a Generation saved by `generate --json`")
    s.add_argument("--out", type=Path, required=True, help="sample JSONL to write")
    s.add_argument("--size", type=int, default=30, help="items to draw (default: 30)")
    s.add_argument("--seed", type=int, default=0, help="random seed (default: 0)")
    _add_doc_args(s, "the source, to store the referenced clauses with each item")
    s.set_defaults(run=_calibrate_sample)
    s = actions.add_parser("score", help="judge the labeled sample and report agreement")
    s.add_argument("path", type=Path, help="a labeled sample JSONL")
    _add_llm_args(s, default="gemini")
    s.add_argument(
        "--judgements",
        type=Path,
        metavar="JSON",
        help="saved judgements (`judge --json` or `calibrate score --json`); no model calls",
    )
    s.add_argument("--json", action="store_true", help="print the whole Calibration as JSON")
    s.set_defaults(run=_calibrate_score)

    p = commands.add_parser("trace", help="trace requirements to items; list gaps and orphans")
    p.add_argument("path", type=Path, help="a Generation saved by `generate --json`")
    p.add_argument(
        "--requirements",
        type=Path,
        help="trace the items to these requirements instead of the generation's own (an "
        "Extraction, a Lint or an eval run's results.json), e.g. a newer extraction",
    )
    out = p.add_mutually_exclusive_group()
    out.add_argument("--json", action="store_true", help="print the whole Matrix as JSON")
    out.add_argument("--csv", action="store_true", help="print the matrix as CSV")
    p.set_defaults(run=_trace)

    p = commands.add_parser(
        "diff", help="compare two versions of a spec; list the items each change affects"
    )
    p.add_argument("old", type=Path, help="the old version's requirements (an Extraction, a "
                   "Lint or an eval run's results.json)")  # fmt: skip
    p.add_argument("new", type=Path, help="the new version's requirements, likewise")
    p.add_argument("--items", type=Path, help="the old version's Generation (`generate --json`)")
    p.add_argument("--all", action="store_true", help="list unchanged requirements too")
    p.add_argument("--json", action="store_true", help="print the whole SpecDiff as JSON")
    p.set_defaults(run=_diff)

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
        return FakeLLM.from_dir(args.fake, model=args.model)
    if args.provider == "anthropic":
        return AnthropicLLM(args.model) if args.model else AnthropicLLM()
    model = {"model": args.model} if args.model else {}
    return OpenAICompatLLM.preset(args.provider, **model)


def _add_doc_args(p: argparse.ArgumentParser, purpose: str) -> None:
    group = p.add_mutually_exclusive_group()
    group.add_argument(
        "--doc", type=Path, help=f"a spec, or a Document saved by `ingest --json`: {purpose}"
    )
    group.add_argument("--corpus", help=f"a corpus ID from the corpora manifest: {purpose}")
    p.add_argument("--dir", type=Path, default=corpora.DEFAULT_DIR, help="corpora folder")


def _doc_arg(args: argparse.Namespace) -> Document | None:
    """The document `--doc` or `--corpus` names, if either was given."""
    if args.doc is not None:
        return _load_document(args.doc, None, None)
    if args.corpus is not None:
        entry = next((d for d in corpora.load_manifest(args.dir) if d.id == args.corpus), None)
        if entry is None:
            raise ValueError(f"{args.corpus!r} is not in {args.dir / corpora.MANIFEST}")
        return corpora.parse(entry, args.dir)
    return None


def _load_requirements(path: Path) -> list[Requirement]:
    """The requirements in an Extraction, a Lint or an eval run's results.json; an eval run
    gives its in-scope records, with their testability flags."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if "gold_path" in data:
        return evals.EvalRun.model_validate(data).predicted
    if "doc_id" in data:
        return Extraction.model_validate(data).requirements
    return Lint.model_validate(data).requirements


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


def _generate(args: argparse.Namespace) -> int:
    try:
        requirements = _load_requirements(args.path)
        doc = _doc_arg(args)
    except (corpora.CorpusError, UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    result = generate(requirements, _llm(args), doc=doc)
    if args.json:
        print(result.model_dump_json(indent=2))
        return 1 if result.failed else 0
    for item in result.items:
        print(render(item), end="\n\n")
    for rej in result.rejected:
        print(f"rejected  {rej.requirement_id}  {rej.kind.value}")
        for e in rej.errors:
            print(f"    {e}")
    for f in result.failed:
        print(f"failed  {f.requirement_id}  {f.error}")
    for req_id in result.unclassified:
        print(f"unclassified  {req_id}  (no verification method)")
    for s in result.skipped:
        print(f"skipped  {s.requirement_id}  {s.reason}")
    kinds = Counter(i.kind.value for i in result.items)
    print(
        f"{len(result.items)} items ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}), "
        f"{len(result.rejected)} rejected, {result.retried} retried, {len(result.failed)} "
        f"failed, {len(result.unclassified)} unclassified, {len(result.skipped)} skipped"
    )
    print(cost_line(result.usage, result.pages))
    return 1 if result.failed else 0


def _judge(args: argparse.Namespace) -> int:
    try:
        generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
        doc = _doc_arg(args)
        result = judge(generation, _llm(args), doc=doc)
    except (corpora.CorpusError, UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        # SameModelError is a ValueError too.
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        for f in result.failed:
            print(f"failed  {f.item_id}  {f.error}")
        print(quality_report(result))
    return 1 if result.failed else 0


def _calibrate_sample(args: argparse.Namespace) -> int:
    try:
        generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
        doc = _doc_arg(args)
    except (corpora.CorpusError, UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    sample = make_sample(generation, args.size, doc=doc, seed=args.seed)
    write_sample(sample, args.out)
    kinds = Counter(s.item.kind.value for s in sample)
    print(
        f"{len(sample)} items ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))}) "
        f"written to {args.out}; fill in `human` on each line, then run "
        f"`spec2test calibrate score {args.out}`"
    )
    return 0


def _calibrate_score(args: argparse.Namespace) -> int:
    try:
        sample = load_sample(args.path)
        saved = None
        if args.judgements is not None:
            data = json.loads(args.judgements.read_text(encoding="utf-8"))
            saved = Judging.model_validate(data.get("judging", data))
        result = calibrate(sample, None if saved else _llm(args), saved=saved)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(calibration_report(result))
    return 0 if result.labeled else 1


def _trace(args: argparse.Namespace) -> int:
    try:
        generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
        reqs = None if args.requirements is None else _load_requirements(args.requirements)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    matrix = trace(generation, reqs)
    if args.json:
        print(matrix.model_dump_json(indent=2))
    elif args.csv:
        print(matrix_csv(matrix), end="")
    else:
        print(matrix_report(matrix))
    return 1 if matrix.gaps or matrix.orphans else 0


def _diff(args: argparse.Namespace) -> int:
    try:
        old, new = _load_requirements(args.old), _load_requirements(args.new)
        items = None
        if args.items is not None:
            items = Generation.model_validate_json(args.items.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = diff(old, new, items=items)
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(diff_report(result, unchanged=args.all))
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
