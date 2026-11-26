import argparse
import logging
from datetime import date
from pathlib import Path

from alembic import command
from alembic.config import Config
from openai import OpenAI

from greenlight.config import get_settings
from greenlight.evaluation.cases import Case, load_cases
from greenlight.evaluation.recording import Recording
from greenlight.evaluation.report import load_price, render, summary
from greenlight.evaluation.runner import Run, replay, run
from greenlight.infrastructure.ai.document_readers import ModelUnavailable
from greenlight.infrastructure.ai.groq_model import GROQ_URL, GroqModel
from greenlight.infrastructure.seed import seed_demo
from greenlight.web.container import build_container

log = logging.getLogger("greenlight")


def _project_root() -> Path:
    """The folder holding alembic.ini: the checkout locally, /app in the container."""
    for candidate in (Path.cwd(), Path(__file__).resolve().parents[2]):
        if (candidate / "alembic.ini").exists():
            return candidate
    raise SystemExit("Run this from the folder that holds alembic.ini.")


def migrate() -> None:
    root = _project_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
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


def evaluate(record: bool, replay_from: Path | None) -> None:
    root = _project_root()
    evals = root / "evals"
    cases = load_cases(evals)
    outcome = replay(cases, Recording.load(replay_from)) if replay_from else _read_with_groq(cases)
    if record:
        outcome.recording().save(evals / "recorded" / f"{outcome.label}.json")
    price = load_price(evals / "pricing.json", outcome.model_name)
    (root / "docs" / "ai-evaluation.md").write_text(render(outcome, price))
    print(summary(outcome))


def _read_with_groq(cases: list[Case]) -> Run:
    settings = get_settings()
    api_key = settings.groq_api_key.get_secret_value()
    if not api_key:
        raise SystemExit(
            "Set GROQ_API_KEY in .env to evaluate against Groq, "
            "or score a saved recording with --replay."
        )
    # The free tier allows a few requests a minute. The client waits as long as Groq's
    # retry-after asks, so a run is slow rather than incomplete.
    patient = OpenAI(api_key=api_key, base_url=GROQ_URL, timeout=60, max_retries=10)
    today = date.today()
    try:
        return run(
            cases,
            lambda on_usage: GroqModel(
                api_key, settings.groq_model, client=patient, on_usage=on_usage
            ),
            label=f"groq-{today.isoformat()}",
            model_name=settings.groq_model,
            ran_on=today,
        )
    except ModelUnavailable as error:
        raise SystemExit(f"Groq could not be reached, so nothing was written: {error}") from error


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="greenlight")
    parser.add_argument("command", choices=["migrate", "seed", "prepare", "eval"])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--provider", choices=["groq"], help="eval: the model to read with")
    source.add_argument(
        "--replay", type=Path, metavar="PATH", help="eval: score a saved recording again"
    )
    parser.add_argument("--record", action="store_true", help="eval: save the model's answers")
    arguments = parser.parse_args()
    chosen = arguments.command
    if chosen == "eval":
        if not (arguments.provider or arguments.replay):
            parser.error("eval needs --provider groq or --replay PATH.")
        if arguments.record and arguments.replay:
            parser.error("--record saves a new run, so it can't be used with --replay.")
        evaluate(arguments.record, arguments.replay)
    if chosen in ("migrate", "prepare"):
        migrate()
    if chosen == "seed" or (chosen == "prepare" and get_settings().environment != "production"):
        seed()


if __name__ == "__main__":
    main()
