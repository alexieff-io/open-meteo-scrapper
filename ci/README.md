# Image trust qualification

The scraper uses aiohttp's default SSL verification backed by the Debian OS trust
store. The image preserves Python 3.12.8, pins its multi-architecture Bookworm
base to `sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0`,
and installs Debian `ca-certificates=20250419~deb12u2`. Package installation fails
closed if that exact version is no longer available. Do not silently substitute
another trust store or disable certificate verification.

Both native AMD64 and ARM64 CI jobs build the image, then run its installed
aiohttp as the existing non-root user with no scraper startup, application data,
credentials, or TLS overrides. All 14 eMudhra/emSign test hosts must fail with an
issuer-chain verification error, while Cloudflare and Open-Meteo must complete
TLS. HTTP status is irrelevant to the TLS control. DNS, expiry, hostname, and
network failures are inconclusive and fail CI. The receipt records runtime,
aiohttp version, CA package, bundle hash, source SHA, and local image identity.

The public endpoint cohort is documented by
https://github.com/galadran/we-have-a-problem.com/tree/24ec06bbc38cedd378ded691610eb6dd4d9d3c6a
and its `chains.zip` has SHA-256
`7b949956955fc501a30f4d98d2f513d34c0bbbe92e29a968194c96e097ce2345`.
The archive was downloaded and its SHA-256 verified before deriving the ordered
DER certificate fingerprints in `chain-fingerprints.json`. For each bad host,
a diagnostic OpenSSL handshake captures the complete served chain (no HTTP or
application data); every certificate must match those pinned fingerprints before
aiohttp's issuer rejection can count. Missing, added, or rotated certificates
fail closed. This diagnostic capture is not itself a trust result. The following
aiohttp request always retains default verification.

These are live handshake tests with pinned chain identity, not offline archive
verification. The identity capture and aiohttp test are separate connections;
remote certificates can rotate. Any mismatch/inconclusive result requires
investigation, not relaxation of the gate.

Run offline tests with `pip install -c ci/test-constraints.txt -e '.[test]'`
then `pytest --cov=scraper --cov-fail-under=80`. The test-only constraint avoids
aioresponses 0.7.9's incompatibility with aiohttp 3.14's new `stream_writer`
constructor argument. Production requirements remain unchanged, and image TLS
qualification uses the image's actual resolved aiohttp, not the test constraint.

Run image qualification with `EXPECTED_ARCH=amd64 bash ci/qualify-image.sh`
(or arm64 on a native ARM64 machine). Docker and outbound HTTPS are required.

## Publication boundary

Pull requests build and qualify only: no registry login, image push, or chart
publication. The existing build job depends on both native qualification jobs. Additionally,
the final Dockerfile build step runs the same gate inside the exact runtime being
built for publication. Both CI build paths disable the build cache, so publication
cannot reuse a previous live-check layer or push an independently rebuilt,
untested runtime. The diagnostic files are build-time mounts only.
Merging to `main` still invokes the existing image publisher at
`ghcr.io/alexieff-io/open-meteo-scrapper`; that destination was already anonymously
readable as of 2026-10-22. This change does not alter its visibility or credentials.
Review and merge remain owner actions. No release tag or chart change is part
of this fix. The existing `v*` tag workflow also publishes Helm charts, so do not
create a tag for an image-only update.
