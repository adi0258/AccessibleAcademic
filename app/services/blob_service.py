import base64
import hashlib
import hmac
import json
import os
import time


def generate_client_upload_token(pathname: str) -> dict:
    """
    Generate a Vercel Blob client upload token.
    Mirrors @vercel/blob's generateClientTokenFromReadWriteToken so the browser
    can PUT a file directly to Vercel Blob without routing it through the function.
    """
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN", "")
    if not blob_token:
        raise ValueError("BLOB_READ_WRITE_TOKEN is not configured")

    signing_key = blob_token.replace("vercel_blob_rw_", "", 1)

    payload = {
        "access": "public",
        "uploadId": f"{int(time.time() * 1000)}-{os.urandom(4).hex()}",
        "addRandomSuffix": True,
        "pathname": pathname,
    }

    token_data = base64.b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode()

    signature = hmac.new(
        signing_key.encode(), token_data.encode(), hashlib.sha256
    ).hexdigest()

    return {
        "clientToken": f"vercel_blob_client_{token_data}_{signature}",
        "uploadUrl": f"https://blob.vercel-storage.com/{pathname}",
    }
