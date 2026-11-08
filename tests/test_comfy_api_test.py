import importlib.util
import base64
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

ROOT = Path(__file__).resolve().parents[1]


class ComfyApiDiagnosticTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('comfy_auth_api_test', ROOT / 'canvas/onekey/comfy_auth.py')
        self.auth = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.auth)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.url = 'http://127.0.0.1:8188'
        paths = types.SimpleNamespace(ONEKEY=self.root, comfy_url=lambda: self.url, comfy_auth=self.auth,
                                      auth_for=lambda url: self.auth.client_auth(self.root, url),
                                      auth_headers_for=lambda url: self.auth.client_headers(self.root, url))
        spec = importlib.util.spec_from_file_location('comfy_api_test_test', ROOT / 'launcher/comfy_api_test.py')
        self.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'paths': paths}):
            spec.loader.exec_module(self.module)

    def test_all_checks_read_only_authenticated_and_no_sensitive_data_returned(self):
        self.auth.save(self.root, self.url, True, auth_type='bearer', token='test-secret-token')
        requests = []
        def handler(request):
            self.assertEqual(request.method, 'GET')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-secret-token')
            requests.append(request.url.path)
            data = {'/system_stats': {'system': {}}, '/object_info': {'KSampler': {}},
                    '/queue': {'queue_running': [[1, 'pid', {'prompt': 'private'}]], 'queue_pending': []}}
            return httpx.Response(200, json=data.get(request.url.path, {}))
        original = httpx.Client
        with patch.object(self.module.httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
            result = self.module.run()
        self.assertTrue(result['ok'])
        self.assertEqual(len(requests), 4)
        self.assertNotIn('test-secret-token', json.dumps(result))
        self.assertNotIn('private', json.dumps(result))
        self.assertTrue(all(c['status_code'] == 200 and c['elapsed_ms'] >= 0 for c in result['checks']))

    def test_http_and_transport_failure_details(self):
        original = httpx.Client
        cases = [(401, 'auth', '认证'), (302, '', '重定向'), (404, '', '不存在'),
                 (503, '', '服务返回错误'), (200, '<html>test-secret</html>', '不是 JSON')]
        for status, body, hint in cases:
            with self.subTest(status=status), patch.object(self.module.httpx, 'Client',
                    side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda req: httpx.Response(status, text=body)), **kw)):
                result = self.module.check_endpoint(self.url, 'health', '/system_stats', 10)
            self.assertFalse(result['ok'])
            self.assertEqual(result['status_code'], status)
            self.assertIn(hint, result['detail'])
            self.assertNotIn('test-secret', json.dumps(result))
        for error, hint in ((httpx.ConnectError, '无法建立连接'), (httpx.ReadTimeout, '超时')):
            def handler(request):
                raise error('test-secret', request=request)
            with patch.object(self.module.httpx, 'Client', side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
                result = self.module.check_endpoint(self.url, 'health', '/system_stats', 10)
            self.assertIsNone(result['status_code'])
            self.assertIn(hint, result['detail'])
            self.assertNotIn('test-secret', json.dumps(result))

    def test_basic_auth_and_no_auth(self):
        original = httpx.Client
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                self.auth.save(self.root, self.url, enabled, 'test-user', 'test-password')
                def handler(request):
                    expected = 'Basic ' + base64.b64encode(b'test-user:test-password').decode() if enabled else None
                    self.assertEqual(request.headers.get('Authorization'), expected)
                    return httpx.Response(200, json={})
                with patch.object(self.module.httpx, 'Client',
                        side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw)):
                    result = self.module.run()
                self.assertEqual(result['auth_type'], 'basic' if enabled else 'none')
                self.assertNotIn('test-password', json.dumps(result))

    def test_redirects_are_not_followed_and_environment_proxy_is_disabled(self):
        self.auth.save(self.root, self.url, True, auth_type='bearer', token='test-secret-token')
        original = httpx.Client
        requests = []
        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(302, headers={'Location': 'https://example.com/private'})
        def client(**kw):
            self.assertFalse(kw['trust_env'])
            self.assertFalse(kw['follow_redirects'])
            return original(transport=httpx.MockTransport(handler), **kw)
        with patch.object(self.module.httpx, 'Client', side_effect=client):
            result = self.module.run()
        self.assertFalse(result['ok'])
        self.assertEqual(len(requests), 4)
        self.assertTrue(all(url.startswith(self.url + '/') for url in requests))
        self.assertNotIn('test-secret-token', json.dumps(result))

    def test_wrong_json_structure_is_not_reported_as_success(self):
        original = httpx.Client
        with patch.object(self.module.httpx, 'Client',
                side_effect=lambda **kw: original(transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'portal': True})), **kw)):
            for path in ('/system_stats', '/object_info', '/queue'):
                self.assertFalse(self.module.check_endpoint(self.url, path, path, 10)['ok'])
