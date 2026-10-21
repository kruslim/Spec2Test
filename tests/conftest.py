from pathlib import Path

import pytest

from spec2test.llm import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM.from_dir(FIXTURES / "llm")
