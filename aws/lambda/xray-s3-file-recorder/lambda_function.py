from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote_plus

import boto3

from repository import connection, upsert_s3_file
from s3_key import parse_s3_key


s3_client = boto3.client("s3")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """S3 ObjectCreated 이벤트를 S3_FILE에 idempotent하게 기록한다."""

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

            head = s3_client.head_object(Bucket=bucket, Key=parsed.s3_key)
            metadata = {str(k).lower(): str(v) for k, v in (head.get("Metadata") or {}).items()}
            usage_name = metadata.get("usage") or parsed.usage_name
            source_order = _optional_int(metadata.get("source_order"))
            if source_order is None:
                source_order = parsed.source_order
            encoded_original_name = metadata.get("original_name")
            original_name = (
                unquote_plus(encoded_original_name)
                if encoded_original_name
                else parsed.original_name
            )

            with connection() as conn:
                upsert_s3_file(
                    conn,
                    artifact_id=parsed.artifact_id,
                    module_type=parsed.module_type,
                    usage_name=usage_name,
                    source_order=source_order,
                    original_name=original_name,
                    s3_key=parsed.s3_key,
                    bucket_name=bucket,
                    size_bytes=_optional_int(head.get("ContentLength") or obj.get("size")),
                    etag=_optional_string(head.get("ETag") or obj.get("eTag") or obj.get("etag")),
                )
            processed += 1
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")

    result = {"processed": processed, "ignored": ignored, "errors": errors}
    print(json.dumps(result, ensure_ascii=False))
    if errors:
        raise RuntimeError("; ".join(errors))
    return result


def _optional_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return int(value)


def _optional_string(value: Any) -> str | None:
    return None if value is None else str(value).strip('"')
