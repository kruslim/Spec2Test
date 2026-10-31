"""Deploy: the hosted app for design partners. Sign-in, one data folder per workspace, a
delete-my-data action, a monthly LLM cost cap and usage metrics.

The Streamlit app (`spec2test.deploy.app`) needs the `deploy` extra; nothing here imports it.
"""

from spec2test.deploy.budget import (
    BudgetExceededError,
    CappedLLM,
    Spend,
    UsageLedger,
    month_of,
    over_cap,
)
from spec2test.deploy.config import (
    CONFIG_ENV,
    Cap,
    DataPage,
    DeployConfig,
    DeployConfigError,
    ModelChoice,
    WorkspaceConfig,
    data_handling,
    load_config,
)
from spec2test.deploy.serve import APP, AUTH_ENV, AuthConfigError, auth_secrets, serve
from spec2test.deploy.workspace import (
    Workspace,
    WorkspaceMetrics,
    ledger,
    metrics,
    metrics_report,
    workspaces_dir,
)

__all__ = [
    "APP",
    "AUTH_ENV",
    "CONFIG_ENV",
    "AuthConfigError",
    "BudgetExceededError",
    "Cap",
    "CappedLLM",
    "DataPage",
    "DeployConfig",
    "DeployConfigError",
    "ModelChoice",
    "Spend",
    "UsageLedger",
    "Workspace",
    "WorkspaceConfig",
    "WorkspaceMetrics",
    "auth_secrets",
    "data_handling",
    "ledger",
    "load_config",
    "metrics",
    "metrics_report",
    "month_of",
    "over_cap",
    "serve",
    "workspaces_dir",
]
