"""Pytest fixtures shared by every test."""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load custom_components/ during tests.

    Required by pytest-homeassistant-custom-component; without it your
    integration is invisible to the test hass instance.
    """
    yield


@pytest.fixture
def simulator(hass):
    """A ready-to-use Simulator bound to the test hass instance."""
    from .simulator import Simulator

    return Simulator(hass)
