#!/usr/bin/env bash
# ==============================================================================
# Teardown Script — Cloud-Based Digital Locker & Parcel Notification System
# Deletes all provisioned AWS resources in reverse order to ensure clean removal
# without orphaned dependencies or lingering costs.
# ==============================================================================

set -e

REGION="ap-south-1"
ACCOUNT_ID="652872010155"
PROFILE="${AWS_PROFILE:-dhruv-jain}"

echo ">>> Starting teardown in Region: $REGION (Profile: $PROFILE) <<<"

# 1. Terminate EC2 Instance
echo "--- 1. Terminating EC2 Warden Dashboard Instance ---"
INSTANCE_IDS=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=parcel-warden-dashboard" "Name=instance-state-name,Values=running,pending,stopped" \
  --query "Reservations[*].Instances[*].InstanceId" \
  --region "$REGION" --profile "$PROFILE" --output text)

if [ -n "$INSTANCE_IDS" ]; then
  echo "Terminating instance(s): $INSTANCE_IDS"
  aws ec2 terminate-instances --instance-ids $INSTANCE_IDS --region "$REGION" --profile "$PROFILE"
  echo "Waiting for instance termination..."
  aws ec2 wait instance-terminated --instance-ids $INSTANCE_IDS --region "$REGION" --profile "$PROFILE"
else
  echo "No EC2 instances found."
fi

# 2. Delete Security Group
echo "--- 2. Deleting Security Group parcel-dashboard-sg ---"
SG_ID=$(aws ec2 describe-security-groups \
  --filters "Name=group-name,Values=parcel-dashboard-sg" \
  --query "SecurityGroups[0].GroupId" \
  --region "$REGION" --profile "$PROFILE" --output text 2>/dev/null || true)

if [ -n "$SG_ID" ] && [ "$SG_ID" != "None" ]; then
  echo "Deleting Security Group: $SG_ID"
  aws ec2 delete-security-group --group-id "$SG_ID" --region "$REGION" --profile "$PROFILE" || true
fi

# 3. Delete CloudWatch Alarm & Dashboard
echo "--- 3. Deleting CloudWatch Alarm & Dashboard ---"
aws cloudwatch delete-alarms --alarm-names "parcel-dlq-messages-visible-alarm" --region "$REGION" --profile "$PROFILE" || true
aws cloudwatch delete-dashboards --dashboard-names "ParcelSystem-Monitoring" --region "$REGION" --profile "$PROFILE" || true

# 4. Delete EventBridge Rule
echo "--- 4. Deleting EventBridge Scheduled Rule ---"
aws events remove-targets --rule "parcel-state-audit-schedule" --ids "1" --region "$REGION" --profile "$PROFILE" || true
aws events delete-rule --name "parcel-state-audit-schedule" --region "$REGION" --profile "$PROFILE" || true

# 5. Delete Lambda Functions
echo "--- 5. Deleting Lambda Functions ---"
FUNCTIONS=("parcel-core-logic" "parcel-student-notify" "parcel-analytics" "parcel-audit" "parcel-state-auditor")
for fn in "${FUNCTIONS[@]}"; do
  echo "Deleting function: $fn"
  aws lambda delete-function --function-name "$fn" --region "$REGION" --profile "$PROFILE" || true
done

# 6. Delete SNS Topics
echo "--- 6. Deleting SNS Topics ---"
TOPIC_ARNS=(
  "arn:aws:sns:$REGION:$ACCOUNT_ID:parcel-events"
  "arn:aws:sns:$REGION:$ACCOUNT_ID:parcel-dlq-alarm-topic"
)
for t in "${TOPIC_ARNS[@]}"; do
  echo "Deleting topic: $t"
  aws sns delete-topic --topic-arn "$t" --region "$REGION" --profile "$PROFILE" || true
done

# 7. Delete SQS Dead-Letter Queue
echo "--- 7. Deleting SQS DLQ ---"
DLQ_URL="https://sqs.$REGION.amazonaws.com/$ACCOUNT_ID/parcel-dlq"
aws sqs delete-queue --queue-url "$DLQ_URL" --region "$REGION" --profile "$PROFILE" || true

# 8. Delete DynamoDB Table
echo "--- 8. Deleting DynamoDB Table ParcelTable ---"
aws dynamodb delete-table --table-name "ParcelTable" --region "$REGION" --profile "$PROFILE" || true
echo "Waiting for DynamoDB table deletion..."
aws dynamodb wait table-not-exists --table-name "ParcelTable" --region "$REGION" --profile "$PROFILE" || true

# 9. Empty and Delete S3 Buckets
echo "--- 9. Emptying and Deleting S3 Buckets ---"
BUCKETS=(
  "parcel-photos-dhruv-$ACCOUNT_ID"
  "parcel-audit-logs-dhruv-$ACCOUNT_ID"
)
for b in "${BUCKETS[@]}"; do
  echo "Emptying bucket: s3://$b"
  aws s3 rm "s3://$b" --recursive --region "$REGION" --profile "$PROFILE" || true
  echo "Deleting bucket: s3://$b"
  aws s3api delete-bucket --bucket "$b" --region "$REGION" --profile "$PROFILE" || true
done

# 10. Delete IAM Roles & Instance Profile
echo "--- 10. Deleting IAM Roles & Instance Profile ---"
# Remove role from instance profile, delete instance profile
aws iam remove-role-from-instance-profile --instance-profile-name parcel-ec2-profile --role-name parcel-ec2-role || true
aws iam delete-instance-profile --instance-profile-name parcel-ec2-profile || true

# Delete parcel-ec2-role inline policy and role
aws iam delete-role-policy --role-name parcel-ec2-role --policy-name parcel-ec2-policy || true
aws iam delete-role --role-name parcel-ec2-role || true

# Detach managed policy and delete inline policy from parcel-lambda-role
aws iam detach-role-policy --role-name parcel-lambda-role --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole || true
aws iam delete-role-policy --role-name parcel-lambda-role --policy-name parcel-lambda-policy || true
aws iam delete-role --role-name parcel-lambda-role || true

echo "========================================================"
echo ">>> Teardown Complete! All resources cleaned up. <<<"
echo "========================================================"