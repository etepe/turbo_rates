"""Phase 7 FX test fixtures (V2 scaffold).

Shared fixtures for the FX layer. Concrete numerical fixtures will be added
when M-105..M-107 are implemented; for now the conftest only carries the
canonical pair and convention so test files can import a stable handle.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from rates.fx.conventions import FXConventions
from rates.fx.types import CurrencyPair

_VAL_DATE = date(2026, 6, 12)


@pytest.fixture(scope="session")
def fx_val_date() -> date:
    return _VAL_DATE


@pytest.fixture(scope="session")
def usdtry_pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="USD", code="USDTRY")


@pytest.fixture(scope="session")
def eurtry_pair() -> CurrencyPair:
    return CurrencyPair(domestic="TRY", foreign="EUR", code="EURTRY")


@pytest.fixture(scope="session")
def fx_conventions(conventions_yaml: Path) -> FXConventions:
    return FXConventions.load(conventions_yaml)
