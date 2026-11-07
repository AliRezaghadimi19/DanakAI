"""Connection checks for the admin dashboard.

`status()` describes each integration from configuration alone and is cheap enough for every page
load. `run()` contacts the services, which an administrator triggers explicitly; its result is kept
in the cache so the dashboard can show the last check.
"""
import json
import logging
import os
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener

from cryptography.fernet import InvalidToken
from django.core.cache import cache
from django.utils import timezone
from ldap3 import Connection
from ldap3.core.exceptions import LDAPException

from .credentials import reveal
from .directory import configuration, directory_server, service_password
from .models import Model, Organization
from .services import NoRedirect

logger = logging.getLogger(__name__)
CACHE_KEY = "nexa:integration-checks"


@dataclass(frozen=True)
class Check:
    key: str
    name: str
    state: str  # ok, failed, off or unchecked
    detail: str


def model_check(model, contact):
    name = f"مدل: {model.name}"
    key = f"model-{model.pk}"
    try:
        secret = reveal(model.api_key_ciphertext) or (os.getenv(model.key_env) if model.key_env else None)
    except InvalidToken:
        return Check(key, name, "failed", "کلید ذخیره‌شده قابل خواندن نیست.")
    if model.key_env and not secret:
        return Check(key, name, "failed", "کلید API تنظیم نشده است.")
    if not contact:
        return Check(key, name, "unchecked", "پیکربندی شده؛ هنوز آزمون نشده.")
    headers = {"Content-Type": "application/json"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    payload = json.dumps({"model": model.identifier, "max_tokens": 1, "stream": False,
                          "messages": [{"role": "user", "content": "ping"}]}).encode()
    try:
        with build_opener(NoRedirect()).open(Request(model.endpoint, data=payload, headers=headers), timeout=15):
            return Check(key, name, "ok", "سرویس پاسخ داد.")
    except HTTPError as error:
        logger.warning("Model %s check returned HTTP %s", model.pk, error.code)
        if error.code in (401, 403):
            return Check(key, name, "failed", "کلید API پذیرفته نشد.")
        return Check(key, name, "failed", f"سرویس با کد HTTP {error.code} پاسخ داد.")
    except (URLError, OSError) as error:
        logger.warning("Model %s check failed: %s", model.pk, type(error).__name__)
        return Check(key, name, "failed", "سرویس در دسترس نیست.")


def directory_check(config, contact):
    name = "Active Directory"
    if config.auth_mode == Organization.Auth.LOCAL:
        return Check("directory", name, "off", "ورود سازمانی غیرفعال است.")
    server = directory_server(config)
    if server is None:
        return Check("directory", name, "failed", "آدرس LDAPS معتبر نیست.")
    try:
        password = service_password(config)
    except InvalidToken:
        return Check("directory", name, "failed", "رمز ذخیره‌شده حساب اتصال قابل خواندن نیست.")
    if not password:
        return Check("directory", name, "failed", "رمز حساب اتصال تنظیم نشده است.")
    if not contact:
        return Check("directory", name, "unchecked", "پیکربندی شده؛ هنوز آزمون نشده.")
    try:
        with Connection(server, config.ldap_bind_dn, password, auto_bind=True,
                        auto_referrals=False, receive_timeout=5):
            return Check("directory", name, "ok", "اتصال امن و ورود حساب سرویس موفق بود.")
    except (LDAPException, OSError) as error:
        logger.warning("Directory check failed: %s", type(error).__name__)
        return Check("directory", name, "failed", "اتصال یا ورود حساب سرویس ناموفق بود.")


def collect(contact):
    config = configuration()
    checks = [model_check(model, contact) for model in Model.objects.filter(enabled=True).order_by("pk")]
    if not checks:
        checks.append(Check("model", "مدل", "off", "مدل فعالی ثبت نشده است."))
    checks.append(directory_check(config, contact))
    return checks


def run():
    checks = collect(contact=True)
    cache.set(CACHE_KEY, {"at": timezone.now().isoformat(), "checks": [asdict(check) for check in checks]}, None)
    return checks


def status():
    """Configuration state merged with the last stored check for the same integration."""
    stored = cache.get(CACHE_KEY) or {}
    previous = {item["key"]: item for item in stored.get("checks", [])}
    merged = []
    for check in collect(contact=False):
        last = previous.get(check.key)
        if check.state == "unchecked" and last and last["name"] == check.name:
            check = Check(**last)
        merged.append(check)
    return merged, stored.get("at")
