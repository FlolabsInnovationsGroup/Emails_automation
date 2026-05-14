<#
  deploy.ps1 -- Deploy flolabs-email-automation to AWS Lambda (us-east-1)

  Usage:
    cd <repo-root>
    .\deploy\deploy.ps1

  Prerequisites:
    - AWS CLI configured (aws configure) with skynet-automation credentials
    - Python + pip on PATH
    - token.json and credentials.json in repo root (Gmail OAuth files)

  What this does:
    1.  Creates a dedicated S3 bucket for the OAuth files + zip
    2.  Uploads token.json + credentials.json to S3
    3.  Attaches an inline S3 policy to the existing flolabs-lambda-role
    4.  Installs Python deps compiled for Linux (manylinux) into .lambda-package/
    5.  Copies source files into .lambda-package/ so everything is at zip root
    6.  Zips .lambda-package/ -> deploy/lambda-deploy.zip
    7.  Creates or updates the Lambda function (uploads via S3 if zip > 50 MB)
    8.  Creates an EventBridge rule to trigger the function every hour
    9.  Prints a summary + test/log commands
#>

$ErrorActionPreference = "Stop"

# ============================================================================
# Config
# ============================================================================
$REGION        = "us-east-1"
$FUNCTION_NAME = "flolabs-email-automation"
$ROLE_ARN      = "arn:aws:iam::975049954782:role/flolabs-lambda-role"
$ACCOUNT_ID    = "975049954782"

# Dedicated bucket for OAuth files and the deployment zip
$S3_BUCKET     = "flolabs-email-automation-975049954782"
# Key prefix inside the bucket for runtime files (token.json, credentials.json)
$S3_PREFIX     = "email-automation/"

$RUNTIME       = "python3.13"
$HANDLER       = "handler.handler"
$TIMEOUT       = 300   # seconds (5 min -- LLM calls can be slow)
$MEMORY_SIZE   = 512   # MB

$SCHEDULE_NAME = "flolabs-email-automation-hourly"
$SCHEDULE_EXPR = "rate(1 hour)"

# Derived paths
$PROJECT_ROOT  = Split-Path -Parent $PSScriptRoot
$DEPLOY_DIR    = "$PROJECT_ROOT\deploy"
$PACKAGE_DIR   = "$PROJECT_ROOT\.lambda-package"
$ZIP_FILE      = "$DEPLOY_DIR\lambda-deploy.zip"

Write-Host ""
Write-Host "=== FloLabs Email Automation -- Lambda Deploy ===" -ForegroundColor Cyan
Write-Host "  Region   : $REGION"
Write-Host "  Function : $FUNCTION_NAME"
Write-Host "  Bucket   : $S3_BUCKET"
Write-Host "  Role     : $ROLE_ARN"
Write-Host ""

# ============================================================================
# Step 1: Create S3 bucket (idempotent)
# ============================================================================
Write-Host "[1/8] S3 bucket..." -ForegroundColor Yellow

$null = aws s3api head-bucket --bucket $S3_BUCKET --region $REGION 2>&1
if ($LASTEXITCODE -ne 0) {
    # us-east-1 does NOT accept a --create-bucket-configuration flag
    aws s3api create-bucket --bucket $S3_BUCKET --region $REGION | Out-Null
    Write-Host "  Created: $S3_BUCKET" -ForegroundColor Green
} else {
    Write-Host "  Already exists: $S3_BUCKET" -ForegroundColor Green
}

# ============================================================================
# Step 2: Upload OAuth files to S3
# ============================================================================
Write-Host "[2/8] Uploading OAuth files to S3..." -ForegroundColor Yellow

foreach ($oauthFile in @("token.json", "credentials.json")) {
    $localPath = Join-Path $PROJECT_ROOT $oauthFile
    if (-not (Test-Path $localPath)) {
        Write-Host "  ERROR: $oauthFile not found at $localPath" -ForegroundColor Red
        exit 1
    }
    aws s3 cp $localPath "s3://$S3_BUCKET/${S3_PREFIX}$oauthFile" --region $REGION | Out-Null
    Write-Host "  Uploaded $oauthFile" -ForegroundColor Green
}

