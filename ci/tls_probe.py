"""Qualify the image's installed aiohttp and OS trust without application secrets.

Only issuer-chain verification failures prove rejection. Expiry, hostname,
DNS, timeouts, and other errors fail the gate rather than masquerading as distrust.
The endpoints are public live peers, not pinned certificate archive fixtures.
"""
import asyncio
import datetime
import hashlib
import importlib.metadata
import json
import os
import re
import ssl
import subprocess
import sys
from pathlib import Path

GOOD_HOSTS = ('www.cloudflare.com', 'api.open-meteo.com')
BAD_HOSTS = tuple(
    f'{kind}-{generation}{cross}-emudhra.we-have-a-problem.com'
    for kind in ('dv', 'ov', 'ev')
    for generation in ('g3b', 'g3c')
    for cross in ('', '-aaa')
) + ('dv-g3b-noct-emudhra.we-have-a-problem.com',
     'dv-g3b-aaa-noct-emudhra.we-have-a-problem.com')
CA_VERSION = '20250419~deb12u2'


def classify(error):
    pending, seen = [error], set()
    while pending and len(seen) < 32:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        code = getattr(current, 'verify_code', None)
        if isinstance(current, ssl.SSLCertVerificationError) and code is not None:
            return {'result': 'certificate-rejected' if code in (20, 21, 27)
                    else 'inconclusive', 'verifyCode': code}
        pending.extend(getattr(current, name, None) for name in
                       ('__cause__', '__context__', 'reason', 'certificate_error'))
        pending.extend(current.args)
    return {'result': 'inconclusive', 'errorType': type(error).__name__}


def validate(rows):
    expected = {h: 'accepted' for h in GOOD_HOSTS}
    expected.update({h: 'certificate-rejected' for h in BAD_HOSTS})
    return (len(rows) == len(expected)
            and {row['host'] for row in rows} == set(expected)
            and all(row['result'] == expected[row['host']] for row in rows)
            and all(row.get('chainMatchesArchive') is True
                    for row in rows if row['host'] in BAD_HOSTS))


def chain_fingerprints(output):
    certificates = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',
                              output, re.DOTALL)
    return [hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest()
            for cert in certificates]


async def identify_chain(host):
    # Diagnostic-only handshake: no HTTP request, credentials, or application data.
    # s_client can display a rejected peer's full chain; this is NOT a trust pass.
    process = await asyncio.create_subprocess_exec(
        'openssl', 's_client', '-connect', host + ':443', '-servername', host,
        '-showcerts', stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        return chain_fingerprints(output.decode())
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return []


async def probe():
    import aiohttp
    expected_chains = json.loads(Path(__file__).with_name("chain-fingerprints.json").read_text())
    if set(expected_chains) != set(BAD_HOSTS):
        raise ValueError("Pinned chain cohort must match all 14 hosts")
    gate = asyncio.Semaphore(4)
    # Same default verification as scraper.collector and scraper.exporter.
    # No ssl=False, custom CA, proxy environment, or application credentials.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async def one(host):
            async with gate:
                identity = {}
                if host in BAD_HOSTS:
                    observed = await identify_chain(host)
                    identity = {'chainSha256': observed,
                                'chainMatchesArchive': observed == expected_chains[host]}
                    if not identity['chainMatchesArchive']:
                        return {'host': host, 'result': 'inconclusive', **identity}
                try:
                    async with session.get('https://' + host + '/', allow_redirects=False) as response:
                        return {'host': host, 'result': 'accepted', 'status': response.status, **identity}
                except Exception as error:
                    return {'host': host, **classify(error), **identity}
        return await asyncio.gather(*(one(host) for host in GOOD_HOSTS + BAD_HOSTS))


def main():
    overrides = [key for key in ('SSL_CERT_FILE', 'SSL_CERT_DIR', 'PYTHONHTTPSVERIFY')
                 if key in os.environ]
    ca_version = subprocess.check_output(
        ['dpkg-query', '-W', '-f=${Version}', 'ca-certificates'], text=True)
    bundle = ssl.get_default_verify_paths().cafile
    rows = asyncio.run(probe())
    receipt = {'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
               'python': sys.version, 'openssl': ssl.OPENSSL_VERSION,
               'aiohttp': importlib.metadata.version('aiohttp'),
               'caVersion': ca_version, 'bundle': bundle,
               'bundleSha256': hashlib.sha256(Path(bundle).read_bytes()).hexdigest(),
               'overrideKeys': overrides, 'kind': 'live-default-aiohttp-handshakes',
               'results': rows}
    print(json.dumps(receipt, indent=2))
    return 0 if not overrides and ca_version == CA_VERSION and validate(rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
