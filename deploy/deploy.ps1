<#
  deploy.ps1 - Deploy flolabs-email-automation to AWS Lambda (us-east-1)
  Usage: .\deploy\deploy.ps1
#>

$ErrorActionPreference = "Continue"

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

$PROJECT_ROOT    = Split-Path -Parent $PSScriptRoot
$DEPLOY_DIR      = "$PROJECT_ROOT\deploy"
$PACKAGE_DIR     = "$PROJECT_ROOT\.lambda-package"
$ZIP_FILE        = "$DEPLOY_DIR\lambda-deploy.zip"

Write-Host ""
Write-Host "=== FloLabs Email Automation - Lambda Deploy ===" -ForegroundColor Cyan
Write-Host "Region:   $REGION"
Write-Host "Function: $FUNCTION_NAME"
Write-Host "Bucket:   $S3_BUCKET"
Write-Host ""

# ── Step 1: Create S3 bucket ───────────────────────────────────────────────
Write-Host "[1/8] Creating S3 bucket..." -ForegroundColor Yellow
$headResult = aws s3api head-bucket --bucket $S3_BUCKET --region $REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    aws s3api create-bucket --bucket $S3_BUCKET --region $REGION | Out-Null
    Write-Host "  Created bucket: $S3_BUCKET" -ForegroundColor Green
} else {
    Write-Host "  Bucket already exists." -ForegroundColor Green
}

# ── Step 2: Upload OAuth files ─────────────────────────────────────────────
Write-Host "[2/8] Uploading OAuth files to S3..." -ForegroundColor Yellow
aws s3 cp "$PROJECT_ROOT\token.json" "s3://$S3_BUCKET/${S3_PREFIX}token.json" --region $REGION | Out-Null
aws s3 cp "$PROJECT_ROOT\credentials.json" "s3://$S3_BUCKET/${S3_PREFIX}credentials.json" --region $REGION | Out-Null
Write-Host "  Uploaded token.json + credentials.json" -ForegroundColor Green

# ── Step 3: Add S3 policy to Lambda role ───────────────────────────────────
Write-Host "[3/8] Ensuring S3 access policy on Lambda role..." -ForegroundColor Yellow

$s3PolicyObj = @{
    Version = "2012-10-17"
    Statement = @(
        @{
            Effect = "Allow"
            Action = @("s3:GetObject", "s3:PutObject")
            Resource = "arn:aws:s3:::$S3_BUCKET/*"
        },
        @{
            Effect = "Allow"
            Action = "s3:ListBucket"
            Resource = "arn:aws:s3:::$S3_BUCKET"
        }
    )
}

$policyFile = Join-Path $DEPLOY_DIR "s3-policy.json"
$s3PolicyObj | ConvertTo-Json -Depth 5 | Out-File -FilePath $policyFile -Encoding ascii -NoNewline

# Convert backslashes to forward slashes for AWS CLI file:// protocol
$policyFilePath = $policyFile.Replace('\', '/')
aws iam put-role-policy --role-name flolabs-lambda-role --policy-name flolabs-email-automation-s3 --policy-document "file://$policyFilePath" --region $REGION 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) {
    Write-Host "  S3 policy attached." -ForegroundColor Green
} else {
    Write-Host "  Warning: could not attach S3 policy" -ForegroundColor DarkYellow
}

# ── Step 4: Install dependencies ───────────────────────────────────────────
Write-Host "[4/8] Installing Python dependencies..." -ForegroundColor Yellow

if (Test-Path $PACKAGE_DIR) { Remove-Item $PACKAGE_DIR -Recurse -Force }
New-Item -ItemType Directory -Path $PACKAGE_DIR -Force | Out-Null

pip install --platform manylinux2014_x86_64 --target $PACKAGE_DIR --implementation cp --python-version 3.13 --only-binary=:all: python-dotenv openai anthropic requests google-api-python-client google-auth-oauthlib google-auth-httplib2 2>&1 | Out-Null

