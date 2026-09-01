import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import pytest


@pytest.fixture(autouse=True)
def _empty_caches():
    """Caches are module-global and outlive a request by design, so a test
    would otherwise be served the previous test's answer."""
    from backend import main

    main.search_cache.clear()
    main.route_cache.clear()
    main.poi_cache.clear()
    yield
