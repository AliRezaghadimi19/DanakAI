import json
import logging
from contextlib import closing
from pathlib import PurePath

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import BACKEND_SESSION_KEY, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.core.exceptions import SuspiciousOperation
from django.db import transaction
from django.http import JsonResponse, StreamingHttpResponse
from django.template.loader import render_to_string
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .directory import configuration
from .models import Audit, Conversation, Knowledge, Model, Organization, Policy
from .throttle import blocked
from .markup import render as render_markdown
from .services import ChatError, allowance, generate, policy_for, reserve, retrieve, stream

logger = logging.getLogger(__name__)


class SignIn(AuthenticationForm):
    method = forms.ChoiceField(label="روش ورود", widget=forms.RadioSelect)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mode = configuration().auth_mode
        choices = [
            (choice, "حساب سازمانی (AD)" if choice == Organization.Auth.AD else "حساب داخلی")
            for choice, _ in Organization.Auth.choices
            if choice != Organization.Auth.BOTH and mode in (choice, Organization.Auth.BOTH)
        ]
        field = self.fields["method"]
        field.choices = choices
        field.initial = choices[0][0]
        if len(choices) == 1:
            field.widget = forms.HiddenInput()
        self.fields["username"].widget.attrs.update({"dir": "ltr", "autocomplete": "username"})

    def clean(self):
        if blocked(self.request, self.data.get("username")):
            raise forms.ValidationError("تلاش‌های ناموفق ورود زیاد بوده است. چند دقیقه بعد دوباره امتحان کنید.")
        return super().clean()


class Login(LoginView):
    template_name = "login.html"
    authentication_form = SignIn
    next_page = reverse_lazy("chat")


class Question(forms.Form):
    question = forms.CharField(label="پرسش", max_length=4000, widget=forms.Textarea(attrs={
        "rows": 1, "placeholder": "پرسش خود را بنویسید…", "form": "composer",
    }))
    model = forms.ModelChoiceField(label="مدل", queryset=Model.objects.none(), required=False)

    def __init__(self, *args, policy, **kwargs):
        super().__init__(*args, **kwargs)
        if policy.mode == Policy.Mode.KNOWLEDGE:
            self.fields.pop("model")
        elif policy.choose_model:
            self.fields["model"].queryset = Model.objects.filter(enabled=True)
            self.fields["model"].empty_label = "مدل پیش‌فرض"
            self.fields["model"].widget.attrs["form"] = "composer"
        else:
            self.fields["model"].widget = forms.HiddenInput(attrs={"form": "composer"})


UPLOAD_LIMIT = 256 * 1024


class Upload(forms.Form):
    file = forms.FileField(label="فایل متنی", error_messages={"required": "فایلی انتخاب نشده است."})

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > UPLOAD_LIMIT or PurePath(upload.name).suffix.lower() not in (".txt", ".md"):
            raise forms.ValidationError("فقط فایل متنی یا Markdown تا ۲۵۶ کیلوبایت مجاز است.")
        try:
            content = upload.read().decode("utf-8-sig").strip()
        except UnicodeDecodeError as error:
            raise forms.ValidationError("فایل باید با کدگذاری UTF-8 ذخیره شده باشد.") from error
        if not content or "\x00" in content:
            raise forms.ValidationError("فایل باید حاوی متن معتبر باشد.")
        return upload.name[:250], content


def title_for(question):
    line = " ".join(question.split())
    return line if len(line) <= 60 else line[:59].rstrip() + "…"


INTERNAL_ERROR = "پاسخ‌گویی با خطای داخلی متوقف شد. دوباره تلاش کنید."
STOPPED_NOTE = "\n\n*پاسخ با توقف کاربر ناتمام ماند.*"


