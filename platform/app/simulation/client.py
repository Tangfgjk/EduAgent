"""No URL parameter: experiments can only address their own ASGI instance."""
import json
import re
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.gateway.routes import create_app
from app.llm.client import FakeLLM
from app.storage.db import Store


class SimulationClient:
    def __init__(self, directory: Path, *, policy_factory=None, catalog_path='', clock=None, bank_factory=None):
        self.directory = directory.resolve()
        if not (self.directory / 'simulation.marker').is_file():
            raise ValueError('isolated simulation marker required')
        self.learner_id = 'simulation_fixture'
        # Never Settings.load(): no personal .env, keys, auth tokens or DB are inherited.
        self.settings = Settings(db_path=str(self.directory / 'runtime.sqlite3'),
            learner_id=self.learner_id, allow_simulated_time=True, learning_catalog_path=catalog_path,
            assessment_require_ticket=True)
        self.store = Store(self.settings.db_path)
        self.client = TestClient(create_app(self.settings, llm=FakeLLM(), store=self.store,
            policy_factory=policy_factory, clock=clock, **({'bank_factory': bank_factory} if bank_factory else {})))
        self.client.__enter__()
        self.latencies = []
        self.requests = []
        self.delivery_refs = {}

    def call(self, method, path, body=None):
        if not path.startswith('/api/') or '?' in path or '..' in path or '\\' in path:
            raise ValueError('only fixed API paths allowed')
        allowed = {
            'GET': r'/api/(?:path/recommend|sessions/[A-Za-z0-9_-]+|learning/(?:state|evidence)/simulation_fixture)',
            'POST': r'/api/(?:contracts|sessions|sessions/[A-Za-z0-9_-]+/(?:messages|reflection)|plans/[A-Za-z0-9_-]+/sign|learning/(?:consent/simulation_fixture|assessment/simulation_fixture/issue|(?:assessment|reviews)/simulation_fixture/submit))',
            'DELETE': r'/api/learning/consent/simulation_fixture',
        }
        if method not in allowed or not re.fullmatch(allowed[method], path):
            raise ValueError('simulation API capability denied')
        serialized = json.dumps(body or {}, ensure_ascii=False)
        if any(secret in serialized for secret in ('true_mastery', 'misconception_rate', 'hint_dependency', 'profile_id')):
            raise ValueError('latent input leakage')
        if method == 'POST' and re.fullmatch(r'/api/learning/(?:assessment|reviews)/simulation_fixture/submit', path) and getattr(self.settings, 'assessment_require_ticket', False):
            body = dict(body)
            attempt = body['attempt_id']
            if attempt not in self.delivery_refs:
                issued = self.call('POST', '/api/learning/assessment/simulation_fixture/issue',
                    {key:body[key] for key in ('assessment_id', 'assessment_version', 'occurred_at') if key in body}
                    | {'issuance_id':f'issue:{attempt}'})
                self.delivery_refs[attempt] = issued['delivery_ref']
            body['delivery_ref'] = self.delivery_refs[attempt]
        started = time.monotonic()
        response = self.client.request(method, path, json=body) if body is not None else self.client.request(method, path)
        self.latencies.append((time.monotonic() - started) * 1000)
        self.requests.append({'method': method, 'path': path, 'body': body, 'status': response.status_code})
        if response.status_code >= 400:
            raise ValueError(f'api_error:{response.status_code}:{path}')
        return response.json()

    def bootstrap(self):
        learner = self.learner_id
        self.call('POST', f'/api/learning/consent/{learner}',
            {'scopes': ['teaching'], 'version': 'simulation-fixture-v1', 'source': 'simulation_fixture'})
        contract = self.call('POST', '/api/contracts', {'goal_text': '练习独立求解一元一次方程并自检'})
        # Preserve learner deliberation: draft during exploration, sign only at the end.
        self.draft_id = contract['plan_version']['version_id']
        return self.call('POST', '/api/sessions', {})

    def close(self):
        self.client.__exit__(None, None, None)
        self.store.close()
