import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("NEXA_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
SECRET_KEY = os.getenv("NEXA_SECRET_KEY")
if not SECRET_KEY:
    key_path = DATA_DIR / "secret"
    try:
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, "w") as output:
            output.write(secrets.token_urlsafe(48))
    SECRET_KEY = key_path.read_text().strip()

DEBUG = os.getenv("NEXA_DEBUG", "1") == "1"

def env_list(name, default=""):
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


# 127.0.0.1 stays allowed so the container healthcheck works with any public host list.
ALLOWED_HOSTS = list(dict.fromkeys([*env_list("NEXA_ALLOWED_HOSTS", "localhost,127.0.0.1"), "127.0.0.1"]))
CSRF_TRUSTED_ORIGINS = env_list("NEXA_CSRF_TRUSTED_ORIGINS")
if os.getenv("NEXA_PROXY_SSL_HEADER") == "1":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "nexa",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "nexa.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "nexa" / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": DATA_DIR / "nexa.sqlite3",
    "OPTIONS": {"timeout": 10, "transaction_mode": "IMMEDIATE"},
}}
AUTHENTICATION_BACKENDS = ["nexa.directory.LocalBackend", "nexa.directory.DirectoryBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/admin/"
LANGUAGE_CODE = "fa"
TIME_ZONE = os.getenv("NEXA_TIME_ZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "static"
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = os.getenv("NEXA_HTTPS") == "1"
SECURE_HSTS_SECONDS = int(os.getenv("NEXA_HSTS_SECONDS", "0"))
CACHES = {"default": {"BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
                      "LOCATION": DATA_DIR / "cache"}}
NEXA_LOGIN_MAX_ATTEMPTS = int(os.getenv("NEXA_LOGIN_MAX_ATTEMPTS", "5"))
NEXA_LOGIN_MAX_ATTEMPTS_PER_ADDRESS = int(os.getenv("NEXA_LOGIN_MAX_ATTEMPTS_PER_ADDRESS", "0"))
NEXA_LOGIN_WINDOW_SECONDS = int(os.getenv("NEXA_LOGIN_WINDOW_SECONDS", "900"))
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "nexa": {"handlers": ["console"], "level": os.getenv("NEXA_LOG_LEVEL", "INFO"), "propagate": False},
    },
}
DATA_UPLOAD_MAX_MEMORY_SIZE = 384 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 384 * 1024
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.MemoryFileUploadHandler"]
DATA_UPLOAD_MAX_NUMBER_FILES = 1
NEXA_MAX_PENDING_UPLOADS = int(os.getenv("NEXA_MAX_PENDING_UPLOADS", "10"))
