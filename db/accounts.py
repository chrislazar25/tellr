"""Compare a spoken account suffix with a full bank account identifier."""


def destination_matches(destination: str, bank_account: str) -> bool:
    """Numeric destinations represent spoken endings; other values are exact IDs."""
    return destination == bank_account or (
        destination.isascii() and destination.isdigit() and bank_account.endswith(destination)
    )
