#!/usr/bin/env bash
set -euo pipefail
image="weather-tls:${GITHUB_SHA:-local}"
mkdir -p build/tls
# Native runners exercise the same installed Python/aiohttp as production.
docker build --no-cache --pull --platform "linux/${EXPECTED_ARCH:?architecture required}" -t "$image" .
test "$(docker image inspect --format '{{.Architecture}}' "$image")" = "$EXPECTED_ARCH"
docker image inspect --format '{{.Id}} {{.Os}}/{{.Architecture}} user={{.Config.User}} entrypoint={{json .Config.Entrypoint}}' "$image" > build/tls/image.txt
printf '%s\n' "${GITHUB_SHA:-local}" > build/tls/source-sha.txt
# Do not start the scraper, mount app data, or pass credentials/environment files.
docker run --rm -i --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --mount "type=bind,src=$PWD/ci,dst=/qualification,readonly" \
  --entrypoint python "$image" /qualification/tls_probe.py > build/tls/aiohttp.json
