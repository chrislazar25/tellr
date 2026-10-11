"""Connectivity check for configured MongoDB; output is exactly ok or fail."""

from db.connection import connect


def ping() -> bool:
    db = None
    try:
        db = connect()
        db.client.admin.command("ping")
        return True
    except Exception:
        # Connection/authentication errors can contain private connection details.
        return False
    finally:
        if db is not None:
            db.client.close()


def main() -> int:
    success = ping()
    print("ok" if success else "fail")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
