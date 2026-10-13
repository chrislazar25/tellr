"""Inspect the once-scored Lily caches without making new extraction requests."""
from experiments.failure_hunt.sealed_score import main


if __name__ == "__main__":
    try:
        main(prefix="sealed2")
    except Exception as error:
        from config import print_error
        print_error(error)
        raise SystemExit(1)
