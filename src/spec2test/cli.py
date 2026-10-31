"""`spec2test` command line."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
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
    compare,
    comparison_report,
    judge,
    judge_sample,
    load_sample,
    make_sample,
    quality_report,
    write_sample,
)
from spec2test.lint import Lint, lint
from spec2test.llm import (
    PRESETS,
    AnthropicLLM,
    FakeLLM,
    LLMClient,
    OpenAICompatLLM,
    SessionLLM,
    cost_line,
    write_requests,
)
from spec2test.llm.session import DEFAULT_MODEL as SESSION_MODEL
from spec2test.review import APP, DEFAULT_DB, ReviewStore, stats_report
from spec2test.schema import Requirement
from spec2test.trace import diff, diff_report, matrix_csv, matrix_report, trace


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    # Windows consoles default to a legacy code page that cannot print '§' or '±'. Errors
    # quote the source too, so stderr gets the same, and never fails on a character.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
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
    p.add_argument(
        "path",
        type=Path,
        help="a Generation saved by `generate --json`, or a calibration sample (.jsonl)",
    )
    _add_doc_args(p, "the source, so the judge sees the referenced clauses too")
    _add_llm_args(p, default="session", session=True)
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
    _add_llm_args(s, default="session", session=True)
    s.add_argument(
        "--judgements",
        type=Path,
        metavar="JSON",
        help="saved judgements (`judge --json` or `calibrate score --json`); no model calls",
    )
    s.add_argument("--json", action="store_true", help="print the whole Calibration as JSON")
    s.set_defaults(run=_calibrate_score)
    s = actions.add_parser("compare", help="agreement between two judges on the same items")
    s.add_argument(
        "first",
        type=Path,
        help="saved judgements (`judge --json`, `calibrate score --json`): the main judge",
    )
    s.add_argument("second", type=Path, help="saved judgements by the second judge, likewise")
    s.add_argument("--json", action="store_true", help="print the whole comparison as JSON")
    s.set_defaults(run=_calibrate_compare)

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

    p = commands.add_parser("review", help="accept, edit or reject items; the acceptance rate")
    actions = p.add_subparsers(required=True, metavar="ACTION")
    s = actions.add_parser("open", help="open the review app (needs the `ui` extra)")
    s.add_argument(
        "path", type=Path, nargs="?", help="a Generation (`generate --json`) to add and open"
    )
    _add_doc_args(s, "the source, to show each quote in its section")
    s.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"review log (default: {DEFAULT_DB})"
    )
    s.add_argument("--fake", type=Path, metavar="DIR", help="let runs replay FakeLLM fixtures")
    s.add_argument("--port", type=int, help="port to serve on (default: Streamlit's)")
    s.set_defaults(run=_review_open)
    s = actions.add_parser("stats", help="acceptance rate of the reviewed items")
    s.add_argument(
        "--db", type=Path, default=DEFAULT_DB, help=f"review log (default: {DEFAULT_DB})"
    )
    s.add_argument("--generation", metavar="ID", help="one generation (an id or its prefix)")
    s.add_argument("--json", action="store_true", help="print the whole ReviewStats as JSON")
    s.set_defaults(run=_review_stats)

    p = commands.add_parser("corpora", help="manage the gold-set source documents")
    p.add_argument("action", choices=["status", "fetch", "parse"])
    p.add_argument("ids", nargs="*", metavar="ID", help="documents to act on (default: all)")
    p.add_argument("--dir", type=Path, default=corpora.DEFAULT_DIR, help="corpora folder")
    p.set_defaults(run=_corpora)
    return parser


def _add_llm_args(
    p: argparse.ArgumentParser, default: str = "kimi-code", *, session: bool = False
) -> None:
    """`session`: also offer `--provider session`, answers written in a Claude Code session."""
    p.add_argument(
        "--provider",
        choices=["anthropic", *PRESETS, *(["session"] if session else [])],
        default=default,
        help=f"model provider (default: {default})",
    )
    p.add_argument("--model", help="model ID (default: the provider's default)")
    p.add_argument("--fake", type=Path, metavar="DIR", help="replay FakeLLM fixtures from DIR")
    if session:
        p.add_argument(
            "--answers",
            type=Path,
            metavar="JSONL",
            help="session answers (`--provider session`); a missing file means none yet",
        )
        p.add_argument(
            "--requests",
            type=Path,
            metavar="JSONL",
            help="where to write the requests still unanswered (default: next to --answers)",
        )


def _llm(args: argparse.Namespace) -> LLMClient:
    if args.fake is not None:
        return FakeLLM.from_dir(args.fake, model=args.model)
    if args.provider == "session":
        if args.answers is None:
            raise ValueError("--provider session needs --answers JSONL (it may not exist yet)")
        return SessionLLM.from_file(args.answers, model=args.model or SESSION_MODEL)
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
        llm = _llm(args)
        if args.path.suffix.lower() == ".jsonl":
            # A calibration sample carries each item's source context with it.
            result = judge_sample(load_sample(args.path), llm)
        else:
            generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
            result = judge(generation, llm, doc=_doc_arg(args))
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
    _write_session_requests(args, llm)
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
        llm = None if saved else _llm(args)
        result = calibrate(sample, llm, saved=saved)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(result.model_dump_json(indent=2))
    else:
        print(calibration_report(result))
    if llm is not None:
        _write_session_requests(args, llm)
    return 0 if result.labeled else 1


def _calibrate_compare(args: argparse.Namespace) -> int:
    try:
        first, second = (_load_judging(path) for path in (args.first, args.second))
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    result = compare(first, second)
    print(result.model_dump_json(indent=2) if args.json else comparison_report(result))
    return 0 if result.compared else 1


def _load_judging(path: Path) -> Judging:
    """Judgements saved by `judge --json`, or the judging inside `calibrate score --json`."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return Judging.model_validate(data.get("judging", data))


