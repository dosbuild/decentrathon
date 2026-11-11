import json
import io
import urllib.error

import pytest

from dalel.inference.client import Config, Provider, QuotaError, http_post


def test_openai_uses_reasoning_completion_cap_and_never_stores(tmp_path):
    requests = []

    def transport(url, key, body):
        requests.append((url, json.loads(body)))
        return {'choices': [{'message': {'content': '{"rows": []}'}}],
                'usage': {'prompt_tokens': 20, 'completion_tokens': 10}}

    client = Provider(Config('https://api.openai.com/v1', 'gpt-6.1-sol', 'test-key'),
                      tmp_path / 'usage.sqlite3', transport)
    messages = [{'role': 'user', 'content': 'Return JSON.'}]
    client.call(messages, max_tokens=1500, store=True)
    assert requests[0][0] == 'https://api.openai.com/v1/chat/completions'
    body = requests[0][1]
    assert body['max_completion_tokens'] == 1500
    assert body['store'] is False
    assert 'temperature' not in body and 'max_tokens' not in body
    assert client.call(messages, max_tokens=1500, store=True)['cache_hit']
    assert len(requests) == 1


def test_openai_environment_defaults_require_explicit_budget(monkeypatch):
    monkeypatch.setattr('dalel.inference.client.load_env', lambda: None)
    for name in ['LOCQA_BASE_URL', 'LOCQA_MODEL', 'LOCQA_BUDGET_USD', 'LOCQA_API_KEY']:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-openai-key')
    config = Config.from_env()
    assert config.api_key == 'test-openai-key'
    assert config.base_url == 'https://api.openai.com/v1'
    assert config.model == 'gpt-6.1-sol'
    assert config.budget == 0


def test_exhausted_account_quota_is_not_retried(tmp_path, monkeypatch):
    class Opener:
        attempts = 0

        def open(self, request, timeout):
            self.attempts += 1
            payload = io.BytesIO(b'{"error":{"code":"insufficient_quota"}}')
            raise urllib.error.HTTPError(request.full_url, 429, 'quota', {}, payload)

    opener = Opener()
    monkeypatch.setattr('dalel.inference.client.urllib.request.build_opener', lambda *a: opener)
    provider = Provider(Config('https://api.openai.com/v1', 'gpt-6.1-sol', 'test-key'),
                        tmp_path / 'usage.db', http_post, connectivity_probe=lambda *a: None)
    with pytest.raises(QuotaError):
        provider.call_with_retry([{'role': 'user', 'content': 'Return JSON.'}])
    assert opener.attempts == 1
    assert provider.summary()['calls'] == 1
    assert provider.summary()['reserved_usd'] > 0


def test_failed_attempts_are_counted_under_the_bound_budget_group(tmp_path):
    from dalel.engine.pipeline import analyze
    from test_production import inputs

    def unavailable(*args):
        raise QuotaError('Synthetic exhausted quota')

    provider = Provider(Config('https://synthetic.invalid', 'test', 'test-key'),
                        tmp_path / 'usage.db', unavailable,
                        budget_group='release-check', group_budget=1)
    source, target = inputs()
    result = analyze(source, target, provider, cache_dir=tmp_path / 'cache')
    assert result['coverage']['status'] == 'partial'
    assert result['api_attempts'] == 1
    assert result['student_usage_after']['calls'] == 1
    assert result['student_accounting_after']['reserved'] > 0
