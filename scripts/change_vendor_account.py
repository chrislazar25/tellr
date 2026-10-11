"""Change an account through the same transactional fence used by the worker."""

import argparse
import json

from db.connection import connect
from config import print_error
from db.vendors import update_vendor_bank_account


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vendor_id")
    parser.add_argument("bank_account")
    args = parser.parse_args()
    db = None
    try:
        db = connect()
        update_vendor_bank_account(db, args.vendor_id, args.bank_account)
        print(json.dumps({"status": "ok", "vendor_id": args.vendor_id}))
        return 0
    except Exception as error:
        print_error(error)
        return 1
    finally:
        if db is not None:
            db.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
