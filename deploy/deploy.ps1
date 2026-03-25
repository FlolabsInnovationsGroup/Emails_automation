<# 
  deploy.ps1 — Deploy flolabs-email-automation to AWS Lambda (us-east-1)
  
  Usage: .\deploy\deploy.ps1
  
  What it does:
  1. Creates S3 bucket for OAuth files (if needed)
  2. Uploads token.json + credentials.json to S3
  3. Installs Python deps into a package/ dir for Lambda
  4. Zips everything into a deployment package
  5. Creates/updates the Lambda function
  6. Sets all environment variables (from .env)
  7. Adds S3 read/write policy to the Lambda role
  8. Creates EventBridge hourly schedule
  
  Prerequisites:
  - AWS CLI configured (aws sts get-caller-identity should work)
  - Python 3.13 installed
  - pip available
#>

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

# ── Config ──────────────────────────────────────────────────────────────────
$REGION          = "us-east-1"
$FUNCTION_NAME   = "flolabs-email-automation"
$ROLE_ARN        = "arn:aws:iam::975049954782:role/flolabs-lambda-role"
$S3_BUCKET       = "flolabs-email-automation-975049954782"
$S3_PREFIX       = "email-automation/"
$RUNTIME         = "python3.13"
$HANDLER         = "handler.handler"
$TIMEOUT         = 300
$MEMORY_SIZE     = 512
$SCHEDULE_NAME   = "flolabs-email-automation-hourly"
$SCHEDULE_EXPR   = "rate(1 hour)"
$ACCOUNT_ID      = "975049954782"

$PROJECT_ROOT    = Split-Path -Parent $PSScriptRoot  # one level up from deploy/
$DEPLOY_DIR      = "$PROJECT_ROOT\deploy"
$PACKAGE_DIR     = "$PROJECT_ROOT\.lambda-package"
$ZIP_FILE        = "$DEPLOY_DIR\lambda-deploy.zip"

Write-Host "`n=== FloLabs Email Automation — Lambda Deploy ===" -ForegroundColor Cyan
Write-Host "Region:   $REGION"
Write-Host "Function: $FUNCTION_NAME"
Write-Host "Bucket:   $S3_BUCKET"
Write-Host ""

# ── Step 1: Create S3 bucket (if needed) ────────────────────────────────────
Write-Host "[1/8] Creating S3 bucket..." -ForegroundColor Yellow
$bucketExists = $false
try {
    aws s3api head-bucket --bucket $S3_BUCKET --region $REGION 2>$null
    $bucketExists = $true
    Write-Host "  Bucket already exists." -ForegroundColor Green
} catch {}

if (-not $bucketExists) {
    # us-east-1 doesn't use LocationConstraint
    aws s3api create-bucket --bucket $S3_BUCKET --region $REGION
    Write-Host "  Created bucket: $S3_BUCKET" -ForegroundColor Green
}

# ── Step 2: Upload OAuth files to S3 ────────────────────────────────────────
Write-Host "[2/8] Uploading OAuth files to S3..." -ForegroundColor Yellow
aws s3 cp "$PROJECT_ROOT\token.json" "s3://$S3_BUCKET/${S3_PREFIX}token.json" --region $REGION
aws s3 cp "$PROJECT_ROOT\credentials.json" "s3://$S3_BUCKET/${S3_PREFIX}credentials.json" --region $REGION
Write-Host "  Uploaded token.json + credentials.json" -ForegroundColor Green

# ── Step 3: Add S3 policy to Lambda role ────────────────────────────────────
Write-Host "[3/8] Ensuring S3 access policy on Lambda role..." -ForegroundColor Yellow

$s3Policy = @"
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetObject",
                "s3:PutObject"
            ],
            "Resource": "arn:aws:s3:::$S3_BUCKET/*"
        },
        {
            "Effect": "Allow",
            "Action": "s3:ListBucket",
            "Resource": "arn:aws:s3:::$S3_BUCKET"
        }
    ]
}
"@

$policyFile = "$DEPLOY_DIR\s3-policy.json"
$s3Policy | Out-File -FilePath $policyFile -Encoding utf8

try {
    aws iam put-role-policy `
        --role-name flolabs-lambda-role `
        --policy-name flolabs-email-automation-s3 `
        --policy-document "file://$policyFile" `
        --region $REGION
    Write-Host "  S3 policy attached." -ForegroundColor Green
} catch {
    Write-Host "  Warning: could not attach S3 policy — $($_.Exception.Message)" -ForegroundColor DarkYellow
}

