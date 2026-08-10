from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import psycopg2
from psycopg2 import sql


@contextmanager
def connection() -> Iterator[Any]:
    conn = psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=int(os.getenv("DB_PORT", "5432")),
        dbname=os.getenv("DB_NAME", "conservation"),
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        sslmode=os.getenv("DB_SSLMODE", "prefer"),
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def upsert_s3_file(
    conn: Any,
    *,
    artifact_id: str,
    module_type: str,
    usage_name: str,
    source_order: int | None,
    original_name: str | None,
    s3_key: str,
    bucket_name: str,
    size_bytes: int | None,
    etag: str | None,
) -> None:
    schema = os.getenv("DB_SCHEMA", "public")
    table = os.getenv("S3_FILE_TABLE", "s3_file")
    available = _columns(conn, schema, table)
    now = datetime.now(timezone.utc)

    values: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "artifact_id": artifact_id,
        "module_type": module_type,
        "usage_name": usage_name,
        "source_order": source_order,
        "original_name": original_name,
        "s3_key": s3_key,
        "bucket_name": bucket_name,
        "size_bytes": size_bytes,
        "etag": etag,
        "status": "COMPLETED",
        "created_at": now,
        "updated_at": now,
    }
    insert_values = {key: value for key, value in values.items() if key in available}
    required = {"id", "artifact_id", "module_type", "usage_name", "s3_key"}
    missing = required - insert_values.keys()
    if missing:
        raise RuntimeError(
            f"{schema}.{table} is missing required columns: {sorted(missing)}"
        )

    update_names = [
        name
        for name in insert_values
        if name not in {
            "id",
            "s3_key",
            "artifact_id",
            "module_type",
            "usage_name",
            "created_at",
            "updated_at",
        }
    ]
    assignments = [
        sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(name), sql.Identifier(name))
        for name in update_names
    ]
    if "updated_at" in available:
        assignments.append(sql.SQL("updated_at = CURRENT_TIMESTAMP"))

    query = sql.SQL("INSERT INTO {}.{} ({}) VALUES ({}) ON CONFLICT (s3_key) DO UPDATE SET {}").format(
        sql.Identifier(schema),
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, insert_values.keys())),
        sql.SQL(", ").join(sql.Placeholder() for _ in insert_values),
        sql.SQL(", ").join(assignments) if assignments else sql.SQL("s3_key = EXCLUDED.s3_key"),
    )
    with conn.cursor() as cursor:
        cursor.execute(query, list(insert_values.values()))


def _columns(conn: Any, schema: str, table: str) -> set[str]:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        )
        return {row[0] for row in cursor.fetchall()}