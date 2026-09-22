from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

from trading_copilot.config import normalize_database_url
from trading_copilot.persistence.models import Base


async def migrate_database(source_url: str, target_url: str) -> dict[str, int]:
    source = create_async_engine(normalize_database_url(source_url))
    target = create_async_engine(normalize_database_url(target_url))
    tables = Base.metadata.sorted_tables
    try:
        async with source.connect() as source_connection, target.begin() as target_connection:
            source_names = await source_connection.run_sync(
                lambda connection: set(inspect(connection).get_table_names())
            )
            target_names = await target_connection.run_sync(
                lambda connection: set(inspect(connection).get_table_names())
            )
            required = {table.name for table in tables}
            missing_source = required - source_names
            missing_target = required - target_names
            if missing_source:
                raise RuntimeError(
                    "Source database is missing migrated tables: "
                    + ", ".join(sorted(missing_source))
                )
            if missing_target:
                raise RuntimeError(
                    "Target database is missing migrated tables: "
                    + ", ".join(sorted(missing_target))
                )
            occupied = []
            for table in tables:
                if await target_connection.scalar(select(func.count()).select_from(table)):
                    occupied.append(table.name)
            if occupied:
                raise RuntimeError(
                    "Target migration aborted because tables already contain data: "
                    + ", ".join(occupied)
                )

            copied: dict[str, int] = {}
            for table in tables:
                rows = (await source_connection.execute(select(table))).mappings().all()
                if rows:
                    await target_connection.execute(table.insert(), [dict(row) for row in rows])
                copied[table.name] = len(rows)
            return copied
    finally:
        await source.dispose()
        await target.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(prog="trading-copilot")
    commands = parser.add_subparsers(dest="command", required=True)
    migrate = commands.add_parser("migrate-database")
    migrate.add_argument("--source", required=True)
    migrate.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.command == "migrate-database":
        copied = asyncio.run(migrate_database(args.source, args.target))
        print("Database migration completed safely.")
        for table, count in copied.items():
            print(f"{table}: {count}")


if __name__ == "__main__":
    main()