if ($LASTEXITCODE -eq 0) {
    Write-Host "  Dependencies installed." -ForegroundColor Green
} else {
    Write-Host "  ERROR: pip install failed!" -ForegroundColor Red
    exit 1
}

# ── Step 5: Create zip package ─────────────────────────────────────────────
Write-Host "[5/8] Creating deployment zip..." -ForegroundColor Yellow

if (Test-Path $ZIP_FILE) { Remove-Item $ZIP_FILE -Force }

# Add dependencies first
Push-Location $PACKAGE_DIR
Compress-Archive -Path * -DestinationPath $ZIP_FILE -Force
Pop-Location

# Add source files on top
$filesToAdd = @("handler.py", "main.py", ".env")
foreach ($f in $filesToAdd) {
    $fullPath = Join-Path $PROJECT_ROOT $f
    if (Test-Path $fullPath) {
        Compress-Archive -Path $fullPath -Update -DestinationPath $ZIP_FILE
    }
}

# Add src/ directory
Push-Location $PROJECT_ROOT
Compress-Archive -Path "src" -Update -DestinationPath $ZIP_FILE
Pop-Location

$zipSizeMB = [math]::Round((Get-Item $ZIP_FILE).Length / 1MB, 1)
Write-Host "  Created: lambda-deploy.zip ($zipSizeMB MB)" -ForegroundColor Green

$deployViaS3 = $false
if ($zipSizeMB -gt 50) {
    Write-Host "  Zip > 50 MB - deploying via S3." -ForegroundColor DarkYellow
    $deployViaS3 = $true
    aws s3 cp $ZIP_FILE "s3://$S3_BUCKET/${S3_PREFIX}lambda-deploy.zip" --region $REGION | Out-Null
}

# ── Step 6: Create/update Lambda function ──────────────────────────────────
Write-Host "[6/8] Deploying Lambda function..." -ForegroundColor Yellow

# Build env vars from .env + overrides
$envVars = @{
    S3_BUCKET              = $S3_BUCKET
    S3_PREFIX              = $S3_PREFIX
    REPLY_MODE             = "auto"
    GMAIL_TOKEN_FILE       = "/tmp/token.json"
    GMAIL_CREDENTIALS_FILE = "/tmp/credentials.json"
}

$overrideKeys = @("REPLY_MODE", "GMAIL_TOKEN_FILE", "GMAIL_CREDENTIALS_FILE", "S3_BUCKET", "S3_PREFIX")
Get-Content (Join-Path $PROJECT_ROOT ".env") | ForEach-Object {
    if ($_ -match '^\s*([A-Z_][A-Z_0-9]*)\s*=\s*(.+?)\s*$' -and $_ -notmatch '^\s*#') {
        $k = $Matches[1]
        $v = $Matches[2]
        if ($k -notin $overrideKeys) {
            $envVars[$k] = $v
        }
    }
}

$envFile = Join-Path $DEPLOY_DIR "env-vars.json"
@{ Variables = $envVars } | ConvertTo-Json -Compress -Depth 3 | Out-File -FilePath $envFile -Encoding ascii -NoNewline

# Check if function exists
$null = aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>&1
$functionExists = ($LASTEXITCODE -eq 0)

