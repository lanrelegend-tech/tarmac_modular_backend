import os
from datetime import timedelta
from pathlib import Path
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured("Set SECRET_KEY in the environment or .env file.")
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
if not DEBUG and SECRET_KEY.startswith("replace-"):
    raise ImproperlyConfigured("Replace the example SECRET_KEY before production.")
ALLOWED_HOSTS = [x.strip() for x in os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if x.strip()]
SCHOOL_EMAIL_DOMAINS = [x.strip().lower() for x in os.getenv("SCHOOL_EMAIL_DOMAINS", "").split(",") if x.strip()]
if not DEBUG and (not os.getenv("ALLOWED_HOSTS") or not any(host not in ("localhost", "127.0.0.1") for host in ALLOWED_HOSTS)):
    raise ImproperlyConfigured("Set ALLOWED_HOSTS for production.")
if not DEBUG and not os.getenv("FRONTEND_ORIGIN"):
    raise ImproperlyConfigured("Set FRONTEND_ORIGIN for the school website.")
INSTALLED_APPS = [
    "django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles",
    "rest_framework", "rest_framework_simplejwt", "rest_framework_simplejwt.token_blacklist", "django_filters", "corsheaders",
    "apps.accounts.apps.AccountsConfig", "apps.products.apps.ProductsConfig",
    "apps.ads.apps.AdsConfig",
    "apps.orders.apps.OrdersConfig", "apps.payments.apps.PaymentsConfig",
    "apps.lost_found.apps.LostFoundConfig", "apps.messaging.apps.MessagingConfig",
    "apps.notifications.apps.NotificationsConfig", "apps.administration.apps.AdministrationConfig",
    "apps.reviews.apps.ReviewsConfig",
]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "whitenoise.middleware.WhiteNoiseMiddleware", "corsheaders.middleware.CorsMiddleware",
    "config.middleware.MaintenanceModeMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware", "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware", "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware", "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.debug","django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"
if os.getenv("DB_ENGINE", "sqlite") == "postgres":
    DATABASES = {"default": {"ENGINE":"django.db.backends.postgresql","NAME":os.getenv("DB_NAME"),"USER":os.getenv("DB_USER"),"PASSWORD":os.getenv("DB_PASSWORD"),"HOST":os.getenv("DB_HOST"),"PORT":os.getenv("DB_PORT") or "5432"}}
else:
    DATABASES = {"default": {"ENGINE":"django.db.backends.sqlite3","NAME":BASE_DIR / os.getenv("DB_NAME", "db.sqlite3")}}
if not DEBUG and os.getenv("DB_ENGINE", "sqlite") != "postgres":
    raise ImproperlyConfigured("Production must use PostgreSQL (set DB_ENGINE=postgres).")
if not DEBUG and not all(os.getenv(key) for key in ("DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")):
    raise ImproperlyConfigured("Set DB_NAME, DB_USER, DB_PASSWORD, and DB_HOST for production PostgreSQL.")
REDIS_URL = os.getenv("REDIS_URL", "")
if not DEBUG and not REDIS_URL:
    raise ImproperlyConfigured("Set REDIS_URL for shared production caching and rate limits.")
CACHES = {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": REDIS_URL}} if REDIS_URL else {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "tarmac-local"}}
if not DEBUG and not SCHOOL_EMAIL_DOMAINS:
    raise ImproperlyConfigured("Set SCHOOL_EMAIL_DOMAINS to the school's student email domain(s).")
AUTH_USER_MODEL = "accounts.User"
AUTH_PASSWORD_VALIDATORS = [{"NAME":"django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},{"NAME":"django.contrib.auth.password_validation.MinimumLengthValidator"},{"NAME":"django.contrib.auth.password_validation.CommonPasswordValidator"},{"NAME":"django.contrib.auth.password_validation.NumericPasswordValidator"}]
LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Lagos"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
PRIVATE_MEDIA_ROOT = BASE_DIR / "private_media"
DATA_UPLOAD_MAX_MEMORY_SIZE = 27 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
CORS_ALLOWED_ORIGINS = [os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()]
SECURE_SSL_REDIRECT = (os.getenv("SECURE_SSL_REDIRECT") or ("False" if DEBUG else "True")).lower() == "true"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SECURE_HSTS_SECONDS = 0 if DEBUG else int(os.getenv("SECURE_HSTS_SECONDS") or "31536000")
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = False
if os.getenv("TRUST_PROXY_SSL_HEADER", "False").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND") or ("django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend")
EMAIL_HOST = os.getenv("EMAIL_HOST", "")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.getenv("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.getenv("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.getenv("EMAIL_USE_TLS", "True").lower() == "true"
EMAIL_TIMEOUT = 10
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL") or "Tarmac <noreply@localhost>"
if not DEBUG and (not EMAIL_HOST or not os.getenv("DEFAULT_FROM_EMAIL")):
    raise ImproperlyConfigured("Configure EMAIL_HOST and DEFAULT_FROM_EMAIL for production notifications.")
PAYSTACK_ENABLED_DEFAULT = os.getenv("PAYSTACK_ENABLED_DEFAULT", "True" if os.getenv("PAYSTACK_SECRET_KEY") and os.getenv("PAYSTACK_WEBHOOK_SECRET") else "False").lower() == "true"
if not DEBUG and PAYSTACK_ENABLED_DEFAULT and (not os.getenv("PAYSTACK_SECRET_KEY") or not os.getenv("PAYSTACK_WEBHOOK_SECRET")):
    raise ImproperlyConfigured("Paystack is enabled by default; configure transaction and webhook secrets or set PAYSTACK_ENABLED_DEFAULT=False.")
SIMPLE_JWT = {"ACCESS_TOKEN_LIFETIME": timedelta(minutes=15), "REFRESH_TOKEN_LIFETIME": timedelta(days=1)}
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework_simplejwt.authentication.JWTAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend", "rest_framework.filters.SearchFilter", "rest_framework.filters.OrderingFilter"],
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle", "rest_framework.throttling.UserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"anon": os.getenv("ANON_THROTTLE_RATE", "30/hour"), "user": os.getenv("USER_THROTTLE_RATE", "1000/day"), "payment_initialization": "10/hour", "account_action": "10/hour"},
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": int(os.getenv("API_PAGE_SIZE", "25")),
}
