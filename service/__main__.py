import argparse
import logging
import os

from .core import JsonSink, Transformer
from .mqtt_io import consume, publish_demo
from .templates import FixtureTemplates, PostgresTemplates, seed_demo


def main():
    parser = argparse.ArgumentParser(description="Modbus register transformation service")
    parser.add_argument("command", choices=["consume", "publish-demo", "seed-demo"])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    dsn = os.getenv("DATABASE_URL")
    if args.command == "seed-demo":
        seed_demo(dsn)
    elif args.command == "publish-demo":
        publish_demo()
    else:
        backend = os.getenv("TEMPLATE_BACKEND", "fixture")
        if backend == "fixture":
            templates = FixtureTemplates()
        elif backend == "postgres":
            templates = PostgresTemplates(dsn)
        else:
            parser.error("TEMPLATE_BACKEND must be fixture or postgres")
        timeout = float(os.getenv("STARLARK_TIMEOUT", "3"))
        if not 0 < timeout <= 10:
            parser.error("STARLARK_TIMEOUT must be in (0, 10] seconds")
        consume(Transformer(templates, JsonSink(os.getenv("OUTPUT_DIR", "./output")), timeout))


if __name__ == "__main__":
    main()
