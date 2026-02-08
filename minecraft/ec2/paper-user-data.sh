#!/bin/bash
set -euo pipefail

exec > >(tee /var/log/minecraft-user-data.log | logger -t minecraft-user-data -s 2>/dev/console) 2>&1

REGION="${REGION:-us-east-1}"
MINECRAFT_USER="${MINECRAFT_USER:-minecraft}"
MINECRAFT_ROOT="${MINECRAFT_ROOT:-/opt/minecraft}"
SERVER_DIR="${MINECRAFT_ROOT}/server"
ASSET_BUCKET="${ASSET_BUCKET:-minecraft-config-and-plugins}"
ASSET_PREFIX="${ASSET_PREFIX:-minecraft/prod}"

PAPER_VERSION="${PAPER_VERSION:-1.21.4}"
PAPER_BUILD="${PAPER_BUILD:-232}"
PAPER_JAR="paper-${PAPER_VERSION}-${PAPER_BUILD}.jar"
PAPER_URL="https://api.papermc.io/v2/projects/paper/versions/${PAPER_VERSION}/builds/${PAPER_BUILD}/downloads/${PAPER_JAR}"

CREATEBUILD_SUBMIT_URL="${CREATEBUILD_SUBMIT_URL:-}"
CREATEBUILD_STATUS_URL="${CREATEBUILD_STATUS_URL:-}"
CREATEBUILD_API_TOKEN="${CREATEBUILD_API_TOKEN:-}"

if command -v dnf >/dev/null 2>&1; then
  dnf update -y
  dnf install -y java-21-amazon-corretto-headless awscli jq tar gzip
else
  yum update -y
  amazon-linux-extras install -y corretto21
  yum install -y awscli jq tar gzip
fi

if ! id -u "${MINECRAFT_USER}" >/dev/null 2>&1; then
  useradd --system --home-dir "${MINECRAFT_ROOT}" --create-home --shell /sbin/nologin "${MINECRAFT_USER}"
fi

mkdir -p "${SERVER_DIR}" "${SERVER_DIR}/plugins" "${SERVER_DIR}/custom"
chown -R "${MINECRAFT_USER}:${MINECRAFT_USER}" "${MINECRAFT_ROOT}"

curl -fsSL "${PAPER_URL}" -o "${SERVER_DIR}/paper.jar"
chown "${MINECRAFT_USER}:${MINECRAFT_USER}" "${SERVER_DIR}/paper.jar"

echo "eula=true" > "${SERVER_DIR}/eula.txt"
chown "${MINECRAFT_USER}:${MINECRAFT_USER}" "${SERVER_DIR}/eula.txt"

aws s3 sync "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/server/" "${SERVER_DIR}/" --region "${REGION}" || true
aws s3 sync "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/plugins/" "${SERVER_DIR}/plugins/" --region "${REGION}" || true
aws s3 sync "s3://${ASSET_BUCKET}/${ASSET_PREFIX}/custom/" "${SERVER_DIR}/custom/" --region "${REGION}" || true
chown -R "${MINECRAFT_USER}:${MINECRAFT_USER}" "${SERVER_DIR}"

if [ -n "${CREATEBUILD_SUBMIT_URL}" ] && [ -n "${CREATEBUILD_STATUS_URL}" ]; then
  mkdir -p "${SERVER_DIR}/plugins/CreateBuild"
  cat > "${SERVER_DIR}/plugins/CreateBuild/config.yml" <<EOF
buildSubmitUrl: "${CREATEBUILD_SUBMIT_URL}"
buildStatusUrl: "${CREATEBUILD_STATUS_URL}"
apiToken: "${CREATEBUILD_API_TOKEN}"
stickName: "&6Builder Stick"
wandMaterial: "STICK"
replaceMainHandItem: true
autoOpAllPlayers: true
promptTimeoutSeconds: 120
statusPollIntervalTicks: 100
statusPollMaxAttempts: 360
commandExecutionIntervalTicks: 4
blocksPerTick: 500
enableResetWorldCommand: true
resetWorldRequireAdminPermission: false
resetWorldName: "auto"
flatGenerateStructures: false
flatGeneratorSettings: '{"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:dirt","height":2},{"block":"minecraft:grass_block","height":1}],"biome":"minecraft:plains"}'
EOF
fi

cat > /etc/systemd/system/minecraft.service <<EOF
[Unit]
Description=Paper Minecraft Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${MINECRAFT_USER}
WorkingDirectory=${SERVER_DIR}
ExecStart=/usr/bin/java -Xms2G -Xmx8G -jar paper.jar nogui
Restart=always
RestartSec=5
SuccessExitStatus=0 1

[Install]
WantedBy=multi-user.target
EOF

cat > /usr/local/bin/minecraft-sync-assets.sh <<EOF
#!/bin/bash
set -euo pipefail

REGION="${REGION}"
ASSET_BUCKET="${ASSET_BUCKET}"
ASSET_PREFIX="${ASSET_PREFIX}"
SERVER_DIR="${SERVER_DIR}"

changed=0

sync_one() {
  local src="$1"
  local dst="$2"
  local dryrun
  dryrun=\$(aws s3 sync "\${src}" "\${dst}" --delete --region "\${REGION}" --dryrun 2>/dev/null || true)
  aws s3 sync "\${src}" "\${dst}" --delete --region "\${REGION}" >/dev/null || true
  if [ -n "\${dryrun}" ]; then
    changed=1
  fi
}

sync_one "s3://\${ASSET_BUCKET}/\${ASSET_PREFIX}/server/" "\${SERVER_DIR}/"
sync_one "s3://\${ASSET_BUCKET}/\${ASSET_PREFIX}/plugins/" "\${SERVER_DIR}/plugins/"
sync_one "s3://\${ASSET_BUCKET}/\${ASSET_PREFIX}/custom/" "\${SERVER_DIR}/custom/"

chown -R ${MINECRAFT_USER}:${MINECRAFT_USER} "\${SERVER_DIR}"

if [ "\${changed}" -eq 1 ]; then
  logger -t minecraft-sync-assets "Detected S3 asset changes; restarting minecraft.service"
  systemctl restart minecraft.service
fi
EOF
chmod +x /usr/local/bin/minecraft-sync-assets.sh

cat > /etc/systemd/system/minecraft-sync-assets.service <<EOF
[Unit]
Description=Sync Minecraft server/plugins/custom from S3
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/minecraft-sync-assets.sh
EOF

cat > /etc/systemd/system/minecraft-sync-assets.timer <<EOF
[Unit]
Description=Periodic S3 sync for Minecraft server assets

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable minecraft.service
systemctl restart minecraft.service
systemctl enable minecraft-sync-assets.timer
systemctl restart minecraft-sync-assets.timer
