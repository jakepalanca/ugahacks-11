#!/bin/bash
set -euo pipefail
export AWS_PAGER=""

REGION="${AWS_REGION:-${REGION:-us-east-1}}"
JOB_TABLE="${JOB_TABLE:-createbuild-jobs}"
WORKER_LOCK_KEY="${WORKER_LOCK_KEY:-__worker_lock__}"
CANCEL_REASON="${CANCEL_REASON:-Canceled while clearing queue}"
DRY_RUN="${DRY_RUN:-0}"

usage() {
  cat <<'EOF'
Usage: clear-createbuild-queue.sh

Clears active createbuild jobs (QUEUED/STARTING/RUNNING) and releases the worker lock row.

Environment:
  AWS_REGION / REGION        AWS region (default: us-east-1)
  JOB_TABLE                  DynamoDB table name (default: createbuild-jobs)
  WORKER_LOCK_KEY            Lock row key (default: __worker_lock__)
  CANCEL_REASON              Message written to failed jobs
  DRY_RUN                    1 = list only, no updates (default: 0)
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "Missing required command: aws" >&2
  exit 1
fi

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\t'/\\t}"
  printf "%s" "${value}"
}

if ! aws dynamodb describe-table --table-name "${JOB_TABLE}" --region "${REGION}" >/dev/null 2>&1; then
  echo "Queue table not found (${JOB_TABLE}) in ${REGION}; nothing to clear."
  exit 0
fi

NOW_EPOCH="$(date +%s)"
NOW_ISO="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
ESCAPED_LOCK_KEY="$(json_escape "${WORKER_LOCK_KEY}")"
ESCAPED_REASON="$(json_escape "${CANCEL_REASON}")"

SCAN_VALUES_JSON="$(cat <<EOF
{
  ":queued": {"S": "QUEUED"},
  ":starting": {"S": "STARTING"},
  ":running": {"S": "RUNNING"},
  ":lock": {"S": "${ESCAPED_LOCK_KEY}"}
}
EOF
)"

ACTIVE_JOB_IDS="$(
  aws dynamodb scan \
    --table-name "${JOB_TABLE}" \
    --region "${REGION}" \
    --projection-expression "job_id,#st" \
    --expression-attribute-names '{"#st":"status"}' \
    --filter-expression "(#st = :queued OR #st = :starting OR #st = :running) AND job_id <> :lock" \
    --expression-attribute-values "${SCAN_VALUES_JSON}" \
    --query 'Items[].job_id.S' \
    --output text
)"

cleared_count=0
if [ -n "${ACTIVE_JOB_IDS//[[:space:]]/}" ] && [ "${ACTIVE_JOB_IDS}" != "None" ]; then
  for job_id in ${ACTIVE_JOB_IDS}; do
    if [ "${job_id}" = "${WORKER_LOCK_KEY}" ]; then
      continue
    fi

    echo "Clearing active job: ${job_id}"
    if [ "${DRY_RUN}" != "1" ]; then
      UPDATE_VALUES_JSON="$(cat <<EOF
{
  ":failed": {"S": "FAILED"},
  ":stage": {"S": "failed"},
  ":msg": {"S": "${ESCAPED_REASON}"},
  ":err": {"S": "${ESCAPED_REASON}"},
  ":updated": {"S": "${NOW_ISO}"},
  ":ended": {"S": "${NOW_ISO}"}
}
EOF
)"
      aws dynamodb update-item \
        --table-name "${JOB_TABLE}" \
        --region "${REGION}" \
        --key "{\"job_id\":{\"S\":\"${job_id}\"}}" \
        --update-expression "SET #st = :failed, progress_stage = :stage, progress_message = :msg, error = :err, updated_at = :updated, ended_at = :ended" \
        --expression-attribute-names '{"#st":"status"}' \
        --expression-attribute-values "${UPDATE_VALUES_JSON}" >/dev/null
    fi
    cleared_count=$((cleared_count + 1))
  done
fi

if [ "${DRY_RUN}" = "1" ]; then
  echo "Dry run only. Would clear ${cleared_count} active job(s) and release worker lock."
  exit 0
fi

LOCK_VALUES_JSON="$(cat <<EOF
{
  ":idle": {"S": "IDLE"},
  ":now": {"N": "${NOW_EPOCH}"},
  ":updated": {"S": "${NOW_ISO}"}
}
EOF
)"

aws dynamodb update-item \
  --table-name "${JOB_TABLE}" \
  --region "${REGION}" \
  --key "{\"job_id\":{\"S\":\"${ESCAPED_LOCK_KEY}\"}}" \
  --update-expression "SET #st = :idle, locked_until = :now, updated_at = :updated REMOVE owner_job_id" \
  --expression-attribute-names '{"#st":"status"}' \
  --expression-attribute-values "${LOCK_VALUES_JSON}" >/dev/null

echo "Cleared ${cleared_count} active createbuild job(s) and released worker lock in ${JOB_TABLE} (${REGION})."
