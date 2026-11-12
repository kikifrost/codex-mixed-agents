"""Independent spec tests; synthetic credentials and mocked HTTPS only."""
import importlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugins/mixed-agents/bridge'))


def quota(used=6, remaining=94, duration=300, unit='TIME_UNIT_MINUTE'):
    detail = {'limit': '100', 'used': str(used), 'remaining': str(remaining),
              'resetTime': '2026-10-11T23:00:00Z'}
    return {'limits': [{'window': {'duration': str(duration), 'timeUnit': unit},
                        'detail': detail}]}


class KimiQuotaTests(unittest.TestCase):
    def setUp(self):
        self.kimi = importlib.import_module('kimi_usage')

    def test_real_shape_string_numerals_and_equivalent_five_hour_windows(self):
        for duration, unit in ((300, 'TIME_UNIT_MINUTE'), (5, 'TIME_UNIT_HOUR'),
                               (18000, 'TIME_UNIT_SECOND')):
            with self.subTest(unit=unit):
                result = self.kimi.parse_usage(quota(duration=duration, unit=unit))
                self.assertTrue(result['available'])
                self.assertAlmostEqual(result['remaining_percent'], 94)
                self.assertEqual(result['reset_at'], '2026-10-11T23:00:00Z')

    def test_used_only_and_remaining_only_and_conservative_multiple_limits(self):
        for missing in ('used', 'remaining'):
            payload = quota()
            del payload['limits'][0]['detail'][missing]
            self.assertAlmostEqual(self.kimi.parse_usage(payload)['remaining_percent'], 94)
        payload = quota()
        payload['limits'].extend(quota(96, 4)['limits'])
        self.assertAlmostEqual(self.kimi.parse_usage(payload)['remaining_percent'], 4)
        payload = quota(90, 10)
        larger = quota()['limits'][0]
        larger['detail'].update(limit='1000', used='200', remaining='800')
        payload['limits'].append(larger)
        self.assertAlmostEqual(self.kimi.parse_usage(payload)['remaining_percent'], 10)

    def test_weekly_totals_and_unproven_windows_never_authorize(self):
        for duration, unit in ((10080, 'TIME_UNIT_MINUTE'), (168, 'TIME_UNIT_HOUR'),
                               (300, 'TOKEN'), (300, 'NOT_A_MINUTE_UNIT'), (5, 'TIME_UNIT_MINUTE')):
            self.assertFalse(self.kimi.parse_usage(quota(duration=duration, unit=unit))['available'])
        payload = quota()
        del payload['limits'][0]['window']
        payload['usage'] = {'limit': 100, 'remaining': 99}
        self.assertFalse(self.kimi.parse_usage(payload)['available'])

    def test_invalid_or_incoherent_quantities_fail_closed(self):
        for field, value in [('limit', 0), ('limit', True), ('limit', 'Infinity'),
                             ('used', -1), ('used', 'NaN'), ('used', 101),
                             ('remaining', -1), ('remaining', 101),
                             ('remaining', None), ('remaining', 90)]:
            with self.subTest(field=field, value=value):
                payload = quota()
                payload['limits'][0]['detail'][field] = value
                self.assertFalse(self.kimi.parse_usage(payload)['available'])

    def test_guard_defaults_and_exact_boundary(self):
        default = self.kimi.normalize_policy({})
        self.assertEqual(default, {'usage_guard': False, 'stop_remaining_percent': 5,
                                   'probe_on_unknown': False, 'probe_cooldown_minutes': 15})
        self.assertIsNone(self.kimi.block_reason({}, default))
        enabled = self.kimi.normalize_policy({'usage_guard': True})
        for remaining in (0, 4.99, 5):
            self.assertEqual(self.kimi.block_reason({'available': True, 'remaining_percent': remaining}, enabled),
                             'provider_quota_reserve')
        self.assertIsNone(self.kimi.block_reason({'available': True, 'remaining_percent': 5.001}, enabled))
        for remaining in (None, True, float('nan'), float('inf'), -1, 101):
            self.assertEqual(self.kimi.block_reason({'available': True, 'remaining_percent': remaining}, enabled),
                             'provider_usage_unavailable')


class KimiProbeAllowanceTests(unittest.TestCase):
    def test_unexpired_claims_survive_more_than_sixty_four_accounts(self):
        kimi = importlib.import_module('kimi_usage')
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            for index in range(65):
                with patch('time.time', return_value=1800000000 + index):
                    self.assertTrue(kimi.claim_probe(state, f'synthetic-account-{index}')['claimed'])
            with patch('time.time', return_value=1800000100):
                self.assertFalse(kimi.claim_probe(state, 'synthetic-account-0')['claimed'],
                                 'Unexpired allowances cannot be evicted to admit more accounts')
            with patch('time.time', return_value=1800018001):
                self.assertTrue(kimi.claim_probe(state, 'synthetic-account-0')['claimed'])


