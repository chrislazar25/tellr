"""Load API keys from the repo's ignored .env files; never print them."""
from pathlib import Path
from dotenv import dotenv_values
ROOT = Path(__file__).resolve().parents[2]


def get(name):
    """Read on demand; secret-file values override .env without interpolation."""
    values = {**dotenv_values(ROOT / ".env", interpolate=False),
              **dotenv_values(ROOT / ".env.secrets", interpolate=False)}
    value = values.get(name)
    if not value:
        raise ValueError(f"Missing {name} in ignored environment files")
    return value
