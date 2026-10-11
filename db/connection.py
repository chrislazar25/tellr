from pymongo import MongoClient
from pymongo.database import Database
from pymongo.write_concern import WriteConcern

from config import mongodb_uri


def connect(uri: str | None = None) -> Database:
    client = MongoClient(uri or mongodb_uri(), serverSelectionTimeoutMS=5000, tz_aware=True)
    return client.get_default_database("mongodb_hackathon").with_options(
        write_concern=WriteConcern("majority")
    )
