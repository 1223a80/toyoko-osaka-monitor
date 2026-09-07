#!/usr/bin/env bash
set -euo pipefail

AWS_REGION="ap-northeast-1"
STACK_NAME="toyoko-osaka-monitor"
PACKAGE_FILE="toyoko-lambda.zip"
TEMPLATE_FILE="cloudformation.yaml"

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 EMAIL_ADDRESS" >&2
  exit 2
fi

NOTIFICATION_EMAIL="$1"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ARTIFACT_BUCKET="toyoko-monitor-artifacts-${ACCOUNT_ID}-${AWS_REGION}"
PACKAGE_SHA256="$(sha256sum "${PACKAGE_FILE}" | awk '{print $1}')"
ARTIFACT_KEY="deployments/toyoko-lambda-${PACKAGE_SHA256}.zip"

if ! aws s3api head-bucket --bucket "${ARTIFACT_BUCKET}" 2>/dev/null; then
  aws s3api create-bucket \
    --bucket "${ARTIFACT_BUCKET}" \
    --region "${AWS_REGION}" \
    --create-bucket-configuration LocationConstraint="${AWS_REGION}"
fi

aws s3 cp "${PACKAGE_FILE}" "s3://${ARTIFACT_BUCKET}/${ARTIFACT_KEY}" \
  --region "${AWS_REGION}"

aws cloudformation deploy \
  --region "${AWS_REGION}" \
  --stack-name "${STACK_NAME}" \
  --template-file "${TEMPLATE_FILE}" \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ArtifactBucket="${ARTIFACT_BUCKET}" \
    ArtifactKey="${ARTIFACT_KEY}" \
    NotificationEmail="${NOTIFICATION_EMAIL}" \
  --tags Project="toyoko-osaka-monitor"

aws lambda invoke \
  --region "${AWS_REGION}" \
  --function-name "toyoko-osaka-monitor" \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  response.json

echo
echo "Deployment and first test invocation completed."
echo "On the first deployment only, open the AWS SNS email and confirm the subscription."
cat response.json
