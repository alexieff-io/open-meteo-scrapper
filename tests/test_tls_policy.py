"""Offline regression tests for the live image qualification's fail-closed rules."""
import asyncio
import datetime
import json
from pathlib import Path
import socket
import ssl

import aiohttp
from aiohttp.http_exceptions import BadHttpMessage, HttpProcessingError
from multidict import CIMultiDict, CIMultiDictProxy
import pytest
from yarl import URL
from ci.tls_probe import (BAD_HOSTS, GOOD_HOSTS, MAX_ATTEMPTS, RETRY_DELAYS, classify,
                          retryable, unparseable_response, validate, valid_at, with_retries)


def test_exact_public_cohort():
    assert len(BAD_HOSTS) == len(set(BAD_HOSTS)) == 14
    assert len(GOOD_HOSTS) >= 2
    assert not set(BAD_HOSTS) & set(GOOD_HOSTS)


@pytest.mark.parametrize('code', [20, 21, 27])
def test_only_issuer_rejections_count(code):
    error = ssl.SSLCertVerificationError('verification failed')
    error.verify_code = code
    assert classify(error)['result'] == 'certificate-rejected'


@pytest.mark.parametrize('code', [9, 10, 18, 62])
def test_other_certificate_errors_are_inconclusive(code):
    error = ssl.SSLCertVerificationError('verification failed')
    error.verify_code = code
    assert classify(error)['result'] == 'inconclusive'


@pytest.mark.parametrize('error', [TimeoutError(), OSError(), ValueError('certificate verify failed')])
def test_network_or_text_is_not_distrust(error):
    assert classify(error)['result'] == 'inconclusive'


def test_aiohttp_wrapper_is_unwrapped():
    error = ssl.SSLCertVerificationError('verification failed')
    error.verify_code = 20
    wrapper = Exception()
    wrapper.certificate_error = error
    assert classify(wrapper)['result'] == 'certificate-rejected'


def good_receipt():
    return ([{'host': h, 'result': 'accepted'} for h in GOOD_HOSTS]
            + [{'host': h, 'result': 'certificate-rejected', 'chainMatchesArchive': True} for h in BAD_HOSTS])


def test_complete_receipt_passes():
    assert validate(good_receipt())


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'accepted', 'inconclusive', 'bad-control'])
def test_incomplete_or_wrong_receipt_fails(mutation):
    rows = good_receipt()
    if mutation == 'missing': rows.pop()
    if mutation == 'duplicate': rows[-1] = rows[-2]
    if mutation in ('accepted', 'inconclusive'): rows[-1]['result'] = mutation
    if mutation == 'bad-control': rows[0]['result'] = 'certificate-rejected'
    assert not validate(rows)


def test_wrong_or_missing_chain_identity_fails():
    rows = good_receipt()
    rows[-1]['chainMatchesArchive'] = False
    assert not validate(rows)
    del rows[-1]['chainMatchesArchive']
    assert not validate(rows)


def test_pinned_complete_chain_inventory():
    chains = json.loads(Path('ci/chain-fingerprints.json').read_text())
    assert set(chains) == set(BAD_HOSTS)
    for host, fingerprints in chains.items():
        assert len(fingerprints) == (2 if '-aaa-' in host else 3)
        assert all(len(value) == 64 and set(value) <= set('0123456789abcdef')
                   for value in fingerprints)


def test_validity_guard_does_not_let_issuer_failure_mask_expiry():
    window = {'notBefore': '2026-10-20T00:00:00+00:00',
              'notAfter': '2027-05-03T00:00:00+00:00'}
    def date(value):
        return datetime.datetime.fromisoformat(value + 'T00:00:00+00:00')
    assert valid_at(window, date('2026-10-22'))
    assert not valid_at(window, date('2026-10-19'))
    assert not valid_at(window, date('2027-05-03'))
    assert not valid_at(window, date('2030-01-01'))


def test_pinned_validity_inventory():
    windows = json.loads(Path('ci/chain-validity.json').read_text())
    assert set(windows) == set(BAD_HOSTS)
    for window in windows.values():
        assert datetime.datetime.fromisoformat(window['notBefore']) < datetime.datetime.fromisoformat(window['notAfter'])


def wrapped(cause, outer=None):
    outer = outer or Exception('request failed')
    outer.__cause__ = cause
    return outer


