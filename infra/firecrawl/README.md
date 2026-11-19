# Local destination-provenance fix

The installed Firecrawl build `7666c1f9ae8720a6bba271e0f60b6a217f8a5210` has a Playwright adapter that returns the requested URL rather than the browser's destination. This prevents trusted-origin policy verification after redirects. These small derivative images fix that dependency without changing the existing deployment's ports, environment, storage, network or security settings.

The browser captures `page.url()` beside the page content, then requires the destination to remain stable and match the navigation response URL. It returns explicit `finalUrlVerified`. JavaScript navigation that no longer matches that response fails closed. The API consumes the validated browser URL and reserves `metadata.finalUrlVerified` as a boolean **for every engine**, before page metadata is merged. Known network-fetch results also report provenance; cached/index/other engine results default false. A page-authored metadata field cannot enable the flag. It indicates observed destination provenance, never official company identity or policy applicability.

The Python `inspect_page` adapter requires the reserved flag and a valid public HTTPS destination; it never falls back to sourceURL, OG/canonical tags or the request URL to claim verification. Source identity must separately satisfy the policy service's owner-confirmed origin and link-evidence rules.

## Rebuild and apply

Prerequisite: the existing local images below are already installed. The build script checks their exact IDs, makes local base tags, applies exact SHA-256-guarded source replacements, and runs Node syntax/contract checks. It downloads no new source or packages. Unexpected source bytes/build revision stop the build.

```sh
infra/firecrawl/build.sh
export PERSONAL_AGENT_FIRECRAWL_CONTEXT="$PWD/infra/firecrawl"
docker compose \
  -f /Users/work/Depository/grievance-agent/compose.firecrawl.yaml \
  -f "$PERSONAL_AGENT_FIRECRAWL_CONTEXT/compose.provenance.yaml" \
  up -d --no-deps --no-build api browser
```

Only API and browser are recreated. The first compose file remains untouched. This repository's override must be supplied again on later updates, otherwise the original image definitions take effect.

Original image IDs retained for rollback:

- API: `sha256:1b223ef18d410f67615f32ccaa3dcedfc26af4286f096247bddca3b624545e63`
- Browser: `sha256:a0b7c31f463fdf692fa5fde751d06475ced4bbf6a93a8268f4ea859d8f05802b`

## Roll back

```sh
docker compose \
  -f /Users/work/Depository/grievance-agent/compose.firecrawl.yaml \
  up -d --no-deps --no-build api browser
```

The original browser image/tag and API digest remain installed. Rollback removes final-URL provenance support; policy inspection then remains unverified and factual drafting remains available. Do not treat rollback as preserving verified research.

## Boundaries

- Existing DNS/private-address navigation protection is unchanged. This patch does not broaden destinations or introduce a host port.
- A stable browser final URL and matching navigation response are checked at capture time; an untrusted page can change after capture, as with any scrape.
- `finalUrlVerified` defaults false for engines outside the patched browser and verified network-fetch paths; unknown/cached content cannot silently gain authority.
- The build-time test exercises the installed metadata merge with a spoofed page value and proves reserved false remains false. The navigation helper tests stable, changing, mismatched and non-HTTPS observations.
- `read_page` keeps its existing public research behavior. `inspect_page` requests full-page Markdown and links with `maxAge=0`, caps source content at 128,000 characters and links at 200, and reports truncation explicitly.

## Observed validation on 19 November 2026

The actual `inspect_page` adapter observed:

| Request | Observed destination | Page status | Provenance |
| --- | --- | --- | --- |
| `https://scrapethissite.com/` | `https://www.scrapethissite.com/` | 200 | verified |
| `https://firecrawl.dev` | `https://www.firecrawl.dev/` | 200 | verified |

The original backend had echoed the requested apex URL for both sites. After the fix, the official homepage returned 36,790 Markdown characters and 100 links, without truncation. This verifies these deployed redirect paths, not every possible navigation or scraping engine. Owner confirmation of an apex origin does not automatically authorize the distinct `www` origin.

Secret-free hashes of runtime settings before and after recreation matched for environment, ports, mounts, networks and security configuration. Redis, RabbitMQ and PostgreSQL services were not recreated.
