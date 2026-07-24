"""Conftest for live API tests.

Overrides HA test framework's socket blocking so we can make real
HTTP requests to the SmartyPlants API.
"""

import os

import pytest
from pytest_socket import _remove_restrictions

# When set (in CI), missing credentials are a hard error instead of a skip.
# The whole point of the live suite is to detect real-world API drift, so a
# run where every test silently skips must NOT report success.
_REQUIRE_LIVE = os.environ.get("SMARTYPLANTS_REQUIRE_LIVE") == "1"
_HAVE_CREDS = bool(os.environ.get("SMARTYPLANTS_EMAIL")) and bool(
    os.environ.get("SMARTYPLANTS_PASSWORD")
)


def pytest_configure(config: pytest.Config) -> None:
    """Fail loudly if live tests are required but cannot run.

    Without this, absent credentials make the entire module skip, and a
    pytest run of only-skipped tests exits 0 - so CI shows green while the
    live API is never actually exercised.
    """
    if _REQUIRE_LIVE and not _HAVE_CREDS:
        pytest.exit(
            "SMARTYPLANTS_REQUIRE_LIVE=1 but SMARTYPLANTS_EMAIL / "
            "SMARTYPLANTS_PASSWORD are not set. The live API tests would be "
            "skipped, which in CI means API changes go undetected. Set the "
            "credentials (repo secrets) or unset SMARTYPLANTS_REQUIRE_LIVE "
            "for local runs.",
            returncode=1,
        )


@pytest.fixture(autouse=True)
def _allow_real_network():
    """Re-enable real sockets and remove connect restrictions."""
    _remove_restrictions()
    yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations():
    """Override the root conftest fixture - not needed for live tests."""
    yield
