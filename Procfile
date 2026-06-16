# Process definition for PaaS hosts (Render, Railway, Heroku-style).
# One worker on purpose: the forecast engine runs a single background refresh
# thread; multiple workers would each spawn their own. Threads handle the
# concurrency this dashboard needs. $PORT is injected by the host.
web: gunicorn -w 1 --threads 8 --timeout 120 -b 0.0.0.0:$PORT app.wsgi:app