@pytest.mark.parametrize('error', [TimeoutError(), ConnectionResetError(),
                                   wrapped(socket.gaierror(-2, 'Name or service not known'))])
def test_transport_failures_are_marked(error):
    assert classify(error) == {'result': 'inconclusive', 'errorType': type(error).__name__, 'transport': True}


@pytest.mark.parametrize('error', [wrapped(ssl.SSLError(1, 'sslv3 alert handshake failure'), OSError('connect failed')),
                                   ValueError('certificate verify failed'), Exception('server disconnected')])
def test_tls_layer_and_unknown_failures_are_not_transport(error):
    assert classify(error)['transport'] is False


def parser_failure(kind=aiohttp.ClientResponseError, method='GET'):
    # Shaped like aiohttp's ClientResponse.start(): the parser's error, copied, then wrapped.
    copied = HttpProcessingError(code=400, message='Too many headers received')
    copied.__cause__ = BadHttpMessage('Too many headers received')
    url = URL('https://' + GOOD_HOSTS[0] + '/')
    info = aiohttp.RequestInfo(url, method, CIMultiDictProxy(CIMultiDict()), url)
    error = kind(info, (), status=400, message='Too many headers received')
    error.__cause__ = copied
    return error


def test_unparseable_response_proves_the_handshake():
    assert unparseable_response(parser_failure()) == {
        'result': 'accepted', 'httpParseError': 'BadHttpMessage: Too many headers received'}


async def test_real_aiohttp_parse_failure_is_recognised():
    # Plain HTTP on loopback: ClientResponse.start() raises the same way after a TLS handshake.
    async def respond(reader, writer):
        await reader.readuntil(b'\r\n\r\n')
        writer.write(b'HTTP/1.1 200 OK\r\nX-Big: ' + b'a' * 9000 + b'\r\n\r\n')
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(respond, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    async with server, aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientResponseError) as caught:
            async with session.get(f'http://127.0.0.1:{port}/'):
                pass
    row = unparseable_response(caught.value)
    assert row['result'] == 'accepted' and row['httpParseError'].startswith('LineTooLong: ')
    assert len(row['httpParseError']) <= 200


