import os
from dataclasses import dataclass

@dataclass
class Config:
    storage_root: str
    database_url: str

    @classmethod
    def from_env(cls) -> "Config":
        storage_root = os.environ.get("STORAGE_ROOT")
        if not storage_root:
            raise ValueError("STORAGE_ROOT environment variable is required")
        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            raise ValueError("DATABASE_URL environment variable is required")
        return cls(storage_root=storage_root, database_url=database_url)
