"""Driver-managed retries for transient transactions and uncertain commits."""

from typing import Callable, TypeVar

from pymongo import ReadPreference
from pymongo.client_session import ClientSession
from pymongo.database import Database
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from observability import log_step

T = TypeVar("T")
RetryObserver = Callable[[PyMongoError], None]


def run_transaction(
    db: Database, callback: Callable[[ClientSession], T], *,
    run_id: str | None = None, on_retry: RetryObserver | None = None,
) -> T:
    retries: list[dict] = []

    def observed(session: ClientSession) -> T:
        try:
            return callback(session)
        except PyMongoError as error:
            if error.has_error_label("TransientTransactionError"):
                retries.append({"label": "TransientTransactionError", "code": getattr(error, "code", None)})
                if on_retry is not None:
                    on_retry(error)
            raise

    try:
        with db.client.start_session() as session:
            # with_transaction also retries ONLY commit on UnknownTransactionCommitResult.
            return session.with_transaction(
                observed, read_concern=ReadConcern("snapshot"),
                write_concern=WriteConcern("majority"),
                read_preference=ReadPreference.PRIMARY, max_commit_time_ms=10000,
            )
    finally:
        if run_id is not None:
            for attempt, error in enumerate(retries, 1):
                log_step(db, run_id, "transaction_retry", {"attempt": attempt}, error)
