#!/usr/bin/env sh
set -eu

cd "$(dirname "$0")"

BUILD_PROXY="${ANITRACK_PROXY:-}"
ALL_BUILD_PROXY="${ANITRACK_ALL_PROXY:-}"

if [ "${BUILD_PROXY}" != "" ]; then
  export HTTP_PROXY="${BUILD_PROXY}"
  export HTTPS_PROXY="${BUILD_PROXY}"
  export http_proxy="${BUILD_PROXY}"
  export https_proxy="${BUILD_PROXY}"
  echo "Using build proxy: ${BUILD_PROXY}"
fi

if [ "${ALL_BUILD_PROXY}" != "" ]; then
  export ALL_PROXY="${ALL_BUILD_PROXY}"
  export all_proxy="${ALL_BUILD_PROXY}"
  echo "Using all proxy: ${ALL_BUILD_PROXY}"
fi

echo "Stopping existing AniTrack container..."
docker compose down --remove-orphans || true
docker rm -f anitrack >/dev/null 2>&1 || true
docker rm -f autoanime >/dev/null 2>&1 || true

echo "Building and starting AniTrack..."
docker compose up -d --build --force-recreate --remove-orphans

echo "Checking container status..."
docker compose ps

echo "Checking rclone version..."
docker exec anitrack rclone version || {
  echo "rclone is not available in the container."
  exit 1
}

echo "Checking rclone PikPak backend..."
if docker exec anitrack rclone help backends | grep -i pikpak >/dev/null 2>&1; then
  echo "PikPak backend is available."
else
  echo "PikPak backend is NOT available. Rebuild with network access to rclone downloads."
  exit 1
fi

echo "AniTrack deployed: http://NAS_IP:32888"
