import argparse
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from greenlight.config import get_settings
from greenlight.infrastructure.seed import seed_demo
from greenlight.web.container import build_container

log = logging.getLogger("greenlight")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def migrate() -> None:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def seed() -> None:
    settings = get_settings()
    if settings.environment == "production":
        raise SystemExit("Refusing to load demo data into production.")
    container = build_container(settings)
    if seed_demo(container.session_factory, container.approvals):
        log.info("Loaded the Orvane demo organisation.")
    else:
        log.info("People already exist, demo data not loaded.")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="greenlight")
    parser.add_argument("command", choices=["migrate", "seed", "prepare"])
    chosen = parser.parse_args().command
    if chosen in ("migrate", "prepare"):
        migrate()
    if chosen == "seed" or (chosen == "prepare" and get_settings().environment != "production"):
        seed()


if __name__ == "__main__":
    main()
