import ssl

from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import path
from django.views.decorators.http import require_POST

from . import checks

from .credentials import seal
from .models import Audit, Conversation, DirectoryIdentity, GroupPolicy, Knowledge, Model, Organization, UserPolicy

admin.site.site_header = "مدیریت نکسا"
admin.site.site_title = "مدیریت نکسا"
admin.site.index_title = "تنظیمات و گزارش‌های سازمان"


class SuperuserOnly:
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


admin.site.unregister(User)
admin.site.unregister(Group)


@admin.register(User)
class NexaUserAdmin(SuperuserOnly, UserAdmin):
    pass


@admin.register(Group)
class NexaGroupAdmin(SuperuserOnly, GroupAdmin):
    pass


@admin.register(DirectoryIdentity, UserPolicy, GroupPolicy)
class SettingsAdmin(SuperuserOnly, admin.ModelAdmin):
    pass


class CredentialForm(forms.ModelForm):
    secret = forms.CharField(required=False, strip=False, max_length=4096, widget=forms.PasswordInput)
    remove_secret = forms.BooleanField(label="پاک کردن مقدار ذخیره‌شده در پنل", required=False)
    secret_field = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        stored = bool(getattr(self.instance, self.secret_field))
        self.fields["secret"].help_text = (
            "مقدار در پنل ذخیره شده است. برای حفظ آن این کادر را خالی بگذارید."
            if stored else "مقداری در پنل ذخیره نشده است."
        )

    def clean(self):
        data = super().clean()
        if data.get("secret") and data.get("remove_secret"):
            self.add_error("remove_secret", "برای جایگزینی، گزینه پاک کردن را غیرفعال کنید.")
        return data

    def save(self, commit=True):
        secret = self.cleaned_data.get("secret")
        if secret:
            setattr(self.instance, self.secret_field, seal(secret))
        elif self.cleaned_data.get("remove_secret"):
            setattr(self.instance, self.secret_field, "")
        return super().save(commit=commit)


class ModelForm(CredentialForm):
    secret_field = "api_key_ciphertext"

    class Meta:
        model = Model
        fields = ("name", "endpoint", "identifier", "enabled", "secret", "remove_secret", "key_env")
        labels = {"key_env": "متغیر محیطی قدیمی (اختیاری)"}
        help_texts = {"key_env": "کلید ذخیره‌شده در پنل در اولویت است."}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secret"].label = "کلید API مدل"

    def clean_secret(self):
        value = self.cleaned_data["secret"].strip()
        if any(ord(character) < 33 or ord(character) > 126 for character in value):
            raise forms.ValidationError("کلید API باید بدون فاصله و نویسه‌های کنترلی باشد.")
        return value


class OrganizationForm(CredentialForm):
    secret_field = "ldap_password_ciphertext"
    ldap_ca_pem = forms.CharField(
        label="گواهی CA برای LDAPS (PEM)", required=False, max_length=65536,
        widget=forms.Textarea(attrs={"rows": 6, "dir": "ltr"}),
        help_text="متن گواهی عمومی CA را وارد کنید. در صورت خالی بودن، تنظیم قبلی سرور یا گواهی‌های سیستم استفاده می‌شوند.",
    )

    class Meta:
        model = Organization
        fields = (
            "auth_mode", "ldap_url", "ldap_base", "ldap_bind_dn", "secret", "remove_secret", "ldap_ca_pem",
            "mode", "default_model", "choose_model", "daily", "monthly", "upload",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["secret"].label = "رمز حساب اتصال AD"

    def clean_ldap_ca_pem(self):
        pem = self.cleaned_data["ldap_ca_pem"]
        if pem:
            try:
                ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cadata=pem)
            except (ValueError, ssl.SSLError) as error:
                raise forms.ValidationError("گواهی معتبر PEM وارد کنید.") from error
        return pem


@admin.register(Model)
class ModelAdmin(SettingsAdmin):
    form = ModelForm
    fieldsets = (
        ("مشخصات مدل", {"fields": ("name", "endpoint", "identifier", "enabled")}),
        ("احراز هویت سرویس", {"fields": ("secret", "remove_secret", "key_env")}),
    )


@admin.register(Organization)
class OrganizationAdmin(SettingsAdmin):
    form = OrganizationForm
    fieldsets = (
        ("ورود کاربران", {"fields": ("auth_mode",)}),
        ("اتصال به Active Directory", {
            "description": "برای ورود داخلی، این بخش را خالی بگذارید.",
            "fields": ("ldap_url", "ldap_base", "ldap_bind_dn", "secret", "remove_secret", "ldap_ca_pem"),
        }),
        ("رفتار پیش‌فرض دستیار", {"fields": ("mode", "default_model", "choose_model")}),
        ("سهمیه و بارگذاری", {"fields": ("daily", "monthly", "upload")}),
    )

    def has_add_permission(self, request):
        return super().has_add_permission(request) and not Organization.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Knowledge)
class KnowledgeAdmin(admin.ModelAdmin):
    list_display = ("title", "group", "user", "approved", "enabled")
    list_filter = ("approved", "enabled")
    search_fields = ("title", "source_name")
    actions = ("publish_selected",)

    @admin.action(description="انتشار موارد انتخاب‌شده", permissions=("change",))
    def publish_selected(self, request, queryset):
        count = queryset.update(approved=True)
        self.message_user(request, f"{count} مدخل منتشر شد.")

    def get_urls(self):
        return [
            path("<int:pk>/publish/", self.admin_site.admin_view(self.publish_view), name="nexa_knowledge_publish"),
            *super().get_urls(),
        ]

    def publish_view(self, request, pk):
        if request.method != "POST":
            return redirect("admin:index")
        document = get_object_or_404(Knowledge, pk=pk)
        if not self.has_change_permission(request, document):
            raise PermissionDenied
        Knowledge.objects.filter(pk=pk).update(approved=True)
        self.message_user(request, f"«{document.title}» منتشر شد.")
        return redirect("admin:index")


@require_POST
def check_integrations(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    results = checks.run()
    failed = [check.name for check in results if check.state == "failed"]
    if failed:
        messages.warning(request, "آزمون اتصال برای این موارد ناموفق بود: " + "، ".join(failed))
    else:
        messages.success(request, "آزمون اتصال انجام شد.")
    return redirect("admin:index")


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "updated_at")
    search_fields = ("title", "user__username")
    readonly_fields = ("user", "title", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "kind", "status", "mode", "model")
    list_filter = ("kind", "status", "mode")
    search_fields = ("actor", "question", "answer")
    ordering = ("-created_at",)
    readonly_fields = ("created_at", "user", "actor", "kind", "status", "question", "answer", "mode", "model",
                       "conversation", "sources")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