class Turn:
    """One question inside a conversation, from quota reservation to the stored answer.

    Creating a Turn raises ChatError when the request is refused before it is recorded (quota,
    model permission). After that every outcome is stored on the audit row: done, failed, or
    stopped by the reader part way through.
    """

    def __init__(self, user, question, model_id, conversation):
        with transaction.atomic():
            if conversation is None:
                conversation = Conversation.objects.create(user=user, title=title_for(question))
            self.history = [
                (turn.question, turn.answer)
                for turn in conversation.turns.filter(status=Audit.Status.DONE).order_by("-created_at")[:6]
            ][::-1]
            self.event, self.policy, self.model = reserve(user, question, model_id, conversation)
        self.user, self.question, self.conversation = user, question, conversation
        self.parts, self.sources, self.closed = [], [], False

    @property
    def answer(self):
        return "".join(self.parts)

    def pieces(self, produce):
        hits = retrieve(self.user, self.question) if self.policy.mode != Policy.Mode.FREE else []
        if self.policy.mode == Policy.Mode.KNOWLEDGE:
            self.sources = [title for title, _ in hits[:1]]
            yield hits[0][1] if hits else "پاسخ مرتبطی در دانش سازمان پیدا نشد."
            return
        self.sources = [title for title, _ in hits]
        yield from produce(self.model, self.question, hits, self.history)

    def finish(self, status, answer, sources=()):
        self.closed = True
        Audit.objects.filter(pk=self.event.pk).update(status=status, answer=answer, sources=list(sources))
        Conversation.objects.filter(pk=self.conversation.pk).update(updated_at=timezone.now())

    def run(self, produce):
        try:
            for piece in self.pieces(produce):
                self.parts.append(piece)
                yield piece
        except ChatError as error:
            self.finish(Audit.Status.FAILED, str(error))
            raise
        except Exception:
            logger.exception("Chat request %s failed unexpectedly", self.event.pk)
            self.finish(Audit.Status.FAILED, INTERNAL_ERROR)
            raise ChatError(INTERNAL_ERROR) from None
        else:
            self.finish(Audit.Status.DONE, self.answer, self.sources)
        finally:
            if not self.closed:
                if self.answer:
                    self.finish(Audit.Status.DONE, self.answer + STOPPED_NOTE, self.sources)
                else:
                    self.finish(Audit.Status.FAILED, "پاسخ با توقف کاربر لغو شد.")


def ask(user, question, model_id, conversation):
    """Answer without streaming (no-JavaScript fallback) and return the conversation."""
    turn = Turn(user, question, model_id, conversation)
    try:
        for _ in turn.run(lambda *args: [generate(*args)]):
            pass
    except ChatError:
        pass
    return turn.conversation


def sse(name, data):
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_events(request, turn, policy):
    yield sse("meta", {
        "conversation": turn.conversation.pk, "title": turn.conversation.title,
        "url": reverse("conversation", args=[turn.conversation.pk]),
    })
    try:
        with closing(turn.run(stream)) as pieces:
            for piece in pieces:
                yield sse("delta", {"text": piece})
    except ChatError as error:
        yield sse("error", {"message": str(error)})
        return
    yield sse("done", {
        "html": str(render_markdown(turn.answer)), "sources": turn.sources,
        "quota": render_to_string("quota.html", {"allowance": allowance(turn.user, policy)}, request),
    })


