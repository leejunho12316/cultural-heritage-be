from __future__ import annotations

import json
from typing import Any

from repository import connection, upsert_s3_file
from s3_key import parse_s3_key


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Persist S3 ObjectCreated records one transaction at a time.

    S3 event delivery is at-least-once.  ``s3_key`` is unique in PostgreSQL,
    so retries update the same row.  A separate transaction per record avoids
    one malformed record aborting every successful record in a multi-record
    invocation.
    """

    processed = 0
    ignored = 0
    errors: list[str] = []

    for record in event.get("Records") or []:
        try:
            s3 = record["s3"]
            bucket = str(s3["bucket"]["name"])
            obj = s3["object"]
            parsed = parse_s3_key(str(obj["key"]))
            if parsed is None:
                ignored += 1
                continue

            with connection() as conn:
                upsert_s3_file(
                    conn,
                    artifact_id=parsed.artifact_id,
                    module_type=parsed.module_type,
                    usage_name=parsed.usage_name,
                    source_order=parsed.source_order,
                    s3_key=parsed.s3_key,
                    bucket_name=bucket,
                    size_bytes=_optional_int(obj.get("size")),
                    etag=_optional_string(obj.get("eTag") or obj.get("etag")),
                )
            processed += 1
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    result = {"processed": processed, "ignored": ignored, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        # Force retry only after every valid record had a chance to commit.
        raise RuntimeError("; ".join(errors))
    return result


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value).strip('"')
