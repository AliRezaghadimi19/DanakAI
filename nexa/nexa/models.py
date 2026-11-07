from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.core.validators import RegexValidator


class Model(models.Model):
    name = models.CharField("نام نمایشی", max_length=100)
    endpoint = models.URLField("آدرس کامل سرویس", max_length=500)
    identifier = models.CharField("شناسه مدل یا عامل", max_length=200)
    key_env = models.CharField("نام متغیر محیطی کلید", max_length=100, blank=True,
                               validators=[RegexValidator(r"^NEXA_MODEL_KEY(?:_[A-Z0-9]+)*$", "نام کلید باید با NEXA_MODEL_KEY آغاز شود.")])
    api_key_ciphertext = models.TextField(blank=True, editable=False)
    enabled = models.BooleanField("فعال", default=True)

    class Meta:
        verbose_name = "مدل"
        verbose_name_plural = "مدل‌ها"

    def __str__(self):
        return self.name


class Policy(models.Model):
    class Mode(models.TextChoices):
        KNOWLEDGE = "knowledge", "فقط دانش"
        HYBRID = "hybrid", "ترکیبی"
        FREE = "free", "آزاد"

    mode = models.CharField("حالت پاسخ", max_length=12, choices=Mode, blank=True)
    default_model = models.ForeignKey(Model, verbose_name="مدل پیش‌فرض", on_delete=models.PROTECT, null=True, blank=True)
    choose_model = models.BooleanField("اجازه انتخاب مدل", null=True, blank=True)
    daily = models.PositiveIntegerField("سقف روزانه", null=True, blank=True)
    monthly = models.PositiveIntegerField("سقف ماهانه", null=True, blank=True)
    upload = models.BooleanField("اجازه بارگذاری", null=True, blank=True)

    class Meta:
        abstract = True


class Organization(Policy):
    class Auth(models.TextChoices):
        LOCAL = "local", "داخلی"
        AD = "ad", "اکتیو دایرکتوری"
        BOTH = "both", "هر دو"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    auth_mode = models.CharField("روش ورود", max_length=8, choices=Auth, default=Auth.LOCAL)
    ldap_url = models.CharField(
        "آدرس امن دایرکتوری", max_length=255, blank=True
    )
    ldap_base = models.CharField("پایه جست‌وجو", max_length=255, blank=True)
    ldap_bind_dn = models.CharField("شناسه اتصال", max_length=255, blank=True)
    ldap_password_ciphertext = models.TextField(blank=True, editable=False)
    ldap_ca_pem = models.TextField("گواهی CA برای LDAPS (PEM)", blank=True)

    class Meta:
        verbose_name = "تنظیمات سازمان"
        verbose_name_plural = "تنظیمات سازمان"
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name="one_organization")]

    def __str__(self):
        return "سیاست سراسری"

    def clean(self):
        if self.auth_mode != self.Auth.LOCAL:
            from urllib.parse import urlsplit

            try:
                url = urlsplit(self.ldap_url)
                valid = url.scheme == "ldaps" and url.hostname and not (
                    url.username or url.password or url.query or url.fragment or url.path
                )
                _ = url.port
            except ValueError:
                valid = False
            if not (valid and self.ldap_base.strip() and self.ldap_bind_dn.strip()):
                raise ValidationError("برای ورود سازمانی آدرس LDAPS، پایه جست‌وجو و شناسه اتصال لازم است.")


class GroupPolicy(Policy):
    group = models.OneToOneField("auth.Group", verbose_name="گروه", on_delete=models.CASCADE)
    priority = models.IntegerField("اولویت", default=0)

    class Meta:
        verbose_name = "سیاست گروه"
        verbose_name_plural = "سیاست‌های گروه"

    def __str__(self):
        return str(self.group)


class UserPolicy(Policy):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name="کاربر", on_delete=models.CASCADE)

    class Meta:
        verbose_name = "سیاست کاربر"
        verbose_name_plural = "سیاست‌های کاربر"

    def __str__(self):
        return str(self.user)


class DirectoryIdentity(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, verbose_name="کاربر", on_delete=models.CASCADE)
    username = models.CharField("نام حساب در دایرکتوری", max_length=150, unique=True)

    class Meta:
        verbose_name = "هویت دایرکتوری"
        verbose_name_plural = "هویت‌های دایرکتوری"

    def __str__(self):
        return self.username

    def clean(self):
        self.username = self.username.strip().lower()

    def save(self, *args, **kwargs):
        self.username = self.username.strip().lower()
        super().save(*args, **kwargs)


class Knowledge(models.Model):
    title = models.CharField("سؤال یا عنوان", max_length=250)
    content = models.TextField("پاسخ یا متن سند", max_length=262144)
    group = models.ForeignKey("auth.Group", verbose_name="گروه مجاز", on_delete=models.CASCADE, null=True, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="کاربر مجاز", on_delete=models.CASCADE, null=True, blank=True)
    source_name = models.CharField("نام فایل", max_length=250, blank=True)
    approved = models.BooleanField("منتشر شده", default=False)
    enabled = models.BooleanField("فعال", default=True)

    class Meta:
        verbose_name = "مدخل دانش"
        verbose_name_plural = "پایگاه دانش"

    def __str__(self):
        return self.title


class Conversation(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="کاربر", on_delete=models.CASCADE)
    title = models.CharField("عنوان", max_length=120)
    created_at = models.DateTimeField("ایجاد", auto_now_add=True)
    updated_at = models.DateTimeField("آخرین پیام", auto_now=True)

    class Meta:
        verbose_name = "گفتگو"
        verbose_name_plural = "گفتگوها"
        indexes = [models.Index(fields=["user", "-updated_at"], name="conversation_recent")]

    def __str__(self):
        return self.title


class Audit(models.Model):
    class Event(models.TextChoices):
        CHAT = "chat", "پرسش"
        UPLOAD = "upload", "بارگذاری"

    class Status(models.TextChoices):
        PENDING = "pending", "در حال پردازش"
        DONE = "done", "موفق"
        FAILED = "failed", "ناموفق"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="کاربر", on_delete=models.SET_NULL, null=True)
    actor = models.CharField("نام حساب", max_length=150)
    kind = models.CharField("رویداد", max_length=10, choices=Event)
    status = models.CharField("وضعیت", max_length=10, choices=Status, default=Status.PENDING)
    created_at = models.DateTimeField("زمان", auto_now_add=True)
    question = models.TextField("پرسش یا نام فایل", blank=True)
    answer = models.TextField("پاسخ یا نتیجه", blank=True)
    mode = models.CharField("سیاست", max_length=12, choices=Policy.Mode, blank=True)
    model = models.CharField("مدل", max_length=200, blank=True)
    conversation = models.ForeignKey(Conversation, verbose_name="گفتگو", on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name="turns")
    sources = models.JSONField("منابع", default=list, blank=True)

    class Meta:
        verbose_name = "رویداد"
        verbose_name_plural = "گزارش فعالیت"
        indexes = [models.Index(fields=["user", "kind", "created_at"])]

    def __str__(self):
        return self.get_kind_display()
