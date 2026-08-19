"""One serial pipeline pass. Invoked by systemd via `compose exec worker`."""

from __future__ import annotations

import logging

log = logging.getLogger("aptplans.job")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    log.info("no catalog jobs yet; worker is idle")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
