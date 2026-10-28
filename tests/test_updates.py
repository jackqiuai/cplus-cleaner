"""Stable release checks with synthetic HTTPS responses and no downloads."""
import io
import json
import unittest
from unittest.mock import Mock
from urllib import error

from cplus_cleaner.application import updates


class UpdateTests(unittest.TestCase):
    def response(self, tag='v1.1.1', **fields):
        return io.BytesIO(json.dumps(dict(tag_name=tag, draft=False, prerelease=False, **fields)).encode())

    def test_numeric_comparison_and_normalized_version_lengths(self):
        for current, latest, expected in [('1.9.0', 'v1.10.0', True), ('1.1.1', 'v1.1.1', False),
                                          ('2.0.0', 'v1.9.9', False), ('1.1.1', 'v1.1.1.0', False),
                                          ('1.1.1', 'v1.1.1.1', True)]:
            with self.subTest(current=current, latest=latest):
                result = updates.check_latest(current, opener=Mock(return_value=self.response(latest)))
                self.assertEqual(result.available, expected)

    def test_fixed_repository_headers_timeout_and_project_owned_browser_url(self):
        opener = Mock(return_value=self.response('v1.2.0', html_url='https://example.invalid/unsafe'))
        result = updates.check_latest('1.1.1', opener=opener)
        message = opener.call_args.args[0]
        self.assertEqual(message.full_url, 'https://api.github.com/repos/jackqiuai/cplus-cleaner/releases/latest')
        self.assertIn('CPlusCleaner/1.1.1', message.get_header('User-agent'))
        self.assertIsNone(message.get_header('Authorization'))
        self.assertEqual(opener.call_args.kwargs['timeout'], 10)
        self.assertEqual(result.url, 'https://github.com/jackqiuai/cplus-cleaner/releases/tag/v1.2.0')

    def test_drafts_prereleases_invalid_versions_and_oversized_responses_fail(self):
        payloads = [b'not json', b'[]', b'{}', b'x'*(updates.MAX_RESPONSE+1)]
        for fields in ({'draft': True}, {'prerelease': True}, {'tag_name': 'nightly'}, {'tag_name': None}):
            data = dict(tag_name='v1.2.0', draft=False, prerelease=False)
            data.update(fields)
            payloads.append(json.dumps(data).encode())
        for payload in payloads:
            with self.subTest(payload=payload[:100]), self.assertRaises(updates.UpdateError):
                updates.check_latest('1.1.1', opener=Mock(return_value=io.BytesIO(payload)))

    def test_offline_timeout_rate_limit_and_missing_release_have_actionable_errors(self):
        cases = [(error.URLError('offline'), '无法连接 GitHub'), (TimeoutError(), '无法连接 GitHub'),
                 (error.HTTPError(updates.API_URL, 404, 'missing', {}, None), '暂未找到'),
                 (error.HTTPError(updates.API_URL, 403, 'limit', {}, None), '请求受限'),
                 (error.HTTPError(updates.API_URL, 429, 'limit', {}, None), '请求受限'),
                 (error.HTTPError(updates.API_URL, 503, 'unavailable', {}, None), '检查更新失败')]
        for exception, message in cases:
            with self.subTest(exception=exception), self.assertRaisesRegex(updates.UpdateError, message):
                updates.check_latest('1.1.1', opener=Mock(side_effect=exception))


# Purpose: Regression tests for stable release parsing, version comparison and network failures
