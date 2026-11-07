from django import template
from django.contrib.auth.models import User

from .. import checks
from ..models import Audit, Knowledge

register = template.Library()

SECTIONS = (
    ("knowledge", "دانش و گزارش", "محتوای قابل استفاده و فعالیت کاربران را بررسی کنید.", (
        ("Knowledge", "انتشار سؤال‌وجواب، مدیریت فایل‌ها و تعیین دامنه دسترسی"),
        ("Audit", "مشاهده پرسش‌ها، پاسخ‌ها و بارگذاری‌ها"),
    )),
    ("access", "کاربران و دسترسی", "حساب‌ها، گروه‌ها و ارتباط آن‌ها با دایرکتوری را مدیریت کنید.", (
        ("User", "ساخت و مدیریت حساب‌های کاربری"),
        ("Group", "دسته‌بندی کاربران و تخصیص مجوزها"),
        ("DirectoryIdentity", "اتصال حساب Nexa به نام کاربری در Active Directory"),
    )),
    ("settings", "پیکربندی", "رفتار دستیار و روش ورود را تنظیم کنید.", (
        ("Organization", "روش ورود، اتصال AD و سیاست سراسری"),
        ("Model", "سرویس‌های پاسخ‌گویی، مدل‌ها و کلیدهای API"),
        ("GroupPolicy", "حالت پاسخ، مدل و سهمیه برای گروه"),
        ("UserPolicy", "تنظیم‌های اختصاصی هر کاربر"),
    )),
)


@register.simple_tag(takes_context=True)
def dashboard(context, app_list):
    user = context["request"].user
    items = {model["object_name"]: model for app in app_list for model in app["models"]}
    sections = []
    for slug, title, description, entries in SECTIONS:
        links = [{**items[name], "description": detail} for name, detail in entries if name in items]
        if links:
            sections.append({"slug": slug, "title": title, "description": description, "links": links})
    stats = []
    if items.get("Knowledge", {}).get("admin_url"):
        url = items["Knowledge"]["admin_url"]
        stats.extend([
            ("دانش منتشرشده", Knowledge.objects.filter(approved=True, enabled=True).count(), url),
            ("در انتظار انتشار", Knowledge.objects.filter(approved=False, enabled=True).count(), url + "?approved__exact=0&enabled__exact=1"),
        ])
    if items.get("User", {}).get("admin_url"):
        stats.append(("کاربران فعال", User.objects.filter(is_active=True).count(), items["User"]["admin_url"]))
    if items.get("Audit", {}).get("admin_url"):
        stats.append(("رویدادهای ثبت‌شده", Audit.objects.count(), items["Audit"]["admin_url"]))
    pending, recent, integrations, checked_at = [], [], [], None
    if items.get("Knowledge", {}).get("perms", {}).get("change"):
        pending = list(Knowledge.objects.filter(approved=False, enabled=True)
                       .select_related("user", "group").order_by("-pk")[:6])
    if items.get("Audit", {}).get("admin_url"):
        recent = list(Audit.objects.order_by("-created_at")[:6])
    if user.is_superuser:
        integrations, checked_at = checks.status()
    return {
        "sections": sections, "stats": stats, "knowledge_add": items.get("Knowledge", {}).get("add_url"),
        "pending": pending, "recent": recent, "integrations": integrations, "checked_at": checked_at,
    }


@register.filter
def persian_digits(value):
    return str(value).translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))


@register.filter
def markdown(value):
    from ..markup import render

    return render(value)