def _write_session_requests(args: argparse.Namespace, llm: LLMClient) -> None:
    """With `--provider session`, write the requests still unanswered for the session."""
    if not isinstance(llm, SessionLLM):
        return
    path = args.requests or args.answers.with_name(f"{args.answers.stem}.requests.jsonl")
    if not llm.pending:
        print(f"every request has a session answer in {args.answers}", file=sys.stderr)
        return
    write_requests(llm.pending, path)
    print(
        f"{len(llm.pending)} request(s) without a session answer written to {path}; answer "
        f"them in a Claude Code session (.claude/skills/session-judge), append the answers to "
        f"{args.answers}, then run the same command again",
        file=sys.stderr,
    )


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


def _review_open(args: argparse.Namespace) -> int:
    if importlib.util.find_spec("streamlit") is None:
        print("error: the review app needs Streamlit: uv sync --extra ui", file=sys.stderr)
        return 2
    env = {**os.environ, "SPEC2TEST_REVIEW_DB": str(args.db.resolve())}
    try:
        doc = _doc_arg(args)
        if args.path is not None:
            generation = Generation.model_validate_json(args.path.read_text(encoding="utf-8"))
            env["SPEC2TEST_REVIEW_OPEN"] = ReviewStore(args.db).add(generation, doc)
        elif doc is not None:
            print("error: --doc and --corpus go with a Generation to open", file=sys.stderr)
            return 2
    except (corpora.CorpusError, UnsupportedFormatError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.fake is not None:
        env["SPEC2TEST_REVIEW_FAKE"] = str(args.fake.resolve())
    # Streamlit sends usage statistics by default; nothing here should leave the machine.
    command = [sys.executable, "-m", "streamlit", "run", str(APP)]
    command += ["--browser.gatherUsageStats", "false"]
    # Serve this machine only: anyone who can reach the app can start paid model runs.
    command += ["--server.address", "localhost"]
    if args.port is not None:
        command += ["--server.port", str(args.port)]
    return subprocess.call(command, env=env)


def _review_stats(args: argparse.Namespace) -> int:
    if not args.db.is_file():
        print(f"error: no review log at {args.db}", file=sys.stderr)
        return 2
    store = ReviewStore(args.db)
    gid = None
    if args.generation is not None:
        ids = [g.id for g in store.generations() if g.id.startswith(args.generation)]
        if len(ids) != 1:
            found = "no generation" if not ids else f"{len(ids)} generations"
            print(f"error: {found} with id {args.generation!r} in {args.db}", file=sys.stderr)
            return 2
        gid = ids[0]
    stats = store.stats(gid)
    print(stats.model_dump_json(indent=2) if args.json else stats_report(stats))
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
