"""Offline regression tests for the live image qualification's fail-closed rules."""
import json
from pathlib import Path
import ssl

import pytest
from ci.tls_probe import BAD_HOSTS, GOOD_HOSTS, classify, validate


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
