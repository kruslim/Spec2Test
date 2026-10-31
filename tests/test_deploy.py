"""The hosted app's config, cost cap, workspaces, metrics and delete action, and the app
itself, driven offline with Streamlit's AppTest. Sign-in is faked by patching
`spec2test.deploy.auth`; runs replay the FakeLLM fixtures. The workspaces and emails are
invented."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from spec2test import cli
from spec2test.deploy import (
    APP,
    AUTH_ENV,
    CONFIG_ENV,
    AuthConfigError,
    BudgetExceededError,
    Cap,
    CappedLLM,
    DeployConfigError,
    ModelChoice,
    UsageLedger,
    Workspace,
    auth,
    auth_secrets,
    data_handling,
    ledger,
    load_config,
    metrics,
    month_of,
)
from spec2test.extract import extract
from spec2test.llm import LLMClient, TokenUsage
from spec2test.review import Decision
from spec2test.schema import Requirement

FIXTURES = Path(__file__).parent / "fixtures"

CONFIG = """
data_dir = "data"

[llm]
provider = "kimi-code"

[cap]
monthly_tokens = 1_000_000

[workspaces.lamp-co]
name = "Lamp Co"
members = ["Ana@Lamp.example", "ben@lamp.example"]

[workspaces.gear-co]
name = "Gear Co"
members = ["cy@gear.example"]
"""


def call(model, input_tokens, output_tokens=0):
    return TokenUsage(model=model, input_tokens=input_tokens, output_tokens=output_tokens)


def write_config(tmp_path, text=CONFIG):
    path = tmp_path / "deploy.toml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path):
    return load_config(write_config(tmp_path))


# Config.


def test_config_resolves_data_dir_and_matches_members_in_any_case(config, tmp_path):
    assert config.data_dir == (tmp_path / "data").resolve()
    assert config.workspace_for("ana@lamp.example ") == "lamp-co"
    assert config.workspace_for("CY@gear.example") == "gear-co"
    assert config.workspace_for("eve@elsewhere.example") is None
    assert config.workspace_for(None) is None
    assert config.llm.model_id == "kimi-for-coding"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        # Kimi has no price, so only a token cap can hold it.
        (("monthly_tokens = 1_000_000", "monthly_usd = 10"), "set cap.monthly_tokens"),
        (("monthly_tokens = 1_000_000", ""), "set monthly_usd, monthly_tokens or both"),
        (('provider = "kimi-code"', 'provider = "openai"'), "unknown provider"),
        (('"cy@gear.example"', '"ben@lamp.example"'), "member of both"),
        (("[workspaces.gear-co]", "[workspaces.Gear_Co]"), "lowercase letters"),
        (('"cy@gear.example"', '"cy"'), "is not an email"),
    ],
)
def test_config_refuses_what_it_cannot_enforce(tmp_path, change, message):
    path = write_config(tmp_path, CONFIG.replace(*change))
    with pytest.raises(DeployConfigError, match=message):
        load_config(path)


def test_a_priced_model_can_take_a_dollar_cap(tmp_path):
    text = CONFIG.replace('provider = "kimi-code"', 'provider = "gemini"')
    config = load_config(write_config(tmp_path, text.replace("monthly_tokens", "monthly_usd")))
    assert config.cap.monthly_usd == 1_000_000


def test_data_page_brackets_what_the_config_does_not_say(config):
    page = data_handling(config)
    assert "Moonshot AI (Kimi)" in page
    assert "`kimi-for-coding`" in page
    assert "[host and region]" in page and "[contact]" in page and "[N] days" in page
    config.data_page.contact = "pilot@spec2test.example"
    assert "Write to pilot@spec2test.example." in data_handling(config)


def test_missing_config_is_an_error(tmp_path):
    with pytest.raises(DeployConfigError, match="no deployment config"):
        load_config(tmp_path / "nope.toml")


# Sign-in secrets.


def test_auth_secrets_come_from_the_environment():
    env = {var: f"value-{key}" for key, var in AUTH_ENV.items()}
    env[AUTH_ENV["cookie_secret"]] = "x" * 32
    text = auth_secrets(env)
    assert text.startswith("[auth]\n")
    assert 'client_id = "value-client_id"' in text
    with pytest.raises(AuthConfigError, match="SPEC2TEST_AUTH_CLIENT_SECRET"):
        auth_secrets({k: v for k, v in env.items() if k != AUTH_ENV["client_secret"]})
    with pytest.raises(AuthConfigError, match="32 characters"):
        auth_secrets({**env, AUTH_ENV["cookie_secret"]: "short"})


# The cost cap.


def test_ledger_sums_a_month_over_every_workspace_or_one(tmp_path):
    book = UsageLedger(tmp_path / "usage.sqlite")
    oct_ = datetime(2026, 10, 31, 23, 0, tzinfo=UTC)
    book.add("a", call("gemini-3.1-pro-preview", 1000), at=oct_)
    book.add("b", call("kimi-for-coding", 10, 5), at=oct_)
    book.add("a", call("kimi-for-coding", 7), at=datetime(2026, 11, 1, tzinfo=UTC))
    total = book.spend("2026-10")
    assert (total.calls, total.tokens, total.unpriced_calls) == (2, 1015, 1)
    assert total.dollars == pytest.approx(0.002)  # 1,000 input tokens at $2 per million
    assert book.spend("2026-10", "b").tokens == 15
    assert book.spend("2026-11").tokens == 7


class Billed(LLMClient):
    """The widget lamp fixtures carry no token counts; this bills 10 tokens a call."""

    def __init__(self, inner):
        self.inner = inner
        self.model = "kimi-for-coding"

    def generate(self, prompt, schema, *, system=None):
        self.record_usage(self.model, 8, 2)
        return self.inner.generate(prompt, schema, system=system)


def test_capped_llm_records_calls_and_stops_once_the_cap_is_reached(
    tmp_path, widget_lamp, fake_llm
):
    book = UsageLedger(tmp_path / "usage.sqlite")
    llm = CappedLLM(Billed(fake_llm), book, Cap(monthly_tokens=25), "lamp-co")
    with pytest.raises(BudgetExceededError, match="30 of 25 tokens"):
        extract(widget_lamp, llm)
    # Three calls ran and took the month over the cap; the fourth never reached the model.
    spent = book.spend(month_of(datetime.now(UTC)), "lamp-co")
    assert (spent.calls, spent.tokens) == (3, 30)
    assert len(llm.usage) == len(fake_llm.usage) == 3


def test_a_spent_budget_refuses_the_first_call(tmp_path, fake_llm):
    book = UsageLedger(tmp_path / "usage.sqlite")
    book.add("gear-co", call("kimi-for-coding", 600, 400))
    llm = CappedLLM(fake_llm, book, Cap(monthly_tokens=1000), "lamp-co")
    with pytest.raises(BudgetExceededError, match="1,000 of 1,000 tokens"):
        llm.generate("anything", Requirement)
    assert fake_llm.usage == []  # the model was never called


# Workspaces.


def test_workspaces_keep_separate_logs_and_delete_removes_only_one(config, generation, widget_lamp):
    lamp, gear = Workspace(config, "lamp-co"), Workspace(config, "gear-co")
    gid = lamp.store().add(generation, widget_lamp)
    lamp.store().record(gid, generation.items[0].id, Decision.ACCEPT, reviewer="ana")
    gear.store().add(generation)
    assert lamp.review_db != gear.review_db
    assert gear.store().latest(gid) == {}  # Gear Co's log holds no Lamp Co decision

    ledger(config).add("lamp-co", call("kimi-for-coding", 5, 5))
    m = metrics(config, "lamp-co")
    assert (m.documents, m.items) == (1, len(generation.items))
    assert (m.reviewed, m.acceptance_rate) == (1, 1.0)
    assert (m.calls, m.tokens) == (1, 10)

    assert lamp.delete() is True
    assert not lamp.dir.exists() and gear.has_data()
    assert lamp.delete() is False
    after = metrics(config, "lamp-co")
    assert (after.documents, after.reviewed, after.tokens) == (0, 0, 10)  # the ledger stays
    with pytest.raises(KeyError):
        Workspace(config, "nobody")


def test_cli_check_metrics_and_delete(tmp_path, config, generation, capsys):
    path = tmp_path / "deploy.toml"
    Workspace(config, "gear-co").store().add(generation)
    assert cli.main(["deploy", "check", str(path)]) == 0
    out = capsys.readouterr().out
    assert "model: kimi-for-coding (kimi-code)" in out
    assert "cap: 1,000,000 tokens a month" in out
    assert "workspaces: 2, 3 members" in out
    assert "sign-in: not configured" in out

    assert cli.main(["deploy", "metrics", str(path)]) == 0
    assert "gear-co: 1 documents" in capsys.readouterr().out

    assert cli.main(["deploy", "delete", str(path), "gear-co"]) == 2
    assert "pass --yes" in capsys.readouterr().err
    assert cli.main(["deploy", "delete", str(path), "gear-co", "--yes"]) == 0
    assert "deleted" in capsys.readouterr().out
    assert not Workspace(config, "gear-co").has_data()


# The app, driven offline.


@pytest.fixture
def app(tmp_path, monkeypatch, fake_llm):
    path = write_config(tmp_path)
    monkeypatch.setenv(CONFIG_ENV, str(path))
    monkeypatch.setattr(ModelChoice, "client", lambda self: fake_llm)
    signed_out = []
    monkeypatch.setattr(auth, "sign_out", lambda: signed_out.append(True))

    def start(email=None):
        monkeypatch.setattr(auth, "current_email", lambda: email)
        at = AppTest.from_file(str(APP), default_timeout=60)
        at.run()
        assert not at.exception
        return at

    return start


def test_signed_out_users_see_sign_in_and_the_data_page(app):
    at = app()
    assert at.title[0].value == "Spec2Test"
    assert at.button[0].label == "Sign in"
    assert any("How your data is handled" in m.value for m in at.markdown)
    assert not at.sidebar.radio


def test_an_uninvited_email_gets_no_workspace(app):
    at = app("eve@elsewhere.example")
    assert "is not invited" in at.warning[0].value
    assert not at.sidebar.radio and not at.sidebar.file_uploader


def test_a_member_runs_reviews_and_deletes_in_their_own_workspace(
    app, config, widget_lamp, fake_llm
):
    at = app("ben@lamp.example")
    assert at.title[0].value == "Spec2Test · Lamp Co"
    data = widget_lamp.model_dump_json().encode("utf-8")
    at.sidebar.file_uploader[0].set_value(("widget-lamp.json", data, "application/json"))
    at.run()
    run = next(b for b in at.sidebar.button if b.label == "Run the pipeline")
    run.click().run()
    assert not at.exception
    assert at.title[0].value == "Review: widget-lamp"

    at.button(key=next(b.key for b in at.button if b.label == "Accept")).click().run()
    lamp = Workspace(config, "lamp-co")
    [saved] = lamp.store().generations()
    [decided] = lamp.store().latest(saved.id).values()
    assert decided.reviewer == "ben@lamp.example"  # the signed-in email, not a typed name
    assert not Workspace(config, "gear-co").has_data()
    month = month_of(datetime.now(UTC))
    assert ledger(config).spend(month, "lamp-co").calls == len(fake_llm.usage) > 0

    at.sidebar.radio[0].set_value("Your data").run()
    assert at.metric[0].value == "1"  # documents processed
    delete = next(b for b in at.button if b.label == "Delete this workspace's data")
    assert delete.disabled
    at.text_input(key="confirm-delete").input("lamp-co").run()
    next(b for b in at.button if b.label == "Delete this workspace's data").click().run()
    assert not at.exception
    assert at.success[0].value == "Deleted."
    assert not lamp.has_data()


def test_a_member_cannot_open_another_workspaces_review(app, config, generation):
    gid = Workspace(config, "gear-co").store().add(generation)
    at = app("ana@lamp.example")
    at.session_state.generation = gid
    at.run()
    assert not at.exception
    assert at.title[0].value == "Spec2Test · Lamp Co"  # not Gear Co's review


def test_a_spent_budget_blocks_runs_in_the_app(app, config, widget_lamp):
    ledger(config).add("gear-co", call("kimi-for-coding", 1_000_000))
    at = app("ana@lamp.example")
    data = widget_lamp.model_dump_json().encode("utf-8")
    at.sidebar.file_uploader[0].set_value(("widget-lamp.json", data, "application/json"))
    at.run()
    assert "budget is used up" in at.sidebar.error[0].value
    assert not any(b.label == "Run the pipeline" for b in at.sidebar.button)


def test_a_broken_config_shows_an_error_page(app, tmp_path):
    (tmp_path / "deploy.toml").write_text("data_dir = ", encoding="utf-8")
    at = app("ana@lamp.example")
    assert "not configured correctly" in at.error[0].value
