#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

if [ "${AUTOANIME_PROXY:-}" != "" ]; then
  export HTTP_PROXY="${AUTOANIME_PROXY}"
  export HTTPS_PROXY="${AUTOANIME_PROXY}"
  export http_proxy="${AUTOANIME_PROXY}"
  export https_proxy="${AUTOANIME_PROXY}"
  echo "Using build proxy: ${AUTOANIME_PROXY}"
fi

if [ "${AUTOANIME_ALL_PROXY:-}" != "" ]; then
  export ALL_PROXY="${AUTOANIME_ALL_PROXY}"
  export all_proxy="${AUTOANIME_ALL_PROXY}"
  echo "Using all proxy: ${AUTOANIME_ALL_PROXY}"
fi

echo "Stopping existing AutoAnime container..."
docker compose down --remove-orphans

echo "Building and starting AutoAnime..."
docker compose up -d --build

echo "Checking container status..."
docker compose ps

echo "Checking rclone version..."
docker exec autoanime rclone version || {
  echo "rclone is not available in the container."
  exit 1
}

echo "Checking rclone PikPak backend..."
if docker exec autoanime rclone help backends | grep -i pikpak >/dev/null 2>&1; then
  echo "PikPak backend is available."
else
  echo "PikPak backend is NOT available. Rebuild with network access to rclone downloads."
  exit 1
fi

echo "AutoAnime deployed: http://NAS_IP:32888"