class KimiReaderTests(unittest.TestCase):
    def setUp(self):
        self.kimi = importlib.import_module('kimi_usage')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.config = self.root / 'config.toml'
        self.config.write_text('[providers.p]\nbase_url="https://api.kimi.com/coding/v1"\napi_key="SYNTHETIC_A"\n'
                               '[models.a]\nmodel="kimi-for-coding"\nprovider="p"\n'
                               '[models.alias]\nmodel="kimi-for-coding"\nprovider="p"\n'
                               '[providers.q]\nbase_url="https://api.kimi.com/coding"\napi_key="SYNTHETIC_B"\n'
                               '[models.b]\nmodel="kimi-for-coding"\nprovider="q"\n')
        self.response = MagicMock(status=200)
        self.response.read.return_value = json.dumps({**quota(), 'private': 'RAW_BODY_SENTINEL'}).encode()
        self.connection = MagicMock()
        self.connection.getresponse.return_value = self.response
        p = patch('http.client.HTTPSConnection', return_value=self.connection)
        self.http = p.start()
        self.addCleanup(p.stop)
        p = patch('socket.socket.connect', side_effect=AssertionError('offline test'))
        p.start()
        self.addCleanup(p.stop)

    def read(self, alias='a', state=True):
        return self.kimi.read_usage(self.state if state else None, self.config, alias)

    def test_fixed_transport_and_no_secrets_in_results_or_private_cache(self):
        result = self.read()
        self.assertAlmostEqual(result['remaining_percent'], 94)
        self.assertEqual(self.http.call_args.args[0], 'api.kimi.com')
        self.assertLessEqual(self.http.call_args.kwargs['timeout'], 30)
        self.assertEqual(self.connection.request.call_args.args[:2], ('GET', '/coding/v1/usages'))
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer SYNTHETIC_A')
        self.assertLessEqual(self.response.read.call_args.args[0], 2**20 + 1)
        for path in self.state.glob('*.json'):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
            result = [result, path.read_text()]
        for forbidden in ('SYNTHETIC_A', 'SYNTHETIC_B', 'RAW_BODY_SENTINEL'):
            self.assertNotIn(forbidden, json.dumps(result))

    def test_alias_cache_sharing_other_accounts_and_credential_rotation(self):
        self.read(); self.read('alias')
        self.assertEqual(self.http.call_count, 1)
        self.read('b')
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer SYNTHETIC_B')
        self.config.write_text(self.config.read_text().replace('SYNTHETIC_A', 'SYNTHETIC_C'))
        self.read()
        self.assertEqual(self.http.call_count, 3)
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer SYNTHETIC_C')

    def test_same_key_under_different_provider_names_is_one_account(self):
        self.config.write_text(self.config.read_text().replace('SYNTHETIC_B', 'SYNTHETIC_A'))
        self.read('a')
        self.read('b')
        self.assertEqual(self.http.call_count, 1)

    def test_stale_cache_is_refreshed_within_thirty_seconds(self):
        self.read()
        with patch('time.time', return_value=time.time() + 31):
            self.read()
        self.assertEqual(self.http.call_count, 2)

    def test_redirect_and_error_bodies_never_leak(self):
        for status in (302, 401, 429, 500):
            self.response.status = status
            result = self.read(state=False)
            self.assertFalse(result['available'])
            self.assertNotIn('RAW_BODY_SENTINEL', json.dumps(result))
        self.response.read.assert_not_called()
        self.connection.request.side_effect = OSError('SYNTHETIC_A RAW_BODY_SENTINEL')
        self.assertNotIn('SYNTHETIC_A', json.dumps(self.read(state=False)))

    def test_unsupported_origins_and_paths_do_not_send_credentials(self):
        original = self.config.read_text()
        for origin in ('http://api.kimi.com/coding/v1', 'https://api.kimi.com.evil.invalid/coding/v1',
                       'https://api.kimi.com:444/coding/v1', 'https://api.kimi.com/other'):
            self.config.write_text(original.replace('https://api.kimi.com/coding/v1', origin))
            self.assertFalse(self.read(state=False)['available'])
        self.http.assert_not_called()

    def oauth_fixture(self):
        self.config.write_text('[providers.p]\nbase_url="https://api.kimi.com/coding/v1"\napi_key=""\n'
                               'oauth={storage="file",key="oauth/kimi-code"}\n'
                               '[models.a]\nmodel="kimi-for-coding"\nprovider="p"\n')
        directory = self.root / 'credentials'
        directory.mkdir()
        path = directory / 'kimi-code.json'
        path.write_text(json.dumps({'access_token': 'SYNTHETIC_OAUTH', 'refresh_token': 'test_synthetic_refresh',
                                    'expires_at': time.time() + 3600, 'token_type': 'Bearer'}))
        return path

    def test_file_oauth_is_read_only_and_token_rotation_invalidates_usage_cache(self):
        path = self.oauth_fixture()
        before = path.read_bytes()
        self.assertEqual(self.read()['remaining_percent'], 94)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer SYNTHETIC_OAUTH')
        path.write_text(path.read_text().replace('SYNTHETIC_OAUTH', 'ROTATED_OAUTH'))
        self.read()
        self.assertEqual(self.http.call_count, 2)
        self.assertEqual(self.connection.request.call_args.kwargs['headers']['Authorization'], 'Bearer ROTATED_OAUTH')
        for cache in self.state.glob('*.json'):
            for secret in ('SYNTHETIC_OAUTH', 'ROTATED_OAUTH', 'test_synthetic_refresh'):
                self.assertNotIn(secret, cache.read_text())

    def test_expired_unsupported_and_traversing_oauth_never_refresh_or_send(self):
        path = self.oauth_fixture()
        payload = json.loads(path.read_text())
        payload['expires_at'] = time.time() - 1
        path.write_text(json.dumps(payload))
        before = path.read_bytes()
        self.assertFalse(self.read(state=False)['available'])
        self.assertEqual(path.read_bytes(), before)
        payload['expires_at'] = time.time() + 3600
        path.write_text(json.dumps(payload))
        original = self.config.read_text()
        for bad in (original.replace('storage="file"', 'storage="keyring"'),
                    original.replace('oauth/kimi-code', '../../kimi-code')):
            self.config.write_text(bad)
            self.assertFalse(self.read(state=False)['available'])
        self.http.assert_not_called()

    def test_large_invalid_or_non_utf8_body_is_unknown(self):
        for body in (b'x' * (2**20 + 1), b'not json', b'\xff'):
            self.response.read.return_value = body
            self.assertFalse(self.read(state=False)['available'])


if __name__ == '__main__':
    unittest.main()
