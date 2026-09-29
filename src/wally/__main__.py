"""Entry point for `python -m wally` and the `wally` console script."""


from wally.cli import run_cli


def main() -> None:
    raise SystemExit(run_cli())


if __name__ == "__main__":
    main()
