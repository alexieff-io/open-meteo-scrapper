"""Qualify the image's installed aiohttp and OS trust without application secrets.

Only issuer-chain verification failures prove rejection. Expiry, hostname,
DNS, timeouts, and other errors fail the gate rather than masquerading as distrust.
A response to the probe's GET that aiohttp read but could not parse proves the handshake
accepted the chain, so it counts as accepted: it passes a control host and fails an eMudhra host.
Transport failures alone get a fresh attempt (MAX_ATTEMPTS in all) and still fail
the gate if they persist; any verdict or identity failure is final.
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
import time
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
# A live connection can stall (seen once in an emulated ARM64 build); retry only what proved nothing.
MAX_ATTEMPTS = 3
RETRY_DELAYS = (2, 5)


def classify(error):
    pending, seen = [error], set()
    network = tls = False
    while pending and len(seen) < 32:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        code = getattr(current, 'verify_code', None)
        if isinstance(current, ssl.SSLCertVerificationError) and code is not None:
            return {'result': 'certificate-rejected' if code in (20, 21, 27)
                    else 'inconclusive', 'verifyCode': code}
        network = network or isinstance(current, OSError)
        tls = tls or isinstance(current, ssl.SSLError)
        pending.extend(getattr(current, name, None) for name in
                       ('__cause__', '__context__', 'reason', 'certificate_error'))
        pending.extend(current.args)
    # Timeouts, DNS and refused/reset connections are OSErrors; ssl.SSLError is one too,
    # so any TLS-layer error anywhere in the chain keeps the attempt final.
    return {'result': 'inconclusive', 'errorType': type(error).__name__,
            'transport': network and not tls}


def unparseable_response(error):
    # aiohttp raises exactly ClientResponseError from a parser error only in ClientResponse.start().
    # For the probe's GET that is on bytes read over the verified connection; a proxy's reply to
    # CONNECT is parsed there too, but before any handshake, so only a GET counts.
    # Its subclasses and raise_for_status() never match.
    from aiohttp import ClientResponseError
    from aiohttp.http_exceptions import HttpProcessingError
    if type(error) is not ClientResponseError or not isinstance(error.__cause__, HttpProcessingError):
        return None
    if getattr(error.request_info, 'method', None) != 'GET':
        return None
    # The 400 is aiohttp's own code, not the server's, so record the parser's error instead.
    root = error.__cause__.__cause__ or error.__cause__
    return {'result': 'accepted', 'httpParseError': f'{type(root).__name__}: {error.message}'[:200]}


def retryable(row):
    # Only attempts that never reached a verdict: a transport failure, or a bad host whose
    # diagnostic capture saw no chain at all. Certificate verdicts, other verify codes,
    # a served chain that differs from the pin, and the validity window are final.
    if row['result'] != 'inconclusive':
        return False
    return row.get('chainSha256') == [] or row.get('transport') is True


async def with_retries(attempt, host, sleep=asyncio.sleep):
    retried = []
    for number in range(1, MAX_ATTEMPTS + 1):
        # Each attempt redoes the whole host procedure, so the kept row is one attempt's.
        row = await attempt(host)
        if number == MAX_ATTEMPTS or not retryable(row):
            return {**row, 'attempts': number, **({'retried': retried} if retried else {})}
        retried.append({key: value for key, value in row.items() if key != 'host'})
        await sleep(RETRY_DELAYS[number - 1])


def validate(rows):
    expected = {h: 'accepted' for h in GOOD_HOSTS}
    expected.update({h: 'certificate-rejected' for h in BAD_HOSTS})
    return (len(rows) == len(expected)
            and {row['host'] for row in rows} == set(expected)
            and all(row['result'] == expected[row['host']] for row in rows)
            and all(row.get('chainMatchesArchive') is True
                    for row in rows if row['host'] in BAD_HOSTS))


def valid_at(window, now):
    return (datetime.datetime.fromisoformat(window['notBefore']) <= now
            < datetime.datetime.fromisoformat(window['notAfter']))


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
    windows = json.loads(Path(__file__).with_name("chain-validity.json").read_text())
    if set(expected_chains) != set(BAD_HOSTS) or set(windows) != set(BAD_HOSTS):
        raise ValueError("Pinned chain cohort must match all 14 hosts")
    gate = asyncio.Semaphore(4)
    # Same default verification as scraper.collector and scraper.exporter.
    # No ssl=False, custom CA, proxy environment, or application credentials.
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
        async def one(host):
            identity = {}
            if host in BAD_HOSTS:
                if not valid_at(windows[host], datetime.datetime.now(datetime.timezone.utc)):
                    return {'host': host, 'result': 'inconclusive', 'reason': 'pinned-chain-outside-validity'}
                observed = await identify_chain(host)
                identity = {'chainSha256': observed,
                            'chainMatchesArchive': observed == expected_chains[host]}
                if not identity['chainMatchesArchive']:
                    return {'host': host, 'result': 'inconclusive', **identity}
            try:
                async with session.get('https://' + host + '/', allow_redirects=False) as response:
                    return {'host': host, 'result': 'accepted', 'status': response.status, **identity}
            except Exception as error:
                return {'host': host, **(unparseable_response(error) or classify(error)), **identity}

        async def attempt(host):
            async with gate:
                started = time.monotonic()
                row = await one(host)
            return {**row, 'seconds': round(time.monotonic() - started, 1)}
        return await asyncio.gather(*(with_retries(attempt, host) for host in GOOD_HOSTS + BAD_HOSTS))


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