# ============================================================================
# Step 3: Attach inline S3 policy to the Lambda role
# ============================================================================
Write-Host "[3/8] Attaching S3 policy to flolabs-lambda-role..." -ForegroundColor Yellow

$s3Policy = @{
    Version   = "2012-10-17"
    Statement = @(
        @{
            Sid      = "AllowObjectReadWrite"
            Effect   = "Allow"
            Action   = @("s3:GetObject", "s3:PutObject")
            Resource = "arn:aws:s3:::$S3_BUCKET/*"
        },
        @{
            Sid      = "AllowBucketList"
            Effect   = "Allow"
            Action   = "s3:ListBucket"
            Resource = "arn:aws:s3:::$S3_BUCKET"
        }
    )
} | ConvertTo-Json -Depth 5

$policyFile = Join-Path $DEPLOY_DIR "s3-policy.json"
[System.IO.File]::WriteAllText($policyFile, $s3Policy, [System.Text.Encoding]::ASCII)
$policyFilePath = $policyFile.Replace('\', '/')

aws iam put-role-policy `
    --role-name flolabs-lambda-role `
    --policy-name flolabs-email-automation-s3 `
    --policy-document "file://$policyFilePath" | Out-Null
Write-Host "  S3 policy attached." -ForegroundColor Green

# ============================================================================
# Step 4: Install Python dependencies compiled for Linux
# ============================================================================
Write-Host "[4/8] Installing Python dependencies (linux/x86_64)..." -ForegroundColor Yellow

if (Test-Path $PACKAGE_DIR) { Remove-Item $PACKAGE_DIR -Recurse -Force }
New-Item -ItemType Directory -Path $PACKAGE_DIR -Force | Out-Null

# --platform manylinux2014_x86_64 ensures Linux-compatible binary wheels
# --only-binary=:all: avoids building from source (which would produce Windows binaries)
# All packages in requirements.txt have manylinux wheels available
$pipArgs = @(
    "install",
    "--platform", "manylinux2014_x86_64",
    "--target", $PACKAGE_DIR,
    "--implementation", "cp",
    "--python-version", "3.13",
    "--only-binary=:all:",
    "--upgrade",
    "python-dotenv",
    "openai",
    "anthropic",
    "requests",
    "google-api-python-client",
    "google-auth-oauthlib",
    "google-auth-httplib2"
)

& pip @pipArgs
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: pip install failed!" -ForegroundColor Red
    exit 1
}
Write-Host "  Dependencies installed." -ForegroundColor Green

# ============================================================================
# Step 5: Assemble zip package
# All files must be at the ROOT of the zip so Lambda can find handler.py.
# Strategy: copy everything into .lambda-package/, then zip from inside it.
# ============================================================================
Write-Host "[5/8] Building deployment zip..." -ForegroundColor Yellow

# Copy source files into the package dir (flat -- at root level)
Copy-Item "$PROJECT_ROOT\handler.py" "$PACKAGE_DIR\handler.py" -Force
Copy-Item "$PROJECT_ROOT\main.py"    "$PACKAGE_DIR\main.py"    -Force
Copy-Item "$PROJECT_ROOT\.env"       "$PACKAGE_DIR\.env"       -Force

# Copy the src/ package (preserves the src/ subdirectory, which is correct)
if (Test-Path "$PACKAGE_DIR\src") { Remove-Item "$PACKAGE_DIR\src" -Recurse -Force }
Copy-Item "$PROJECT_ROOT\src" "$PACKAGE_DIR\src" -Recurse -Force

# Create the zip from inside the package dir so all paths are relative
if (Test-Path $ZIP_FILE) { Remove-Item $ZIP_FILE -Force }
Push-Location $PACKAGE_DIR
Compress-Archive -Path * -DestinationPath $ZIP_FILE -Force
Pop-Location

$zipSizeMB = [math]::Round((Get-Item $ZIP_FILE).Length / 1MB, 1)
Write-Host "  Created: lambda-deploy.zip ($zipSizeMB MB)" -ForegroundColor Green

# Lambda direct upload limit is 50 MB; use S3 for larger zips
$deployViaS3 = $zipSizeMB -gt 50
if ($deployViaS3) {
    Write-Host "  Zip > 50 MB -- will deploy via S3." -ForegroundColor DarkYellow
    aws s3 cp $ZIP_FILE "s3://$S3_BUCKET/${S3_PREFIX}lambda-deploy.zip" --region $REGION | Out-Null
}

# ============================================================================
# Step 6: Build env-vars JSON from .env + mandatory overrides
# ============================================================================
Write-Host "[6/8] Deploying Lambda function..." -ForegroundColor Yellow

# Start with the mandatory Lambda-specific overrides
$envVars = [ordered]@{
    S3_BUCKET              = $S3_BUCKET
    S3_PREFIX              = $S3_PREFIX
    REPLY_MODE             = "auto"
    GMAIL_TOKEN_FILE       = "/tmp/token.json"
    GMAIL_CREDENTIALS_FILE = "/tmp/credentials.json"
}

# Keys that must NOT be overridden by .env values
$overrideKeys = [System.Collections.Generic.HashSet[string]]@(
    "REPLY_MODE", "GMAIL_TOKEN_FILE", "GMAIL_CREDENTIALS_FILE", "S3_BUCKET", "S3_PREFIX"
)

# Read .env and merge (skip comments and blank lines; skip override keys)
Get-Content (Join-Path $PROJECT_ROOT ".env") | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    if ($line -match '^([A-Z_][A-Z_0-9]*)\s*=\s*(.*)$') {
        $k = $Matches[1]
        $v = $Matches[2].Trim()
        if (-not $overrideKeys.Contains($k) -and $v -ne "") {
            $envVars[$k] = $v
        }
    }
}

$envFile = Join-Path $DEPLOY_DIR "env-vars.json"
[System.IO.File]::WriteAllText(
    $envFile,
    (@{ Variables = $envVars } | ConvertTo-Json -Compress -Depth 3),
    [System.Text.Encoding]::ASCII
)
$envFilePath = $envFile.Replace('\', '/')

# ============================================================================
# Create or update the Lambda function
# ============================================================================
$null = aws lambda get-function --function-name $FUNCTION_NAME --region $REGION 2>&1
$functionExists = ($LASTEXITCODE -eq 0)

if ($functionExists) {
    Write-Host "  Updating existing function code..." -ForegroundColor DarkYellow

    if ($deployViaS3) {
        aws lambda update-function-code `
            --function-name $FUNCTION_NAME `
            --s3-bucket $S3_BUCKET `
            --s3-key "${S3_PREFIX}lambda-deploy.zip" `
            --region $REGION | Out-Null
    } else {
        $zipFilePath = $ZIP_FILE.Replace('\', '/')
        aws lambda update-function-code `
            --function-name $FUNCTION_NAME `
            --zip-file "fileb://$zipFilePath" `
            --region $REGION | Out-Null
    }

    Write-Host "  Waiting for code update to finish..." -ForegroundColor DarkYellow
    aws lambda wait function-updated --function-name $FUNCTION_NAME --region $REGION 2>&1 | Out-Null

    aws lambda update-function-configuration `
        --function-name $FUNCTION_NAME `
        --handler $HANDLER `
        --runtime $RUNTIME `
        --timeout $TIMEOUT `
        --memory-size $MEMORY_SIZE `
        --environment "file://$envFilePath" `
        --region $REGION | Out-Null

} else {
    Write-Host "  Creating new Lambda function..." -ForegroundColor DarkYellow

    if ($deployViaS3) {
        aws lambda create-function `
            --function-name $FUNCTION_NAME `
            --runtime $RUNTIME `
            --role $ROLE_ARN `
            --handler $HANDLER `
            --timeout $TIMEOUT `
            --memory-size $MEMORY_SIZE `
            --code "S3Bucket=$S3_BUCKET,S3Key=${S3_PREFIX}lambda-deploy.zip" `
            --environment "file://$envFilePath" `
            --region $REGION | Out-Null
    } else {
        $zipFilePath = $ZIP_FILE.Replace('\', '/')
        aws lambda create-function `
            --function-name $FUNCTION_NAME `
            --runtime $RUNTIME `
            --role $ROLE_ARN `
            --handler $HANDLER `
            --timeout $TIMEOUT `
            --memory-size $MEMORY_SIZE `
            --zip-file "fileb://$zipFilePath" `
            --environment "file://$envFilePath" `
            --region $REGION | Out-Null
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: Lambda deploy failed!" -ForegroundColor Red
    exit 1
}
Write-Host "  Lambda function deployed." -ForegroundColor Green

# Wait until the function is active before attaching EventBridge
aws lambda wait function-active --function-name $FUNCTION_NAME --region $REGION 2>&1 | Out-Null

# ============================================================================
# Step 7: EventBridge hourly schedule
# ============================================================================
Write-Host "[7/8] EventBridge hourly trigger..." -ForegroundColor Yellow

# Create / update the schedule rule
aws events put-rule `
    --name $SCHEDULE_NAME `
    --schedule-expression $SCHEDULE_EXPR `
    --state ENABLED `
    --description "Trigger $FUNCTION_NAME every hour" `
    --region $REGION | Out-Null

# Grant EventBridge permission to invoke the function (idempotent: ignore AlreadyExists)
aws lambda add-permission `
    --function-name $FUNCTION_NAME `
    --statement-id "${SCHEDULE_NAME}-invoke" `
    --action "lambda:InvokeFunction" `
    --principal "events.amazonaws.com" `
    --source-arn "arn:aws:events:${REGION}:${ACCOUNT_ID}:rule/${SCHEDULE_NAME}" `
    --region $REGION 2>&1 | Out-Null   # ignore "already exists" error

# Wire Lambda as the rule target (write JSON to file to avoid shell-quoting hell)
$lambdaArn = "arn:aws:lambda:${REGION}:${ACCOUNT_ID}:function:${FUNCTION_NAME}"
$targetsFile = Join-Path $DEPLOY_DIR "targets.json"
[System.IO.File]::WriteAllText(
    $targetsFile,
    (@(@{ Id = "1"; Arn = $lambdaArn }) | ConvertTo-Json -Compress),
    [System.Text.Encoding]::ASCII
)
$targetsFilePath = $targetsFile.Replace('\', '/')
aws events put-targets `
    --rule $SCHEDULE_NAME `
    --targets "file://$targetsFilePath" `
    --region $REGION | Out-Null

Write-Host "  EventBridge rule active: $SCHEDULE_NAME ($SCHEDULE_EXPR)" -ForegroundColor Green

# ============================================================================
# Step 8: Verify + summary
# ============================================================================
Write-Host "[8/8] Verifying..." -ForegroundColor Yellow

$funcInfo = aws lambda get-function-configuration `
    --function-name $FUNCTION_NAME --region $REGION | ConvertFrom-Json

Write-Host ""
Write-Host "=== Deployment Complete ===" -ForegroundColor Green
Write-Host "  Function : $($funcInfo.FunctionName)"
Write-Host "  Runtime  : $($funcInfo.Runtime)"
Write-Host "  Handler  : $($funcInfo.Handler)"
Write-Host "  Memory   : $($funcInfo.MemorySize) MB"
Write-Host "  Timeout  : $($funcInfo.Timeout) s"
Write-Host "  Modified : $($funcInfo.LastModified)"
Write-Host "  Schedule : $SCHEDULE_EXPR (EventBridge: $SCHEDULE_NAME)"
Write-Host ""
Write-Host "Invoke manually:" -ForegroundColor Cyan
Write-Host "  aws lambda invoke --function-name $FUNCTION_NAME --region $REGION --log-type Tail --query LogResult --output text out.json | %{[System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(`$_))}"
Write-Host ""
Write-Host "Stream logs:" -ForegroundColor Cyan
Write-Host "  aws logs tail /aws/lambda/$FUNCTION_NAME --region $REGION --follow"
Write-Host ""

# Cleanup temp files generated during deploy
foreach ($tmp in @($policyFile, $envFile, $targetsFile)) {
    Remove-Item $tmp -ErrorAction SilentlyContinue
}
