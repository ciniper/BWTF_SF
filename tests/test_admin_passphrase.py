"""Admin logins require explicit configuration; existing sessions remain valid."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]

LOGIN_CHECK = """
import json
import os
import sys
from shared import supabase  # Load local env before applying isolated test config.
for key in ('ADMIN_DASHBOARD_PASSPHRASE', 'ALERTS_PASSPHRASE'):
    os.environ.pop(key, None)
config, expected = json.loads(sys.argv[1])
os.environ.update(config)
from app.wsgi import app
from features.alerts.lab_sop import steps
photo = steps()[0]['photos'][0]['src']
protected = f'/alerts/lab-sop/photos/{photo}'
with app.test_client() as client:
    assert client.get('/api/build').status_code == 200
    assert client.get(protected).status_code == 401
    for supplied in ('', '   ', 'wrong-test-password', 'legacy-test-password'):
        assert client.post('/alerts/unlock', data={'passphrase': supplied}).status_code == 401
        assert client.get(protected).status_code == 401
    if expected:
        normalized_input = '  ' + expected.upper().replace(' ', '  ') + '  '
        response = client.post('/alerts/unlock', data={
            'passphrase': normalized_input, 'next': '/alerts/lab-sop',
        })
        assert response.status_code == 302
        assert response.headers['Location'].endswith('/alerts/lab-sop')
        assert client.get(protected).status_code == 200
with app.test_client() as client:
    cookie = app.session_interface.get_signing_serializer(app).dumps({'alerts_unlocked': True})
    client.set_cookie(app.config['SESSION_COOKIE_NAME'], cookie)
    assert client.get(protected).status_code == 200
"""


class AdminPassphraseTests(unittest.TestCase):
    def test_startup_configuration_and_existing_sessions(self):
        password = "admin test password"
        cases = {
            "unset": ({}, None),
            "empty": ({"ADMIN_DASHBOARD_PASSPHRASE": ""}, None),
            "whitespace": ({"ADMIN_DASHBOARD_PASSPHRASE": " \t "}, None),
            "legacy_only": ({"ALERTS_PASSPHRASE": "legacy-test-password"}, None),
            "configured": ({"ADMIN_DASHBOARD_PASSPHRASE": password}, password),
            "both": ({"ADMIN_DASHBOARD_PASSPHRASE": password,
                      "ALERTS_PASSPHRASE": "legacy-test-password"}, password),
        }
        env = {**os.environ, "SUPABASE_URL": "", "SUPABASE_SERVICE_KEY": "",
               "SFPUC_EMAIL_ENABLED": "false", "ALERT_WATCHER_INTERVAL": "off",
               "FLASK_SECRET_KEY": "offline-session-test-key"}
        for name, case in cases.items():
            with self.subTest(name=name):
                result = subprocess.run(
                    [sys.executable, "-c", LOGIN_CHECK, json.dumps(case)],
                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
