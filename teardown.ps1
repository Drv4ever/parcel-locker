# ==============================================================================
# Teardown Script (PowerShell) — Cloud-Based Digital Locker System
# Run after viva in PowerShell: .\teardown.ps1
# ==============================================================================

$env:AWS_PROFILE = "dhruv-jain"
$aws = "C:\Users\Dhruv Jain\AppData\Local\Programs\Amazon\AWSCLIV2\aws.exe"
$region = "ap-south-1"
$accountId = "652872010155"

Write-Host ">>> Starting Teardown in $region <<<" -ForegroundColor Yellow

# 1. EC2 Instance
Write-Host "--- 1. Terminating EC2 Instance ---"
$instanceIds = (& $aws ec2 describe-instances --filters "Name=tag:Name,Values=parcel-warden-dashboard" "Name=instance-state-name,Values=running,pending,stopped" --query "Reservations[*].Instances[*].InstanceId" --region $region --output text)
if ($instanceIds -and $instanceIds -ne "None") {
    & $aws ec2 terminate-instances --instance-ids $instanceIds --region $region
    Write-Host "Waiting for EC2 termination..."
    & $aws ec2 wait instance-terminated --instance-ids $instanceIds --region $region
}

# 2. Security Group
Write-Host "--- 2. Deleting Security Group ---"
$sgId = (& $aws ec2 describe-security-groups --filters "Name=group-name,Values=parcel-dashboard-sg" --query "SecurityGroups[0].GroupId" --region $region --output text 2>$null)
if ($sgId -and $sgId -ne "None") {
    & $aws ec2 delete-security-group --group-id $sgId --region $region
}

# 3. CloudWatch Alarms & Dashboards
Write-Host "--- 3. Deleting CloudWatch Alarm & Dashboard ---"
& $aws cloudwatch delete-alarms --alarm-names "parcel-dlq-messages-visible-alarm" --region $region
& $aws cloudwatch delete-dashboards --dashboard-names "ParcelSystem-Monitoring" --region $region

# 4. EventBridge Rule
Write-Host "--- 4. Deleting EventBridge Scheduled Rule ---"
& $aws events remove-targets --rule "parcel-state-audit-schedule" --ids "1" --region $region
& $aws events delete-rule --name "parcel-state-audit-schedule" --region $region

# 5. Lambdas
Write-Host "--- 5. Deleting Lambda Functions ---"
$fns = @("parcel-core-logic", "parcel-student-notify", "parcel-analytics", "parcel-audit", "parcel-state-auditor")
foreach ($fn in $fns) {
    & $aws lambda delete-function --function-name $fn --region $region
}

# 6. SNS Topics
Write-Host "--- 6. Deleting SNS Topics ---"
& $aws sns delete-topic --topic-arn "arn:aws:sns:$region:$accountId:parcel-events" --region $region
& $aws sns delete-topic --topic-arn "arn:aws:sns:$region:$accountId:parcel-dlq-alarm-topic" --region $region

# 7. SQS Queue
Write-Host "--- 7. Deleting SQS DLQ ---"
& $aws sqs delete-queue --queue-url "https://sqs.$region.amazonaws.com/$accountId/parcel-dlq" --region $region

# 8. DynamoDB
Write-Host "--- 8. Deleting DynamoDB Table ---"
& $aws dynamodb delete-table --table-name "ParcelTable" --region $region
& $aws dynamodb wait table-not-exists --table-name "ParcelTable" --region $region

# 9. S3 Buckets
Write-Host "--- 9. Emptying and Deleting S3 Buckets ---"
$buckets = @("parcel-photos-dhruv-$accountId", "parcel-audit-logs-dhruv-$accountId")
foreach ($b in $buckets) {
    & $aws s3 rm "s3://$b" --recursive --region $region
    & $aws s3api delete-bucket --bucket $b --region $region
}

# 10. IAM
Write-Host "--- 10. Deleting IAM Roles & Profiles ---"
& $aws iam remove-role-from-instance-profile --instance-profile-name parcel-ec2-profile --role-name parcel-ec2-role
& $aws iam delete-instance-profile --instance-profile-name parcel-ec2-profile
& $aws iam delete-role-policy --role-name parcel-ec2-role --policy-name parcel-ec2-policy
& $aws iam delete-role --role-name parcel-ec2-role
& $aws iam detach-role-policy --role-name parcel-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
& $aws iam delete-role-policy --role-name parcel-lambda-role --policy-name parcel-lambda-policy
& $aws iam delete-role --role-name parcel-lambda-role

Write-Host ">>> Teardown Complete! <<<" -ForegroundColor Green