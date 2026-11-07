"""Failed-login throttle shared by the chat and admin sign-in forms.

Counting by account name is what protects Active Directory: every failed directory login is a bind
with the submitted password, so unlimited attempts could trip the domain's lockout policy for a real
employee. Counting by client address limits spraying across many names; it is off by default
because behind a reverse proxy every request shares the proxy's address.
"""
import hashlib

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_login_failed
from django.core.cache import cache
from django.dispatch import receiver


def limits(request, username):
    name = hashlib.sha256((username or "").strip().lower().encode()).hexdigest()
    result = {f"nexa:login:user:{name}": settings.NEXA_LOGIN_MAX_ATTEMPTS}
    if request is not None and settings.NEXA_LOGIN_MAX_ATTEMPTS_PER_ADDRESS > 0:
        address = request.META.get("REMOTE_ADDR", "")
        result[f"nexa:login:addr:{address}"] = settings.NEXA_LOGIN_MAX_ATTEMPTS_PER_ADDRESS
    return {key: limit for key, limit in result.items() if limit > 0}


def blocked(request, username):
    active = limits(request, username)
    counts = cache.get_many(list(active))
    return any(counts.get(key, 0) >= limit for key, limit in active.items())


def record(request, username):
    # Sliding window: each failure restarts the timer. Not atomic across workers; a few extra
    # attempts under a race are acceptable for this purpose.
    active = list(limits(request, username))
    counts = cache.get_many(active)
    cache.set_many({key: counts.get(key, 0) + 1 for key in active}, settings.NEXA_LOGIN_WINDOW_SECONDS)


@receiver(user_login_failed)
def on_failure(sender, credentials, request=None, **kwargs):
    record(request, credentials.get("username"))


@receiver(user_logged_in)
def on_success(sender, request, user, **kwargs):
    username = request.POST.get("username") if request is not None else None
    cache.delete_many([key for key in limits(None, username or user.get_username())])
