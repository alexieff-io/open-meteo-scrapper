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
TLS. HTTP status is irrelevant to the TLS control. So is a response to the
probe's GET that aiohttp cannot parse, such as an oversized header: aiohttp only
parses that response after verifying the connection, so the result counts as
accepted and the receipt records the parse error. On an eMudhra host the same
result fails CI, because a distrusted chain was accepted. A proxy's unparseable
reply to CONNECT arrives before any handshake, so it proves nothing. Expiry and
hostname failures are inconclusive and fail CI; DNS and network failures are inconclusive and fail
CI if they persist. Because a single live connection can stall (seen once in an
emulated ARM64 build), transport-only failures get a fresh attempt: a timeout,
DNS or connection error with no TLS error anywhere in its exception
chain, or a diagnostic capture that saw no chain at all. A host gets at most 3
attempts (after 2s, then 5s) and still fails CI if the failure persists, which
takes about 6 minutes at worst. Certificate verdicts, other TLS errors, chain
mismatches and validity-window failures are final and never retried, and every
host must still end with its expected result. The receipt records runtime,
aiohttp version, CA package, bundle hash, source SHA, and local image identity,
plus each host's attempts, elapsed seconds, any retried results and any HTTP
parse error.

The public endpoint cohort is documented by
https://github.com/galadran/we-have-a-problem.com/tree/24ec06bbc38cedd378ded691610eb6dd4d9d3c6a
and its `chains.zip` has SHA-256
`7b949956955fc501a30f4d98d2f513d34c0bbbe92e29a968194c96e097ce2345`.
The archive was downloaded and its SHA-256 verified before deriving the ordered
DER certificate fingerprints in `chain-fingerprints.json`. For each bad host,
a diagnostic OpenSSL handshake captures the complete served chain (no HTTP or
application data); every certificate must match those pinned fingerprints before
aiohttp's issuer rejection can count. Missing, added, or rotated certificates
fail closed. Pinned leaf hostnames were independently checked against their
certificates; the per-host validity window in `chain-validity.json` is the
intersection of all certificate validity intervals in that complete chain.
The probe checks that window explicitly, since issuer errors can otherwise mask
expired/not-yet-valid certificates. This diagnostic capture is not itself a trust result. The following
aiohttp request always retains default verification.

These are live handshake tests with pinned chain identity, not offline archive
verification. The identity capture and aiohttp test are separate connections;
remote certificates can rotate. Any mismatch/inconclusive result requires
investigation, not relaxation of the gate. Retried attempts stay in the receipt
(the `tls-qualification-*` artifact for the native jobs, the build log for the
in-image gate), so a run that passed only after a retry can still be investigated.

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
Pushes to `main` publish the image to `ghcr.io/alexieff-io/open-meteo-scrapper`
(tags `main` and `sha-<short>`); the package was anonymously readable as of
2026-10-22. A `v*` tag publishes the versioned image tags and `latest`, and also
packages and pushes the Helm chart, so an image-only change needs no tag.
