import base64

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.utils.crypto import salted_hmac


def cipher():
    keys = [settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS]
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(
            salted_hmac("nexa.integration.credentials", "", secret=key, algorithm="sha256").digest()
        ))
        for key in keys
    ])


def seal(value):
    return cipher().encrypt(value.encode()).decode() if value else ""


def reveal(value):
    return cipher().decrypt(value.encode()).decode() if value else ""
