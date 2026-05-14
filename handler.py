"""
AWS Lambda entry point for FloLabs Email Automation.
Function name: flolabs-email-automation   Handler: handler.handler

Triggered hourly by EventBridge. Execution flow:
  1. Override env vars so config.py uses /tmp/ paths and auto-sends
  2. Download token.json + credentials.json from S3 -> /tmp/
  3. Run main.main() -- processes unread emails, sends replies
  4. Upload (possibly OAuth-refreshed) token.json back to S3
  5. Return JSON result

Environment variables expected on the Lambda function (set by deploy.ps1):
  S3_BUCKET, S3_PREFIX, EMAIL_ADDRESS, EMAIL_PASSWORD, OPENAI_API_KEY,
  NOTION_API_TOKEN, NOTION_DATABASE_ID, DISCORD_WEBHOOK_URL, and all others
  from .env.  REPLY_MODE is forced to "auto" in code regardless of the value
  stored in the env var.
"""

import json
import logging
import os
import traceback

import boto3

logger = logging.getLogger("email_automation.lambda")
logger.setLevel(logging.INFO)

# S3 config -- set via Lambda environment variables by deploy.ps1
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "email-automation/")
S3_TOKEN_KEY = S3_PREFIX + "token.json"
S3_CREDENTIALS_KEY = S3_PREFIX + "credentials.json"

# Writable temp paths available inside Lambda
LOCAL_TOKEN = "/tmp/token.json"
LOCAL_CREDENTIALS = "/tmp/credentials.json"


def _download(s3_client, bucket: str, key: str, dest: str) -> None:
    logger.info("S3 download: s3://%s/%s -> %s", bucket, key, dest)
    s3_client.download_file(bucket, key, dest)


def _upload(s3_client, src: str, bucket: str, key: str) -> None:
    if os.path.exists(src):
        logger.info("S3 upload: %s -> s3://%s/%s", src, bucket, key)
        s3_client.upload_file(src, bucket, key)
    else:
        logger.warning("Upload skipped -- file not found: %s", src)


def handler(event, context):
    """Lambda entry point. Called by EventBridge every hour."""
    logger.info("Invoked. event=%s", json.dumps(event, default=str))

    # -- 1. Force env vars before any src import reads them ------------------
    # config.py uses os.getenv() inside dataclass field_factory calls, so we
    # must set these before the first `from src.config import Config` anywhere.
    os.environ["GMAIL_TOKEN_FILE"] = LOCAL_TOKEN
    os.environ["GMAIL_CREDENTIALS_FILE"] = LOCAL_CREDENTIALS
    os.environ["REPLY_MODE"] = "auto"       # always send; never queue in Lambda
    os.environ.setdefault("LOG_FILE", "")   # Lambda logs to stdout/CloudWatch

    # -- 2. Guard: S3_BUCKET must be set ------------------------------------
    if not S3_BUCKET:
        msg = "S3_BUCKET environment variable is not set -- aborting."
        logger.error(msg)
        return {"statusCode": 500, "body": json.dumps({"error": msg})}

    # -- 3. Download OAuth credentials from S3 ------------------------------
    s3 = boto3.client("s3")
    try:
        _download(s3, S3_BUCKET, S3_CREDENTIALS_KEY, LOCAL_CREDENTIALS)
        _download(s3, S3_BUCKET, S3_TOKEN_KEY, LOCAL_TOKEN)
    except Exception as exc:
        logger.error("S3 download failed: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"S3 download failed: {exc}"}),
        }

    # -- 4. Run the email automation ----------------------------------------
    exit_code = 1
    try:
        # Late import: env vars must already be set before Config() is built
        from main import main as run_main

        exit_code = run_main(argv=[])  # argv=[] -> use env vars, no CLI flags
        logger.info("run completed, exit_code=%d", exit_code)

    except Exception as exc:
        logger.error("Unhandled error in run_main: %s\n%s", exc, traceback.format_exc())
        # Best-effort token upload even on crash (OAuth may have refreshed)
        try:
            _upload(s3, LOCAL_TOKEN, S3_BUCKET, S3_TOKEN_KEY)
        except Exception:
            pass
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }),
        }

    # -- 5. Upload refreshed token.json back to S3 --------------------------
    # Google OAuth silently rotates the access token on each use; the new
    # token is written to LOCAL_TOKEN by google-auth. We persist it so the
    # next hourly invocation does not have to re-authorise.
    try:
        _upload(s3, LOCAL_TOKEN, S3_BUCKET, S3_TOKEN_KEY)
    except Exception as exc:
        logger.warning("token.json upload failed (non-fatal): %s", exc)

    # -- 6. Return ----------------------------------------------------------
    success = exit_code == 0
    return {
        "statusCode": 200 if success else 500,
        "body": json.dumps({
            "message": "OK" if success else f"exited with code {exit_code}",
            "exit_code": exit_code,
        }),
    }
