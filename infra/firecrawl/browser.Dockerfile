# build.sh creates this local tag only from the exact original image ID.
FROM personal-agent-firecrawl-browser-base:a0b7c31f463fdf692fa5fde751d06475ced4bbf6a93a8268f4ea859d8f05802b
USER root
COPY patch.cjs provenance.cjs verify.cjs /opt/personal-agent/
RUN node /opt/personal-agent/patch.cjs browser && node /opt/personal-agent/verify.cjs
USER node
