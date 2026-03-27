"""
AWS Lambda entry point for FloLabs Email Automation.

Triggered hourly by EventBridge. This handler:
1. Downloads token.json and credentials.json from S3 to /tmp/
2. Sets environment variables so config.py picks up the right paths + auto mode
3. Runs the email automation (main.main with no CLI args)
4. Uploads the (possibly OAuth-refreshed) token.json back to S3
5. Returns a JSON response with success/failure info
"""

import json
import logging
import os
import traceback

import boto3

logger = logging.getLogger("email_automation.lambda")
logger.setLevel(logging.INFO)

# S3 config — bucket name set via Lambda environment variable
S3_BUCKET = os.environ.get("S3_BUCKET", "")
S3_PREFIX = os.environ.get("S3_PREFIX", "email-automation/")  # key prefix in the bucket
S3_TOKEN_KEY = S3_PREFIX + "token.json"
S3_CREDENTIALS_KEY = S3_PREFIX + "credentials.json"

# Local paths in Lambda's writable /tmp directory
LOCAL_TOKEN = "/tmp/token.json"
LOCAL_CREDENTIALS = "/tmp/credentials.json"


def _download_from_s3(s3_client, bucket: str, key: str, local_path: str) -> None:
    """Download a file from S3 to a local path."""
    logger.info("Downloading s3://%s/%s -> %s", bucket, key, local_path)
    s3_client.download_file(bucket, key, local_path)


def _upload_to_s3(s3_client, local_path: str, bucket: str, key: str) -> None:
    """Upload a local file to S3."""
    if os.path.exists(local_path):
        logger.info("Uploading %s -> s3://%s/%s", local_path, bucket, key)
        s3_client.upload_file(local_path, bucket, key)
    else:
        logger.warning("File %s does not exist, skipping upload.", local_path)


def handler(event, context):
    """Lambda handler. Triggered by EventBridge (hourly) or manual invocation."""
    logger.info("Lambda invoked. Event: %s", json.dumps(event, default=str))

    # ── 1. Override env vars for Lambda context ─────────────────────────────
    # These ensure config.py reads the /tmp/ paths and sends immediately.
    os.environ["GMAIL_TOKEN_FILE"] = LOCAL_TOKEN
    os.environ["GMAIL_CREDENTIALS_FILE"] = LOCAL_CREDENTIALS
    os.environ["REPLY_MODE"] = "auto"

    # ── 2. Download OAuth files from S3 ─────────────────────────────────────
    if not S3_BUCKET:
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "S3_BUCKET environment variable not set"})
        }

    s3 = boto3.client("s3")
    try:
        _download_from_s3(s3, S3_BUCKET, S3_CREDENTIALS_KEY, LOCAL_CREDENTIALS)
        _download_from_s3(s3, S3_BUCKET, S3_TOKEN_KEY, LOCAL_TOKEN)
    except Exception as exc:
        logger.error("Failed to download files from S3: %s", exc)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": f"S3 download failed: {exc}"})
        }

    # ── 3. Run the email automation ─────────────────────────────────────────
    from src.delivery_monitor import report_failure, report_success, get_throttled_max_emails

    # Apply batch-size throttle: if previous runs failed, reduce MAX_EMAILS
    # while keeping the hourly schedule unchanged.
    throttled_max = get_throttled_max_emails()
    current_max = int(os.environ.get("MAX_EMAILS", "10"))
    if throttled_max < current_max:
        logger.info(
            "Delivery throttle active: MAX_EMAILS reduced from %d to %d.",
            current_max, throttled_max,
        )
        os.environ["MAX_EMAILS"] = str(throttled_max)

    exit_code = 1
    try:
        # Import here so env vars are already set when Config() reads them
        from main import main as run_main

        exit_code = run_main(argv=[])  # No CLI args — uses env vars
        logger.info("Email automation completed with exit code: %d", exit_code)

    except Exception as exc:
        error_msg = str(exc)
        logger.error("Email automation failed: %s\n%s", exc, traceback.format_exc())

        # Report delivery failure — throttles schedule + alerts Discord
        try:
            report_failure(error_msg)
        except Exception as monitor_exc:
            logger.warning("Delivery monitor failed: %s", monitor_exc)

        # Still try to upload token in case OAuth refreshed before the crash
        try:
            _upload_to_s3(s3, LOCAL_TOKEN, S3_BUCKET, S3_TOKEN_KEY)
        except Exception:
            pass
        return {
            "statusCode": 500,
            "body": json.dumps({
                "error": error_msg,
                "traceback": traceback.format_exc()
            })
        }

    # ── 4. Upload (possibly refreshed) token.json back to S3 ───────────────
    try:
        _upload_to_s3(s3, LOCAL_TOKEN, S3_BUCKET, S3_TOKEN_KEY)
    except Exception as exc:
        logger.warning("Failed to upload token.json to S3: %s (non-fatal)", exc)

    # ── 5. Report outcome and return result ─────────────────────────────────
    if exit_code == 0:
        try:
            report_success()
        except Exception as monitor_exc:
            logger.warning("Delivery monitor (success) failed: %s", monitor_exc)
    else:
        # Non-zero exit code without an exception — still a failure
        try:
            report_failure(f"Email automation exited with code {exit_code}")
        except Exception as monitor_exc:
            logger.warning("Delivery monitor failed: %s", monitor_exc)

    success = exit_code == 0
    return {
        "statusCode": 200 if success else 500,
        "body": json.dumps({
            "message": "Email automation completed successfully" if success
                       else f"Email automation finished with exit code {exit_code}",
            "exit_code": exit_code
        })
    }
