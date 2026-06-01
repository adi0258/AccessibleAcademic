import base64
import hashlib
import hmac as _hmac
import os
import time
from urllib.parse import urlencode

import requests


def _parse_store_id(token: str) -> str:
    # Token format: vercel_blob_rw_{storeId}_{secret}
    parts = token.split("_")
    return parts[3] if len(parts) > 3 else ""


def _to_base64url(data: bytes) -> str:
    return base64.b64encode(data).decode().replace("+", "-").replace("/", "_").rstrip("=")


def generate_client_upload_token(pathname: str) -> dict:
    """
    Generate a presigned Vercel Blob upload URL using the @vercel/blob SDK presign flow.
    The browser can PUT a file directly to the returned uploadUrl without routing through
    the Vercel function, bypassing the 4.5 MB request-body limit.
    """
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not blob_token:
        raise ValueError("BLOB_READ_WRITE_TOKEN is not configured")

    store_id = _parse_store_id(blob_token)
    valid_until_ms = int((time.time() + 300) * 1000)  # 5 minutes

    resp = requests.post(
        "https://vercel.com/api/blob/signed-token",
        headers={
            "authorization": f"Bearer {blob_token}",
            "content-type": "application/json",
            "x-vercel-blob-store-id": store_id,
            "x-api-version": "12",
        },
        json={
            "pathname": pathname,
            "operations": ["put"],
            "validUntil": valid_until_ms,
        },
        timeout=10,
    )
    if not resp.ok:
        raise ValueError(
            f"Vercel Blob signed-token error {resp.status_code}: {resp.text[:300]}"
        )

    data = resp.json()
    client_signing_token = data.get("clientSigningToken")
    delegation_token = data.get("delegationToken")

    if not client_signing_token or not delegation_token:
        raise ValueError(f"Unexpected signed-token response keys: {list(data.keys())}")

    # Mirror canonicalString(pathname, entries, "put") from @vercel/blob SDK.
    # entries = [("vercel-blob-allow-overwrite", "true")] so duplicates don't error.
    entries = [("vercel-blob-allow-overwrite", "true")]
    lines = [f"operation=put", f"pathname={pathname}"] + [f"{k}={v}" for k, v in entries]
    lines.sort()  # UTF-8 lexicographic order, matching SDK's compareUtf8
    canonical = "\n".join(lines)

    # hmacSha256Base64Url: HMAC-SHA256 → base64url (no padding)
    sig_bytes = _hmac.new(
        client_signing_token.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signature = _to_base64url(sig_bytes)

    # buildPresignedPutUrl: https://vercel.com/api/blob/?pathname=…&<entries>&delegation&signature
    params = {"pathname": pathname}
    params.update(dict(entries))
    params["vercel-blob-delegation"] = delegation_token
    params["vercel-blob-signature"] = signature

    presigned_url = "https://vercel.com/api/blob/?" + urlencode(params)

    return {
        "uploadUrl": presigned_url,
        "storeId": store_id,
        "clientToken": None,
    }
