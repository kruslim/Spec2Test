from pathlib import Path

import pytest

from spec2test.extract import extract
from spec2test.generate import Generation, generate
from spec2test.ingest import Block, Document, build_document
from spec2test.lint import lint
from spec2test.llm import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Live tests spend API money, so they need `-m live` on the command line as well as
    SPEC2TEST_LIVE=1: a variable left set in the shell never runs them on its own."""
    if "live" in (config.option.markexpr or ""):
        return
    skip = pytest.mark.skip(reason="live test: run with SPEC2TEST_LIVE=1 uv run pytest -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


# Invented for the tests. The canned extraction answers are in
# tests/fixtures/llm/extract_widget_lamp.json, its gold set in tests/fixtures/gold/.
WIDGET_LAMP = [
    Block("heading", "Widget Lamp Specification", 1),
    Block("text", "This specification is invented for the Spec2Test extraction tests.", 1),
    Block("text", "1. SCOPE", 1),
    Block("text", "1.1 Purpose. This document specifies requirements for a widget lamp.", 1),
    Block("text", "2. REQUIREMENTS", 2),
    Block(
        "text", "2.1 Brightness. The lamp shall emit at least 400 lm within 2.0 s of switch-on.", 2
    ),
    Block("text", "2.2 Housing. The housing shall be red and shall carry a voltage label.", 2),
    Block("text", "2.3 Switch. The switch shall be operable with one hand.", 2),
    Block("text", "2.4 Battery. The battery should last 8 h at full brightness.", 3),
    Block("text", "2.5 Mounting. The lamp shall mount on either:", 3),
    Block("list", "a. a 35 mm rail; or", 3),
    Block("list", "b. a wall bracket.", 3),
    Block("text", "2.6 Packaging. The lamp shall be packed in a carton.", 3),  # no fixture
]


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM.from_dir(FIXTURES / "llm")


@pytest.fixture
def widget_lamp() -> Document:
    return build_document("widget-lamp", WIDGET_LAMP)


@pytest.fixture
def generation(widget_lamp, fake_llm) -> Generation:
    """The widget lamp items: extracted, linted and generated from the FakeLLM fixtures."""
    requirements = lint(extract(widget_lamp, fake_llm).requirements, fake_llm).requirements
    return generate(requirements, fake_llm, doc=widget_lamp)
