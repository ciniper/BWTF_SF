"""Admin archive navigation, private data, and reversible visibility controls."""
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from app.wsgi import app
from features.alerts import email_archive as A
from features.sfpuc_updates import store as S

MID = 'c37b38cd-a0a0-4111-9000-111111111111'
PATH = '/alerts/emails/surfrider'
VISIBILITY = '/alerts/sfpuc-emails/visibility'


def bulletin(**kwargs):
    return dict(id=MID, subject='SF BEACH DE-POSTED', body_text='Official bulletin',
                sender='beachwaterquality@sfwater.org', sender_matches=True, warnings=[],
                sent_at=None, received_at='2026-11-24T12:00:00Z', raw_payload={},
                status='captured', revision=0, visibility_revision=0, **kwargs)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('SFPUC_EMAIL_ENABLED', 'true')
    monkeypatch.setattr(A.sb, 'is_configured', lambda: True)
    monkeypatch.setattr(A.sb, 'select', lambda *a: pytest.fail('Unexpected database read'))
    monkeypatch.setattr(A.sb, 'rpc', lambda *a: pytest.fail('Unexpected database write'))
    monkeypatch.setattr(S, 'recent_runs', lambda: [])
    monkeypatch.setattr(S, 'inbox', lambda offset: [])
    with app.test_client() as client:
        yield client


def unlock(client):
    with client.session_transaction() as session:
        session['alerts_unlocked'] = True
        session['sfpuc_email_csrf'] = 'test-csrf'


def test_both_sources_require_admin_and_sent_emails_work_without_sfpuc(client, monkeypatch):
    for path in ('/alerts/emails', PATH, '/alerts/sfpuc-emails'):
        assert b'name="passphrase"' in client.get(path).data
    assert client.post(VISIBILITY).status_code == 401
    unlock(client)
    assert client.get('/alerts/emails').location.endswith('/alerts/sfpuc-emails')
    monkeypatch.setenv('SFPUC_EMAIL_ENABLED', 'false')
    monkeypatch.setattr(A.sb, 'select', lambda *a: [])
    assert client.get('/alerts/emails').location.endswith(PATH)
    assert client.get('/alerts/sfpuc-emails').status_code == 404
    result = client.get(PATH)
    assert result.status_code == 200 and b'No beach-alert emails' in result.data
    assert b'SFPUC <small>' not in result.data
    assert result.headers['Cache-Control'] == 'private, no-store'


def test_sent_archive_reads_email_only_and_bodies_only_for_selected(client, monkeypatch):
    row = dict(id=7, subject='Beach update', recipient='private@example.test', simulated=True,
               sent_at='2026-11-24T12:00:00Z', http_status=201, html_body='<p>Actual email</p>', text_body='Actual email')
    calls = []
    def select(table, params):
        calls.append((table, params))
        return [row]
    monkeypatch.setattr(A.sb, 'select', select)
    unlock(client)
    result = client.get(PATH + '?offset=25')
    assert result.status_code == 200 and b'Actual email' in result.data
    assert b'Test alert' in result.data and b'Accepted' in result.data
    assert 'private, no-store' == result.headers['Cache-Control']
    assert all(table == 'alert_deliveries' and q['channel'] == 'eq.email' for table, q in calls)
    assert calls[0][1]['order'] == 'sent_at.desc,id.desc' and calls[0][1]['offset'] == '25'
    assert 'html_body' not in calls[0][1]['select'] and calls[1][1]['id'] == 'eq.7'
    assert b'sandbox=""' in result.data and b'iframe' in result.data
    class Frames(HTMLParser):
        def __init__(self):
            super().__init__()
            self.frames = []
        def handle_starttag(self, tag, attrs):
            if tag == 'iframe':
                self.frames.append(dict(attrs))
    parsed = Frames()
    parsed.feed(result.data.decode())
    assert len(parsed.frames) == 1
    doc = parsed.frames[0]['srcdoc']
    assert '<p>Actual email</p>' in doc
    assert 'default-src \'none\'' in doc and 'form-action \'none\'' in doc
    assert b'No approval' not in result.data


def test_sent_html_has_no_subscriber_actions_or_active_content():
    output = A.archived_html('<style>p{color:blue}</style><table><tr><td><a href="https://example.test/unsubscribe">Stop</a>'
                             '<img src="https://tracker.test/pixel"><p onclick="evil()">Body</p></td></tr></table>'
                             '<script>evil()</script><meta http-equiv="refresh" content="0;url=https://bad.test">'
                             '<form action="/unsubscribe"><input></form>')
    assert '<table>' in output and '<style>p{color:blue}</style>' in output
    assert 'href=' not in output and 'onclick' not in output and 'evil' not in output
    assert '<meta' not in output and '<form' not in output


