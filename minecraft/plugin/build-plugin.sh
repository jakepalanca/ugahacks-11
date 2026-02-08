#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

mvn -f "${SCRIPT_DIR}/pom.xml" -DskipTests clean package
cp "${SCRIPT_DIR}/target/CreateBuildPlugin.jar" "${ROOT_DIR}/server-assets/plugins/CreateBuildPlugin.jar"
echo "Built plugin jar at ${ROOT_DIR}/server-assets/plugins/CreateBuildPlugin.jar"
