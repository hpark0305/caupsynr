"""Gunicorn settings, loaded automatically by `gunicorn app:app` from this directory.

Security checklists (정보 노출 항목) flag version banners; gunicorn otherwise sends
"Server: gunicorn". Patching the module value here, in the master process before
workers fork, removes the product name from every response.
"""
import gunicorn.http.wsgi as _wsgi

_wsgi.SERVER = "web"

# Render injects PORT; keep gunicorn's defaults for workers (WEB_CONCURRENCY) and binding.
forwarded_allow_ips = "*"  # Render's proxy is the only route to the app; client IP comes from CF-Connecting-IP

# Safety margin for slow database round trips (default 30 s killed the worker
# mid-request on 2026-11-04). Pages should stay far below this.
timeout = 60

# Default access log plus the response time in ms (%(M)s), so slow pages show
# up in Render's logs without guessing from timestamps.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(M)sms'
