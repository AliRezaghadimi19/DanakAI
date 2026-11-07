import logging
import os
import ssl
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied
from ldap3 import Connection, Server, Tls
from ldap3.core.exceptions import LDAPException
from ldap3.utils.conv import escape_filter_chars

from .credentials import reveal
from .models import DirectoryIdentity, Organization
from .throttle import blocked

logger = logging.getLogger(__name__)


def configuration():
    return Organization.objects.first() or Organization()


def service_password(config):
    """Return the service bind password; raises InvalidToken when the stored value is unreadable."""
    return reveal(config.ldap_password_ciphertext) or os.getenv("NEXA_LDAP_BIND_PASSWORD")


def directory_server(config):
    url = urlsplit(config.ldap_url)
    if url.scheme != "ldaps" or not url.hostname:
        return None
    return Server(
        url.hostname, port=url.port or 636, use_ssl=True, connect_timeout=5,
        tls=Tls(
            validate=ssl.CERT_REQUIRED,
            ca_certs_file=None if config.ldap_ca_pem else os.getenv("NEXA_LDAP_CA_FILE"),
            ca_certs_data=config.ldap_ca_pem or None,
        ),
    )


class LocalBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if blocked(request, username):
            raise PermissionDenied
        method = request.POST.get("method") if request else None
        admin_login = request is not None and request.path == "/admin/login/"
        if method == "ad" or (not admin_login and configuration().auth_mode == Organization.Auth.AD):
            return None
        user = super().authenticate(request, username, password, **kwargs)
        if user and DirectoryIdentity.objects.filter(user=user).exists():
            return None
        return user


class DirectoryBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if request is None or request.path != "/login/" or request.POST.get("method") != "ad":
            return None
        if blocked(request, username):
            raise PermissionDenied
        config = configuration()
        if config.auth_mode == Organization.Auth.LOCAL or not username or not password:
            return None
        try:
            bind_password = service_password(config)
        except InvalidToken:
            logger.error("Stored LDAP bind password cannot be decrypted")
            return None
        if not bind_password:
            logger.error("Directory login attempted without a configured bind password")
            return None
        identity = DirectoryIdentity.objects.select_related("user").filter(username=username.strip().lower()).first()
        if not identity or not self.user_can_authenticate(identity.user):
            return None
        server = directory_server(config)
        if server is None:
            return None
        try:
            search = Connection(server, config.ldap_bind_dn, bind_password, auto_bind=True,
                                auto_referrals=False, receive_timeout=5)
        except (LDAPException, OSError) as error:
            logger.error("LDAP service bind failed: %s", type(error).__name__)
            return None
        try:
            with search:
                name = escape_filter_chars(identity.username)
                found = search.search(config.ldap_base, f"(&(objectClass=user)(sAMAccountName={name}))",
                                      attributes=["distinguishedName"], size_limit=2, time_limit=5)
                if not found or len(search.entries) != 1:
                    logger.info("Directory account %s matched %d entries", identity.username, len(search.entries))
                    return None
                dn = search.entries[0].entry_dn
            with Connection(server, dn, password, auto_bind=True, auto_referrals=False, receive_timeout=5):
                return identity.user
        except (LDAPException, OSError) as error:
            logger.info("Directory login failed for %s: %s", identity.username, type(error).__name__)
            return None
