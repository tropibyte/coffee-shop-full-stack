"""WSGI entry point for gunicorn, Azure App Service and any other host.

Usage::

    gunicorn --bind 0.0.0.0:8000 wsgi:app
"""

from src.api import app

__all__ = ["app"]
