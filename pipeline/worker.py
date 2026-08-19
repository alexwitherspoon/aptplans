"""Long-running serial worker for the AptPlans Compose stack.

Stays up so `site`, `worker`, and `ollama` are one project. The systemd
timer execs into this container for a single job; this process does not
serve HTTP.
"""

from __future__ import annotations

import logging
import os
import time

log = logging.getLogger("aptplans.worker")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log.info(
        "worker idle host=%s model=%s",
        os.environ.get("OLLAMA_HOST", ""),
        os.environ.get("OLLAMA_MODEL", ""),
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
