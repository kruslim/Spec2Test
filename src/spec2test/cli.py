"""`spec2test` command line."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from spec2test import corpora
from spec2test.ingest import Document, UnsupportedFormatError, ingest


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

    p = commands.add_parser("corpora", help="manage the gold-set source documents")
    p.add_argument("action", choices=["status", "fetch", "parse"])
    p.add_argument("ids", nargs="*", metavar="ID", help="documents to act on (default: all)")
    p.add_argument("--dir", type=Path, default=corpora.DEFAULT_DIR, help="corpora folder")
    p.set_defaults(run=_corpora)
    return parser


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
