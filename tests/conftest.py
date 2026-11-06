import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
FIXTURES = Path(__file__).parent / "netschool" / "fixtures"   # из HAR: tools/har_to_fixtures.py
DUMPS = Path(os.environ.get("NS_DUMPS") or ROOT / "dumps")    # с живого сервера: tools/dump_pages.py

os.environ.setdefault("APP_ENV", "test")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_time():
    """Каждый тест начинает с настоящего времени."""
    from app.timeutil import set_fake_now
    yield
    set_fake_now(None)
