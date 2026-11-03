"""Gunicorn settings, loaded automatically by `gunicorn app:app` from this directory.

Security checklists (정보 노출 항목) flag version banners; gunicorn otherwise sends
"Server: gunicorn". Patching the module value here, in the master process before
workers fork, removes the product name from every response.
"""
import gunicorn.http.wsgi as _wsgi

_wsgi.SERVER = "web"

# Render injects PORT; keep gunicorn's defaults for workers (WEB_CONCURRENCY) and binding.
forwarded_allow_ips = "*"  # Render's proxy is the only route to the app; client IP comes from CF-Connecting-IP
