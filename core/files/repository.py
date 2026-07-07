"""Tenant-scoped repository for stored files."""

from core.files.models import StoredFile
from shared.repository import Repository


class FileRepository(Repository[StoredFile]):
    model = StoredFile