def test_sent_errors_invalid_ids_and_missing_message(client, monkeypatch):
    unlock(client)
    for query in ('?message=no', '?message=-1', '?message=9223372036854775808', '?offset=no'):
        assert client.get(PATH + query).status_code == 400
    monkeypatch.setattr(A.sb, 'select', lambda *a: [])
    assert client.get(PATH + '?message=123').status_code == 404
    def fail(*a):
        raise RuntimeError('PRIVATE secret')
    monkeypatch.setattr(A.sb, 'select', fail)
    result = client.get(PATH)
    assert result.status_code == 503 and b'PRIVATE' not in result.data
    assert result.headers['Cache-Control'] == 'private, no-store'


def test_sfpuc_opens_newest_without_approval_and_setup_stays_private(client, monkeypatch):
    row = bulletin()
    monkeypatch.setattr(S, 'inbox', lambda offset: [dict(row, status='pending')])
    monkeypatch.setattr(S, 'detail', lambda mid: row)
    unlock(client)
    result = client.get('/alerts/sfpuc-emails')
    assert result.status_code == 200 and b'Shown on Today' in result.data
    assert b'Hide from Today' in result.data and b'Approve for preview' not in result.data
    assert b'Reviewed message' not in result.data
    row.update(subject='Gmail forwarding confirmation', body_text='Code 123456')
    result = client.get('/alerts/sfpuc-emails')
    assert b'123456' in result.data and b'Private' in result.data
    assert b'Hide from Today' not in result.data and b'Restore to Today' not in result.data


def test_hide_restore_changes_only_current_state_with_stale_protection(client, monkeypatch):
    row, calls = bulletin(), []
    monkeypatch.setattr(S, 'detail', lambda mid: row.copy())
    monkeypatch.setattr(S, 'captured', lambda *a, **k: [row.copy()])
    def visibility(mid, revision, hidden):
        calls.append((mid, revision, hidden))
        if revision != row['revision']:
            return {'ok': False}
        row.update(revision=revision + 1, visibility_revision=revision + 1,
                   status='rejected' if hidden else 'captured')
        return {'ok': True}
    monkeypatch.setattr(S, 'set_visibility', visibility)
    unlock(client)
    form = dict(message_id=MID, revision=0, action='hide')
    assert client.post(VISIBILITY, data=form).status_code == 403
    form['csrf'] = 'test-csrf'
    assert client.get('/api/city-emails').json['update']['id'] == MID
    assert client.post(VISIBILITY, data=form).status_code == 303
    assert client.get('/api/city-emails').json['update'] is None
    assert client.post(VISIBILITY, data=form).status_code == 409
    form.update(revision=1, action='restore')
    assert client.post(VISIBILITY, data=form).status_code == 303
    assert client.get('/api/city-emails').json['update']['id'] == MID
    assert row['body_text'] == 'Official bulletin' and row['revision'] == 2
    assert calls[-1] == (MID, 1, False)
    page = client.get('/alerts/sfpuc-emails?message=' + MID)
    assert b'legacy review history' not in page.data and b'Reviewed message' not in page.data


def test_history_reads_work_before_migration_but_visibility_waits(client, monkeypatch):
    row = bulletin()
    del row['visibility_revision']
    monkeypatch.setattr(S, 'detail', lambda mid: row)
    monkeypatch.setattr(S, 'set_visibility', lambda *args: pytest.fail('Migration is not ready'))
    unlock(client)
    page = client.get('/alerts/sfpuc-emails?message=' + MID)
    assert page.status_code == 200 and b'Official bulletin' in page.data
    assert b'Hide/Restore is temporarily unavailable' in page.data
    assert b'name="action"' not in page.data
    assert client.post(VISIBILITY, data=dict(message_id=MID, revision=0, csrf='test-csrf', action='hide')).status_code == 503


def test_detail_reads_original_and_current_state_without_old_edits(monkeypatch):
    calls = []
    def select(table, params):
        calls.append(table)
        if table == 'sfpuc_email_messages':
            return [bulletin()]
        if table == 'sfpuc_email_inbox':
            return [dict(revision=4, status='rejected', has_conflict=False)]
        pytest.fail('Detail must not read legacy edits')
    monkeypatch.setattr(S.sb, 'select', select)
    message = S.detail(MID)
    assert message['body_text'] == 'Official bulletin' and message['status'] == 'rejected'
    assert message['revision'] == 4
    assert calls == ['sfpuc_email_messages', 'sfpuc_email_inbox']


def test_visibility_cannot_publish_other_mail_or_override_validation(client, monkeypatch):
    row = bulletin()
    row['subject'] = 'Gmail forwarding confirmation'
    monkeypatch.setattr(S, 'detail', lambda mid: row)
    unlock(client)
    form = dict(message_id=MID, revision=0, csrf='test-csrf', action='restore')
    assert client.post(VISIBILITY, data=form).status_code == 400
    for changes in ({'revision': '-1'}, {'message_id': 'x'}, {'action': 'approve'}):
        assert client.post(VISIBILITY, data=dict(form, **changes)).status_code == 400
    assert client.post(VISIBILITY, data='x' * 4097).status_code == 413


if __name__ == '__main__':
    raise SystemExit(pytest.main([__file__, '-q']))
