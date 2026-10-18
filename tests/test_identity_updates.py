import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.infrastructure.db.models import AuthSession
from app.modules.errors import DomainError
from app.modules.identity.security import password_hash
from tests.test_s02 import database, env, PASSWORD


def test_default_browser_origin_uses_fixed_project_port(env):
    from app.config import Settings
    from app.gateway.v1 import create_app
    with TestClient(create_app(Settings(), database=env.db)) as browser:
        body = {'username': 'admin.s02', 'password': PASSWORD}
        for origin in ('http://127.0.0.1:4003', 'http://localhost:4003'):
            assert browser.post('/api/v1/auth/login', headers={'Origin': origin}, json=body).status_code == 200
        for origin in ('http://127.0.0.1:8000', 'https://untrusted.example'):
            assert browser.post('/api/v1/auth/login', headers={'Origin': origin}, json=body).status_code == 403


@pytest.mark.parametrize('secret',['123456','ab12cd'])
def test_student_six_character_password_create_reset_and_revoke(env,secret):
    bad=env.admin.post('/api/v1/admin/students',json={'username':'short.student','display_name':'测试学生','password':'12345'})
    assert bad.status_code==422
    response=env.admin.post('/api/v1/admin/students',json={'username':'short.student','display_name':'测试学生','password':secret})
    assert response.status_code==201, response.text
    aid=response.json()['id']
    with TestClient(env.app) as student:
        assert student.post('/api/v1/auth/login',json={'username':'short.student','password':secret}).status_code==200
        assert student.get('/api/v1/auth/me').status_code==200
        assert env.admin.post(f'/api/v1/admin/students/{aid}/reset-password',json={'password':'12345'}).status_code==422
        assert env.admin.post(f'/api/v1/admin/students/{aid}/reset-password',json={'password':'654321'}).status_code==200
        assert student.get('/api/v1/auth/me').status_code==401
        assert student.post('/api/v1/auth/login',json={'username':'short.student','password':secret}).status_code==401
        assert student.post('/api/v1/auth/login',json={'username':'short.student','password':'654321'}).status_code==200


def test_administrator_default_password_policy_remains():
    with pytest.raises(DomainError):
        password_hash('123456')


def test_browser_role_sessions_coexist_and_logout_separately(env):
    env.student()
    sh={'X-Wenjin-Client':'student'}
    ah={'X-Wenjin-Client':'admin'}
    with TestClient(env.app) as browser:
        assert browser.post('/api/v1/auth/login',json={'username':'admin.s02','password':PASSWORD}).status_code==200
        assert 'wenjin_session' in list(browser.cookies.keys())
        student=browser.post('/api/v1/auth/login',headers=sh,json={'username':'student.one','password':PASSWORD})
        assert student.status_code==200
        assert 'wenjin_student_session' in list(browser.cookies.keys())
        assert 'wenjin_session' not in list(browser.cookies.keys())
        assert 'wenjin_csrf' not in list(browser.cookies.keys())
        admin=browser.post('/api/v1/auth/login',headers=ah,json={'username':'admin.s02','password':PASSWORD})
        assert admin.status_code==200
        assert browser.get('/api/v1/auth/me',headers=sh).json()['account']['role']=='student'
        assert browser.get('/api/v1/auth/me',headers=ah).json()['account']['role']=='admin'
        assert browser.get('/api/v1/auth/me').status_code==401  # 不自动选取另一端的会话。
        assert browser.get('/api/v1/admin/students',headers=sh).status_code==403
        assert browser.get('/api/v1/student/workspaces',headers=ah).status_code==403
        assert browser.post('/api/v1/auth/logout',headers={**ah,'X-CSRF-Token':student.json()['csrf_token']}).status_code==403
        assert browser.post('/api/v1/auth/logout',headers={**ah,'X-CSRF-Token':admin.json()['csrf_token']}).status_code==204
        assert browser.get('/api/v1/auth/me',headers=sh).status_code==200
        assert browser.get('/api/v1/auth/me',headers=ah).status_code==401


def test_wrong_login_role_cannot_replace_or_create_session(env):
    env.student()
    headers={'X-Wenjin-Client':'student'}
    with TestClient(env.app) as browser:
        assert browser.post('/api/v1/auth/login',headers=headers,json={'username':'student.one','password':PASSWORD}).status_code==200
        with env.db.transaction() as session:
            before=len(list(session.scalars(select(AuthSession))))
        response=browser.post('/api/v1/auth/login',headers=headers,json={'username':'admin.s02','password':PASSWORD})
        assert response.status_code==403
        with env.db.transaction() as session:
            assert len(list(session.scalars(select(AuthSession))))==before
        assert browser.get('/api/v1/auth/me',headers=headers).json()['account']['role']=='student'
        assert browser.get('/api/v1/auth/me',headers={'X-Wenjin-Client':'unknown'}).status_code==400