async def test_real_proxy_connect_parse_failure_proves_nothing():
    # A proxy's reply to CONNECT goes through the same ClientResponse.start(), but before any
    # TLS handshake with the host, so a garbled one must not count as accepted.
    async def respond(reader, writer):
        await reader.readuntil(b'\r\n\r\n')
        writer.write(b'HTTP/1.1 200 OK\r\nX-Big: ' + b'a' * 9000 + b'\r\n\r\n')
        await writer.drain()
        writer.close()
    server = await asyncio.start_server(respond, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    async with server, aiohttp.ClientSession() as session:
        with pytest.raises(aiohttp.ClientResponseError) as caught:
            async with session.get('https://' + GOOD_HOSTS[0] + '/', proxy=f'http://127.0.0.1:{port}'):
                pass
    assert type(caught.value) is aiohttp.ClientResponseError
    assert caught.value.request_info.method == 'CONNECT'
    assert unparseable_response(caught.value) is None


@pytest.mark.parametrize('error', [
    aiohttp.ClientResponseError(None, (), status=503, message='Service Unavailable'),
    parser_failure(aiohttp.TooManyRedirects),
    parser_failure(aiohttp.ClientHttpProxyError),
    parser_failure(aiohttp.ContentTypeError),
    parser_failure(aiohttp.WSServerHandshakeError),
    parser_failure(method='CONNECT'),
    parser_failure().__cause__,
], ids=['no-parser-cause', 'redirects', 'proxy', 'content-type', 'websocket', 'proxy-connect', 'unwrapped'])
def test_other_http_errors_prove_nothing(error):
    assert unparseable_response(error) is None
    assert classify(error) == {'result': 'inconclusive', 'errorType': type(error).__name__, 'transport': False}


def test_unparseable_response_passes_a_control_but_fails_a_distrusted_chain():
    evidence = unparseable_response(parser_failure())
    rows = good_receipt()
    rows[0] = {'host': rows[0]['host'], **evidence}
    assert validate(rows)
    rows[-1] = {'host': rows[-1]['host'], **evidence, 'chainMatchesArchive': True}
    assert not validate(rows)


CHAIN = ['a' * 64, 'b' * 64]
TIMEOUT = {'result': 'inconclusive', 'errorType': 'TimeoutError', 'transport': True}


@pytest.mark.parametrize('row', [
    TIMEOUT,
    {'result': 'inconclusive', 'chainSha256': [], 'chainMatchesArchive': False},
    {**TIMEOUT, 'chainSha256': CHAIN, 'chainMatchesArchive': True},
], ids=['timeout', 'no-chain-captured', 'get-timeout-after-chain-match'])
def test_unproven_transport_attempts_are_retryable(row):
    assert retryable(row)


@pytest.mark.parametrize('row', [
    {'result': 'accepted', 'status': 200},
    {'result': 'certificate-rejected', 'verifyCode': 20, 'chainSha256': CHAIN, 'chainMatchesArchive': True},
    {'result': 'inconclusive', 'verifyCode': 10},
    {'result': 'inconclusive', 'verifyCode': 62},
    {'result': 'inconclusive', 'chainSha256': CHAIN, 'chainMatchesArchive': False},
    {'result': 'inconclusive', 'reason': 'pinned-chain-outside-validity'},
    {'result': 'inconclusive', 'errorType': 'OSError', 'transport': False},
    {'result': 'accepted', 'httpParseError': 'LineTooLong: Got more than 8190 bytes'},
], ids=['accepted', 'rejected', 'expired', 'hostname', 'chain-mismatch', 'validity-window', 'tls-layer',
        'unparseable-response'])
def test_verdicts_and_identity_failures_are_final(row):
    assert not retryable(row)


def scripted(*rows):
    calls = []
    async def attempt(host):
        calls.append(host)
        return {'host': host, **rows[len(calls) - 1]}
    return attempt, calls


def recorder():
    delays = []
    async def sleep(seconds):
        delays.append(seconds)
    return sleep, delays


@pytest.mark.parametrize('host, verdict', [(BAD_HOSTS[0], 'accepted'), (GOOD_HOSTS[0], 'certificate-rejected')])
async def test_wrong_verdict_is_never_retried(host, verdict):
    attempt, calls = scripted({'result': verdict}, {'result': 'unused'})
    sleep, delays = recorder()
    row = await with_retries(attempt, host, sleep)
    assert (row['result'], row['attempts'], calls, delays) == (verdict, 1, [host], [])
    assert 'retried' not in row


async def test_transport_failure_is_retried_on_a_fresh_attempt():
    host = GOOD_HOSTS[1]
    attempt, _ = scripted(TIMEOUT, {'result': 'accepted', 'status': 400})
    sleep, delays = recorder()
    row = await with_retries(attempt, host, sleep)
    assert row == {'host': host, 'result': 'accepted', 'status': 400, 'attempts': 2, 'retried': [TIMEOUT]}
    assert delays == [RETRY_DELAYS[0]]


async def test_final_row_comes_from_one_attempt():
    host = BAD_HOSTS[0]
    mismatch = {'result': 'inconclusive', 'chainSha256': ['c' * 64], 'chainMatchesArchive': False}
    attempt, calls = scripted({**TIMEOUT, 'chainSha256': CHAIN, 'chainMatchesArchive': True}, mismatch, {'result': 'unused'})
    sleep, _ = recorder()
    row = await with_retries(attempt, host, sleep)
    assert {k: v for k, v in row.items() if k != 'retried'} == {'host': host, **mismatch, 'attempts': 2}
    assert len(calls) == 2


async def test_persistent_transport_failure_still_fails_the_gate():
    host = GOOD_HOSTS[1]
    attempt, calls = scripted(*[TIMEOUT] * (MAX_ATTEMPTS + 1))
    sleep, delays = recorder()
    row = await with_retries(attempt, host, sleep)
    assert (row['result'], row['attempts'], len(row['retried']), len(calls)) == (
        'inconclusive', MAX_ATTEMPTS, MAX_ATTEMPTS - 1, MAX_ATTEMPTS)
    assert delays == list(RETRY_DELAYS)
    assert not validate([row if r['host'] == host else r for r in good_receipt()])


def test_receipt_with_retry_evidence_still_validates():
    assert validate([{**row, 'attempts': 2, 'retried': [TIMEOUT], 'seconds': 1.5} for row in good_receipt()])
