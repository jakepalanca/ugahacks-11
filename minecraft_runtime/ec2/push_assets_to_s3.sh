#!/bin/bash
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
ASSET_BUCKET="${1:-}"
ASSET_PREFIX="${2:-minecraft/prod}"

if [ -z "${ASSET_BUCKET}" ]; then
  echo "Usage: $0 <asset-bucket> [asset-prefix]" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ASSETS_DIR="${ROOT_DIR}/server-assets"
SERVER_DIR="${ASSETS_DIR}/server"
LEGACY_CONFIG_DIR="${ASSETS_DIR}/config"
PLUGINS_DIR="${ASSETS_DIR}/plugins"
CUSTOM_DIR="${ASSETS_DIR}/custom"
LEGACY_MODS_DIR="${ASSETS_DIR}/mods"

echo "Syncing Minecraft assets to s3://${ASSET_BUCKET}/${ASSET_PREFIX}"
if [ -d "${SERVER_DIR}" ]; then
  aws s3 sync "${SERVER_DIR}/" "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/server/" --region "${REGION}"
elif [ -d "${LEGACY_CONFIG_DIR}" ]; then
  aws s3 sync "${LEGACY_CONFIG_DIR}/" "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/server/" --region "${REGION}"
else
  echo "WARN: No server config directory found at ${SERVER_DIR} or ${LEGACY_CONFIG_DIR}"
fi

if [ -d "${PLUGINS_DIR}" ]; then
  aws s3 sync "${PLUGINS_DIR}/" "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/plugins/" --region "${REGION}"
else
  echo "WARN: No plugins directory found at ${PLUGINS_DIR}"
fi

if [ -d "${CUSTOM_DIR}" ]; then
  aws s3 sync "${CUSTOM_DIR}/" "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/custom/" --region "${REGION}"
elif [ -d "${LEGACY_MODS_DIR}" ]; then
  aws s3 sync "${LEGACY_MODS_DIR}/" "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/custom/" --region "${REGION}"
else
  echo "WARN: No custom directory found at ${CUSTOM_DIR} or ${LEGACY_MODS_DIR}"
fi

aws s3 cp "${ROOT_DIR}/ec2/paper_user_data.sh" "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/bootstrap/paper_user_data.sh" --region "${REGION}"

echo "Upload complete."
