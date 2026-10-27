from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """`photo-embed` is on by default; tests must never download the fixtures' photo links."""

    def refuse(url, timeout=10.0):
        raise OSError("network disabled in tests")

    monkeypatch.setattr("vcardmanager.clean.photos.default_fetch", refuse)
FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def google_v30() -> Path:
    """Google Contacts / DAVx5 export, vCard 3.0, UTF-8 (anonymized from a real export)."""
    return FIXTURES / "google_v30.vcf"


@pytest.fixture
def legacy_v21_gbk() -> Path:
    """Legacy phone/mail-client export, vCard 2.1, GBK (anonymized from a real export)."""
    return FIXTURES / "legacy_v21_gbk.vcf"


def uid(card) -> str:
    return card.get("UID").value


def by_uid(cards) -> dict:
    return {uid(c): c for c in cards}


def by_fn(cards) -> dict:
    return {c.get("FN").value: c for c in cards if c.get("FN")}