if ($functionExists) {
    Write-Host "  Updating existing function..." -ForegroundColor DarkYellow
    $envFilePath = $envFile.Replace('\', '/')
    if ($deployViaS3) {
        aws lambda update-function-code --function-name $FUNCTION_NAME --s3-bucket $S3_BUCKET --s3-key "${S3_PREFIX}lambda-deploy.zip" --region $REGION | Out-Null
    } else {
        $zipFilePath = $ZIP_FILE.Replace('\', '/')
        aws lambda update-function-code --function-name $FUNCTION_NAME --zip-file "fileb://$zipFilePath" --region $REGION | Out-Null
    }

    # Wait for code update to finish
    Write-Host "  Waiting for function update..." -ForegroundColor DarkYellow
    aws lambda wait function-updated --function-name $FUNCTION_NAME --region $REGION 2>&1 | Out-Null

    aws lambda update-function-configuration --function-name $FUNCTION_NAME --handler $HANDLER --runtime $RUNTIME --timeout $TIMEOUT --memory-size $MEMORY_SIZE --environment "file://$envFilePath" --region $REGION | Out-Null
} else {
    Write-Host "  Creating new function..." -ForegroundColor DarkYellow
    $envFilePath = $envFile.Replace('\', '/')
    if ($deployViaS3) {
        aws lambda create-function --function-name $FUNCTION_NAME --runtime $RUNTIME --role $ROLE_ARN --handler $HANDLER --timeout $TIMEOUT --memory-size $MEMORY_SIZE --code "S3Bucket=$S3_BUCKET,S3Key=${S3_PREFIX}lambda-deploy.zip" --environment "file://$envFilePath" --region $REGION | Out-Null
    } else {
        $zipFilePath = $ZIP_FILE.Replace('\', '/')
        aws lambda create-function --function-name $FUNCTION_NAME --runtime $RUNTIME --role $ROLE_ARN --handler $HANDLER --timeout $TIMEOUT --memory-size $MEMORY_SIZE --zip-file "fileb://$zipFilePath" --environment "file://$envFilePath" --region $REGION | Out-Null
    }
}

if ($LASTEXITCODE -eq 0) {
    Write-Host "  Lambda function deployed." -ForegroundColor Green
} else {
    Write-Host "  ERROR: Lambda deploy failed!" -ForegroundColor Red
    exit 1
}

# ── Step 7: EventBridge hourly schedule ────────────────────────────────────
Write-Host "[7/8] Setting up EventBridge hourly trigger..." -ForegroundColor Yellow

aws events put-rule --name $SCHEDULE_NAME --schedule-expression "$SCHEDULE_EXPR" --state ENABLED --description "Trigger flolabs-email-automation every hour" --region $REGION | Out-Null

# Add permission for EventBridge to invoke Lambda
aws lambda add-permission --function-name $FUNCTION_NAME --statement-id "${SCHEDULE_NAME}-invoke" --action "lambda:InvokeFunction" --principal "events.amazonaws.com" --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${SCHEDULE_NAME}" --region $REGION 2>&1 | Out-Null

# Set Lambda as target
$targetJson = '[{\"Id\":\"1\",\"Arn\":\"arn:aws:lambda:' + $REGION + ':' + $ACCOUNT_ID + ':function:' + $FUNCTION_NAME + '\"}]'
aws events put-targets --rule $SCHEDULE_NAME --targets $targetJson --region $REGION | Out-Null

Write-Host "  EventBridge rule created: $SCHEDULE_NAME" -ForegroundColor Green

# ── Step 8: Verify ─────────────────────────────────────────────────────────
Write-Host "[8/8] Verifying deployment..." -ForegroundColor Yellow

$funcInfo = aws lambda get-function-configuration --function-name $FUNCTION_NAME --region $REGION | ConvertFrom-Json

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Green
Write-Host "  Function:  $($funcInfo.FunctionName)"
Write-Host "  Runtime:   $($funcInfo.Runtime)"
Write-Host "  Handler:   $($funcInfo.Handler)"
Write-Host "  Memory:    $($funcInfo.MemorySize) MB"
Write-Host "  Timeout:   $($funcInfo.Timeout)s"
Write-Host "  Last mod:  $($funcInfo.LastModified)"
Write-Host "  Schedule:  Every hour (EventBridge: $SCHEDULE_NAME)"
Write-Host ""
Write-Host "Test manually:" -ForegroundColor Cyan
Write-Host "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --log-type Tail out.json"
Write-Host ""
Write-Host "Check logs:" -ForegroundColor Cyan
Write-Host "  aws logs tail /aws/lambda/$FUNCTION_NAME --region $REGION --since 1h"
Write-Host ""

# Cleanup temp files
Remove-Item $policyFile -ErrorAction SilentlyContinue
Remove-Item $envFile -ErrorAction SilentlyContinue