@never_cache
@login_required
@require_POST
def stream_answer(request):
    if refuse_mismatched_session(request):
        return JsonResponse({"error": "نشست شما برای دستیار معتبر نیست. دوباره وارد شوید."}, status=403)
    conversation = None
    if pk := request.POST.get("conversation"):
        if not pk.isdigit():
            raise SuspiciousOperation
        conversation = get_object_or_404(Conversation, pk=int(pk), user=request.user)
    policy = policy_for(request.user)
    question = Question(request.POST, policy=policy)
    if not question.is_valid():
        return JsonResponse({"error": next(iter(question.errors.values()))[0]}, status=400)
    model = question.cleaned_data.get("model")
    try:
        turn = Turn(request.user, question.cleaned_data["question"], model.pk if model else None, conversation)
    except ChatError as error:
        return JsonResponse({"error": str(error)}, status=409)
    response = StreamingHttpResponse(stream_events(request, turn, policy), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@transaction.atomic
def save_upload(user, filename, content):
    if not policy_for(user).upload:
        raise ChatError("اجازه بارگذاری فایل ندارید.")
    pending = Knowledge.objects.filter(user=user, approved=False, enabled=True).count()
    if pending >= settings.NEXA_MAX_PENDING_UPLOADS:
        raise ChatError("تعداد فایل‌های در انتظار تأیید شما به سقف رسیده است. پس از بررسی مدیر دوباره تلاش کنید.")
    Knowledge.objects.create(title=filename, source_name=filename, user=user, content=content)
    Audit.objects.create(user=user, actor=user.get_username(), kind=Audit.Event.UPLOAD,
                         question=filename, status=Audit.Status.DONE, answer="در انتظار تأیید")


def refuse_mismatched_session(request):
    auth_mode = configuration().auth_mode
    method = "ad" if request.session.get(BACKEND_SESSION_KEY, "").endswith(".DirectoryBackend") else "local"
    directory_account = hasattr(request.user, "directoryidentity")
    if auth_mode in (method, Organization.Auth.BOTH) and directory_account == (method == "ad"):
        return None
    if request.user.is_staff and method == "local" and not directory_account:
        messages.info(request, "دستیار با روش ورود فعلی سازمان در دسترس این حساب نیست. پنل مدیریت همچنان باز است.")
        return redirect("admin:index")
    logout(request)
    return redirect("login")


def upload_file(request, policy):
    if not policy.upload:
        raise ChatError("اجازه بارگذاری فایل ندارید.")
    if "file" not in request.FILES and int(request.META.get("CONTENT_LENGTH") or 0) > UPLOAD_LIMIT:
        raise ChatError("حجم فایل بیش از ۲۵۶ کیلوبایت است.")
    upload = Upload(request.POST, request.FILES)
    if not upload.is_valid():
        raise ChatError(upload.errors["file"][0])
    save_upload(request.user, *upload.cleaned_data["file"])
    messages.success(request, "فایل ثبت شد و در انتظار تأیید مدیر است.")


def history_groups(user):
    today = timezone.localdate()
    groups = {"امروز": [], "هفت روز گذشته": [], "قدیمی‌تر": []}
    for conversation in Conversation.objects.filter(user=user).order_by("-updated_at")[:60]:
        age = (today - timezone.localdate(conversation.updated_at)).days
        groups["امروز" if age < 1 else "هفت روز گذشته" if age < 7 else "قدیمی‌تر"].append(conversation)
    return [(label, items) for label, items in groups.items() if items]


@never_cache
@login_required
@require_http_methods(["GET", "POST"])
def chat(request, pk=None):
    if refusal := refuse_mismatched_session(request):
        return refusal
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user) if pk else None
    policy = policy_for(request.user)
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "upload":
                upload_file(request, policy)
            elif action == "ask":
                question = Question(request.POST, policy=policy)
                if not question.is_valid():
                    raise ChatError(next(iter(question.errors.values()))[0])
                model = question.cleaned_data.get("model")
                conversation = ask(request.user, question.cleaned_data["question"],
                                   model.pk if model else None, conversation)
            else:
                raise SuspiciousOperation
        except ChatError as exc:
            messages.error(request, str(exc))
        return redirect("conversation", pk=conversation.pk) if conversation else redirect("chat")
    default_model = None
    if policy.mode != Policy.Mode.KNOWLEDGE and policy.default_model_id:
        default_model = Model.objects.filter(pk=policy.default_model_id, enabled=True).first()
    turns = conversation.turns.order_by("created_at") if conversation else []
    return render(request, "chat.html", {
        "conversation": conversation, "turns": turns, "history": history_groups(request.user),
        "default_model": default_model, "question": Question(policy=policy), "can_upload": policy.upload,
        "allowance": allowance(request.user, policy), "mode": Policy.Mode(policy.mode).label,
    })


@login_required
@require_POST
def delete_conversation(request, pk):
    get_object_or_404(Conversation, pk=pk, user=request.user).delete()
    messages.success(request, "گفتگو حذف شد.")
    return redirect("chat")


@require_GET
def health(request):
    return JsonResponse({"status": "ok"})
