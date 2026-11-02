"""Read-only reference retrieval through the trusted service boundary."""
from uuid import UUID

from packages_py.storage import PostgresStorage


class ReferenceRetrieval:
    def __init__(self, storage: PostgresStorage):
        self.storage = storage

    def search(self, *, subject_id: UUID, session_id: UUID, query: str,
               source_ids: list[UUID], limit: int = 5):
        return self.storage.retrieve_references(
            subject_id, session_id, query, source_ids, limit
        )
