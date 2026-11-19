#!/bin/sh
set -eu
PERSONAL_AGENT_FIRECRAWL_CONTEXT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export PERSONAL_AGENT_FIRECRAWL_CONTEXT
browser_base='sha256:a0b7c31f463fdf692fa5fde751d06475ced4bbf6a93a8268f4ea859d8f05802b'
api_base='sha256:1b223ef18d410f67615f32ccaa3dcedfc26af4286f096247bddca3b624545e63'
test "$(docker image inspect "$browser_base" --format '{{.Id}}')" = "$browser_base"
test "$(docker image inspect "$api_base" --format '{{.Id}}')" = "$api_base"
docker tag "$api_base" personal-agent-firecrawl-api-base:1b223ef18d410f67615f32ccaa3dcedfc26af4286f096247bddca3b624545e63
docker tag "$browser_base" personal-agent-firecrawl-browser-base:a0b7c31f463fdf692fa5fde751d06475ced4bbf6a93a8268f4ea859d8f05802b
docker build --pull=false -f "$PERSONAL_AGENT_FIRECRAWL_CONTEXT/api.Dockerfile" -t personal-agent-firecrawl-api:provenance-v1 "$PERSONAL_AGENT_FIRECRAWL_CONTEXT"
docker build --pull=false -f "$PERSONAL_AGENT_FIRECRAWL_CONTEXT/browser.Dockerfile" -t personal-agent-firecrawl-browser:provenance-v1 "$PERSONAL_AGENT_FIRECRAWL_CONTEXT"
