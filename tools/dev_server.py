"""Run Doiamo with invented routes, so the UI can be worked on without a key.

    ./dev.sh                      # or: python -m tools.dev_server

Weather, air quality, points of interest and place search are all real in this
mode — they come from free APIs that need no key. Only the routing is faked,
because that is the part that costs quota.
"""
from __future__ import annotations

import os

import uvicorn

from backend import main
from tools.offline_engine import OfflineEngine

_lifespan = main.lifespan


@main.asynccontextmanager
async def _offline_lifespan(app):
    async with _lifespan(app):
        app.state.engine = OfflineEngine()
        yield


def run() -> None:
    main.app.router.lifespan_context = _offline_lifespan
    port = int(os.environ.get("PORT", "8001"))
    print("Doiamo dev server (offline routing) on http://127.0.0.1:{}".format(port))
    print("Routing is invented; weather, air, POIs and search are real.")
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    run()