# ── Step 4: Install dependencies ────────────────────────────────────────────
Write-Host "[4/8] Installing Python dependencies..." -ForegroundColor Yellow

if (Test-Path $PACKAGE_DIR) { Remove-Item $PACKAGE_DIR -Recurse -Force }
New-Item -ItemType Directory -Path $PACKAGE_DIR | Out-Null

# Install deps for Linux Lambda (not Windows)
pip install `
    --platform manylinux2014_x86_64 `
    --target "$PACKAGE_DIR" `
    --implementation cp `
    --python-version 3.13 `
    --only-binary=:all: `
    python-dotenv openai anthropic requests `
    google-api-python-client google-auth-oauthlib google-auth-httplib2 `
    2>&1 | Out-Null

Write-Host "  Dependencies installed to .lambda-package/" -ForegroundColor Green

# ── Step 5: Create zip package ──────────────────────────────────────────────
Write-Host "[5/8] Creating deployment zip..." -ForegroundColor Yellow

if (Test-Path $ZIP_FILE) { Remove-Item $ZIP_FILE -Force }

# Add dependencies
Push-Location $PACKAGE_DIR
Compress-Archive -Path * -DestinationPath $ZIP_FILE
Pop-Location

# Add source files on top
$sourceFiles = @(
    "$PROJECT_ROOT\handler.py",
    "$PROJECT_ROOT\main.py",
    "$PROJECT_ROOT\.env"
)
foreach ($f in $sourceFiles) {
    Compress-Archive -Path $f -Update -DestinationPath $ZIP_FILE
}

# Add src/ directory
Push-Location $PROJECT_ROOT
Compress-Archive -Path "src" -Update -DestinationPath $ZIP_FILE
Pop-Location

$zipSize = (Get-Item $ZIP_FILE).Length / 1MB
Write-Host "  Created: lambda-deploy.zip (${zipSize:N1} MB)" -ForegroundColor Green

if ($zipSize -gt 50) {
    Write-Host "  WARNING: Zip is > 50 MB. Will deploy via S3." -ForegroundColor DarkYellow
    $deployViaS3 = $true
    aws s3 cp $ZIP_FILE "s3://$S3_BUCKET/${S3_PREFIX}lambda-deploy.zip" --region $REGION
} else {
    $deployViaS3 = $false
}

# ── Step 6: Create or update Lambda function ────────────────────────────────
Write-Host "[6/8] Deploying Lambda function..." -ForegroundColor Yellow

# Build environment variables JSON from .env
$envVars = @{
    S3_BUCKET              = $S3_BUCKET
    S3_PREFIX              = $S3_PREFIX
    REPLY_MODE             = "auto"
    GMAIL_TOKEN_FILE       = "/tmp/token.json"
    GMAIL_CREDENTIALS_FILE = "/tmp/credentials.json"
}

# Read .env and add all variables (except overridden ones)
$overrides = @("REPLY_MODE", "GMAIL_TOKEN_FILE", "GMAIL_CREDENTIALS_FILE", "S3_BUCKET", "S3_PREFIX")
Get-Content "$PROJECT_ROOT\.env" | ForEach-Object {
    if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.+?)\s*$' -and $_ -notmatch '^\s*#') {
        $key = $Matches[1]
        $val = $Matches[2]
        if ($key -notin $overrides) {
            $envVars[$key] = $val
        }
    }
}

$envJson = @{ Variables = $envVars } | ConvertTo-Json -Compress
# Write to file to avoid shell escaping issues
$envFile = "$DEPLOY_DIR\env-vars.json"
$envJson | Out-File -FilePath $envFile -Encoding utf8

$functionExists = $false
try {
    aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>$null | Out-Null
    $functionExists = $true
} catch {}

