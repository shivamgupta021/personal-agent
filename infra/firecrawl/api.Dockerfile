# build.sh creates this local tag only from the exact original image ID.
FROM personal-agent-firecrawl-api-base:1b223ef18d410f67615f32ccaa3dcedfc26af4286f096247bddca3b624545e63
COPY patch.cjs provenance.cjs verify.cjs /opt/personal-agent/
RUN node /opt/personal-agent/patch.cjs api && node /opt/personal-agent/verify.cjs
