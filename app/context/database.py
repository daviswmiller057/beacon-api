import argparse
import sqlite3
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path
from typing import Iterator


MIGRATIONS = {
    1: "001_context_registry",
    2: "002_conversation",
}
LATEST_SCHEMA_VERSION = max(MIGRATIONS)


class ContextDatabase:
    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def upgrade(self) -> None:
        with self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row[0] for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for version in range(1, LATEST_SCHEMA_VERSION + 1):
                if version in applied:
                    continue
                sql = files("app.context.migrations").joinpath(
                    f"{MIGRATIONS[version]}.sql"
                ).read_text()
                connection.executescript(sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, applied_at) "
                    "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                    (version,),
                )

    def downgrade(self) -> None:
        """Downgrade one version; intended for isolated development/test databases."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT max(version) FROM schema_migrations"
            ).fetchone()
            version = row[0] if row else None
            if version is None:
                return
            sql = files("app.context.migrations").joinpath(
                f"{MIGRATIONS[version]}.down.sql"
            ).read_text()
            connection.executescript(sql)
            connection.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def main() -> None:
    from app.config import get_settings

    parser = argparse.ArgumentParser(description="Manage Context Registry schema")
    parser.add_argument("command", choices=("upgrade", "downgrade"))
    args = parser.parse_args()
    database = ContextDatabase(get_settings().context_database_path)
    getattr(database, args.command)()


if __name__ == "__main__":
    main()
