#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CFN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

REGION="${AWS_REGION:-us-east-1}"
NAME_PREFIX="${NAME_PREFIX:-createbuild-prod}"
STACK_NAME="${STACK_NAME:-${NAME_PREFIX}-stack}"
DELETE_PACKAGING_BUCKET="${DELETE_PACKAGING_BUCKET:-false}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

stack_exists() {
  aws cloudformation describe-stacks \
    --region "${REGION}" \
    --stack-name "${STACK_NAME}" >/dev/null 2>&1
}

stack_output_or_empty() {
  local key="$1"
  aws cloudformation describe-stacks \
    --region "${REGION}" \
    --stack-name "${STACK_NAME}" \
    --query "Stacks[0].Outputs[?OutputKey=='${key}'].OutputValue | [0]" \
    --output text 2>/dev/null || true
}

empty_bucket() {
  local bucket="$1"
  if [ -z "${bucket}" ] || [ "${bucket}" = "None" ]; then
    return
  fi

  echo "Emptying s3://${bucket}"
  aws s3 rm "s3://${bucket}" --recursive --region "${REGION}" >/dev/null 2>&1 || true
}

require_cmd aws

if ! stack_exists; then
  echo "Stack ${STACK_NAME} was not found in ${REGION}. Nothing to delete."
  exit 0
fi

ASSETS_BUCKET="$(stack_output_or_empty AssetsBucketName)"
PIPELINE_BUCKET="$(stack_output_or_empty PipelineBucketName)"

# Buckets must be emptied before stack deletion when they are not empty.
empty_bucket "${ASSETS_BUCKET}"
empty_bucket "${PIPELINE_BUCKET}"

echo "Deleting CloudFormation stack: ${STACK_NAME}"
aws cloudformation delete-stack \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}"

echo "Waiting for stack deletion..."
aws cloudformation wait stack-delete-complete \
  --region "${REGION}" \
  --stack-name "${STACK_NAME}"

if [ "${DELETE_PACKAGING_BUCKET}" = "true" ]; then
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text --region "${REGION}")"
  PACKAGING_BUCKET="${PACKAGING_BUCKET:-${NAME_PREFIX}-${ACCOUNT_ID}-${REGION}-cfn-artifacts}"
  echo "Deleting packaging bucket: s3://${PACKAGING_BUCKET}"
  aws s3 rm "s3://${PACKAGING_BUCKET}" --recursive --region "${REGION}" >/dev/null 2>&1 || true
  aws s3api delete-bucket --bucket "${PACKAGING_BUCKET}" --region "${REGION}" >/dev/null 2>&1 || true
fi

echo "Destroy complete: ${STACK_NAME} (${REGION})"
echo "You can redeploy with: ${CFN_DIR}/scripts/deploy.sh"