if ($functionExists) {
    Write-Host "  Updating existing function..." -ForegroundColor DarkYellow
    if ($deployViaS3) {
        aws lambda update-function-code `
            --function-name $FUNCTION_NAME `
            --s3-bucket $S3_BUCKET `
            --s3-key "${S3_PREFIX}lambda-deploy.zip" `
            --region $REGION | Out-Null
    } else {
        aws lambda update-function-code `
            --function-name $FUNCTION_NAME `
            --zip-file "fileb://$ZIP_FILE" `
            --region $REGION | Out-Null
    }
    
    # Wait for update to complete before changing config
    Write-Host "  Waiting for function update..." -ForegroundColor DarkYellow
    aws lambda wait function-updated --function-name $FUNCTION_NAME --region $REGION

    aws lambda update-function-configuration `
        --function-name $FUNCTION_NAME `
        --handler $HANDLER `
        --runtime $RUNTIME `
        --timeout $TIMEOUT `
        --memory-size $MEMORY_SIZE `
        --environment "file://$envFile" `
        --region $REGION | Out-Null
} else {
    Write-Host "  Creating new function..." -ForegroundColor DarkYellow
    if ($deployViaS3) {
        aws lambda create-function `
            --function-name $FUNCTION_NAME `
            --runtime $RUNTIME `
            --role $ROLE_ARN `
            --handler $HANDLER `
            --timeout $TIMEOUT `
            --memory-size $MEMORY_SIZE `
            --code "S3Bucket=$S3_BUCKET,S3Key=${S3_PREFIX}lambda-deploy.zip" `
            --environment "file://$envFile" `
            --region $REGION | Out-Null
    } else {
        aws lambda create-function `
            --function-name $FUNCTION_NAME `
            --runtime $RUNTIME `
            --role $ROLE_ARN `
            --handler $HANDLER `
            --timeout $TIMEOUT `
            --memory-size $MEMORY_SIZE `
            --zip-file "fileb://$ZIP_FILE" `
            --environment "file://$envFile" `
            --region $REGION | Out-Null
    }
}
Write-Host "  Lambda function deployed." -ForegroundColor Green

# ── Step 7: Create EventBridge hourly schedule ──────────────────────────────
Write-Host "[7/8] Setting up EventBridge hourly trigger..." -ForegroundColor Yellow

# Create or update the rule
aws events put-rule `
    --name $SCHEDULE_NAME `
    --schedule-expression "$SCHEDULE_EXPR" `
    --state ENABLED `
    --description "Trigger flolabs-email-automation every hour" `
    --region $REGION | Out-Null

# Add permission for EventBridge to invoke Lambda (idempotent via statement-id)
try {
    aws lambda add-permission `
        --function-name $FUNCTION_NAME `
        --statement-id "${SCHEDULE_NAME}-invoke" `
        --action "lambda:InvokeFunction" `
        --principal "events.amazonaws.com" `
        --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${SCHEDULE_NAME}" `
        --region $REGION 2>$null | Out-Null
} catch {
    # Permission already exists — that's fine
}

# Set Lambda as target
$targetJson = @"
[{"Id":"1","Arn":"arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"}]
"@

aws events put-targets `
    --rule $SCHEDULE_NAME `
    --targets $targetJson `
    --region $REGION | Out-Null

Write-Host "  EventBridge rule created: $SCHEDULE_NAME" -ForegroundColor Green

# ── Step 8: Verify ──────────────────────────────────────────────────────────
Write-Host "[8/8] Verifying deployment..." -ForegroundColor Yellow

$funcInfo = aws lambda get-function-configuration `
    --function-name $FUNCTION_NAME `
    --region $REGION | ConvertFrom-Json

Write-Host "`n=== Deployment Complete ===" -ForegroundColor Green
Write-Host "  Function:  $($funcInfo.FunctionName)"
Write-Host "  Runtime:   $($funcInfo.Runtime)"
Write-Host "  Handler:   $($funcInfo.Handler)"
Write-Host "  Memory:    $($funcInfo.MemorySize) MB"
Write-Host "  Timeout:   $($funcInfo.Timeout)s"
Write-Host "  Last mod:  $($funcInfo.LastModified)"
Write-Host "  Schedule:  Every hour (EventBridge: $SCHEDULE_NAME)"
Write-Host ""
Write-Host "To test manually:" -ForegroundColor Cyan
Write-Host "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --log-type Tail out.json"
Write-Host ""
Write-Host "To check logs:" -ForegroundColor Cyan
Write-Host "  aws logs tail /aws/lambda/$FUNCTION_NAME --region $REGION --since 1h"
Write-Host ""

# ── Cleanup temp files ──────────────────────────────────────────────────────
Remove-Item $policyFile -ErrorAction SilentlyContinue
Remove-Item $envFile -ErrorAction SilentlyContinue
