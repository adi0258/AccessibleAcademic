import os

import boto3
from botocore.config import Config


def _r2_client():
    account_id = os.environ["R2_ACCOUNT_ID"]
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def generate_client_upload_token(pathname: str) -> dict:
    """
    Generate a presigned PUT URL so the browser can upload directly to Cloudflare R2,
    bypassing Vercel's 4.5 MB function body limit.
    Returns uploadUrl (presigned PUT) and publicUrl (the final accessible URL).
    """
    bucket = os.environ["R2_BUCKET_NAME"]
    public_base = os.environ["R2_PUBLIC_URL"].rstrip("/")

    presigned_url = _r2_client().generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": pathname},
        ExpiresIn=86400,  # 24 hours — large lecture uploads can take a while
    )

    return {
        "uploadUrl": presigned_url,
        "publicUrl": f"{public_base}/{pathname}",
    }


def upload_file_to_r2(local_path: str, key: str) -> str:
    """Upload a local file to R2 and return its public URL."""
    bucket = os.environ["R2_BUCKET_NAME"]
    public_base = os.environ["R2_PUBLIC_URL"].rstrip("/")
    with open(local_path, "rb") as f:
        _r2_client().put_object(Bucket=bucket, Key=key, Body=f)
    return f"{public_base}/{key}"


def delete_blob(blob_url: str) -> None:
    """Delete a file from R2 by its public URL. Silently ignores errors."""
    if not blob_url:
        return
    try:
        public_base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
        bucket = os.environ.get("R2_BUCKET_NAME", "")
        if not public_base or not bucket:
            return
        key = blob_url[len(public_base) + 1:]  # strip "public_base/"
        _r2_client().delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass
