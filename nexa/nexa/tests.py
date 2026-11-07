import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.cache import cache
from django.test import Client, TestCase, override_settings

from .models import Audit, Conversation, DirectoryIdentity, GroupPolicy, Knowledge, Model, Organization, Policy, UserPolicy
from .services import policy_for


LOCAL_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCAL_CACHE)
class NexaFlowTests(TestCase):
    def setUp(self):
        cache.clear()

    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("member", password="test-passphrase-only")
        cls.organization = Organization.objects.create(mode=Policy.Mode.KNOWLEDGE, daily=2, monthly=3)

    def ask(self, data):
        return self.client.post("/", data, follow=True)

    def sign_in(self):
        response = self.client.post("/login/", {
            "username": "member", "password": "test-passphrase-only", "method": "local",
        })
        self.assertRedirects(response, "/")

    def test_local_login_rtl_and_knowledge_with_quota_and_scope(self):
        self.assertContains(self.client.get("/login/"), 'lang="fa" dir="rtl"')
        self.sign_in()
        Knowledge.objects.create(title="مرخصی سالانه", content="اطلاعات محرمانه",
                                 user=User.objects.create_user("other"), approved=True)
        Knowledge.objects.create(title="مرخصی سالانه", content="راهنمای سازمان", approved=True)
        with patch("nexa.views.generate") as provider:
            first = self.ask({"action": "ask", "question": "مرخصی سالانه"})
            self.assertContains(first, "راهنمای سازمان")
            self.assertNotContains(first, "اطلاعات محرمانه")
            self.assertContains(first, 'data-number="1"')
            missing = self.ask({"action": "ask", "question": "سیاره زحل"})
            self.assertContains(missing, "پاسخ مرتبطی")
            denied = self.ask({"action": "ask", "question": "مرخصی سالانه"})
            self.assertContains(denied, "سهمیه پرسش شما به پایان رسیده است.")
            provider.assert_not_called()
        self.assertEqual(Audit.objects.filter(status=Audit.Status.DONE).count(), 2)

    def test_group_and_user_fields_inherit_separately(self):
        high, low = Group.objects.create(name="high"), Group.objects.create(name="low")
        self.user.groups.add(high, low)
        GroupPolicy.objects.create(group=low, daily=9, monthly=4, priority=1)
        GroupPolicy.objects.create(group=high, daily=7, upload=True, priority=2)
        UserPolicy.objects.create(user=self.user, upload=False, daily=0)
        policy = policy_for(self.user)
        self.assertEqual((policy.daily, policy.monthly, policy.upload), (0, 4, False))

    def test_upload_permission_approval_and_audit(self):
        self.sign_in()
        data = "پشتیبانی شبکه سازمان".encode()
        denied = self.ask({"action": "upload", "file": SimpleUploadedFile("guide.txt", data)})
        self.assertContains(denied, "اجازه بارگذاری فایل ندارید.")
        UserPolicy.objects.create(user=self.user, upload=True)
        invalid = self.ask({"action": "upload", "file": SimpleUploadedFile("guide.pdf", data)})
        self.assertContains(invalid, "فقط فایل متنی")
        uploaded = self.ask({"action": "upload", "file": SimpleUploadedFile("guide.txt", data)})
        self.assertContains(uploaded, "در انتظار تأیید مدیر")
        document = Knowledge.objects.get(source_name="guide.txt")
        self.assertFalse(document.approved)
        self.assertEqual(document.user, self.user)
        self.assertEqual(Audit.objects.get(kind=Audit.Event.UPLOAD).question, "guide.txt")
        self.assertContains(self.ask({"action": "ask", "question": "پشتیبانی شبکه"}), "پاسخ مرتبطی")
        document.approved = True
        document.save()
        self.assertContains(self.ask({"action": "ask", "question": "پشتیبانی شبکه"}), "پشتیبانی شبکه سازمان")

    def test_hybrid_and_free_use_openai_wire_protocol_and_enforce_model_selection(self):
        calls = []

        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"choices": [{"message": {"content": "پاسخ آزمایشی"}}]}).encode())

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = f"http://127.0.0.1:{server.server_port}/api/agents/v1/chat/completions"
                model = Model.objects.create(name="مدل اول", endpoint=endpoint, identifier="agent_test")
                other = Model.objects.create(name="مدل دوم", endpoint=endpoint, identifier="agent_other")
                self.organization.mode = Policy.Mode.HYBRID
                self.organization.default_model = model
                self.organization.save()
                Knowledge.objects.create(title="راهنمای شبکه", content="متن اختصاصی", approved=True)
                self.sign_in()
                self.ask({"action": "ask", "question": "راهنمای شبکه", "model": other.pk})
                self.assertEqual(calls, [])
                hybrid = self.ask({"action": "ask", "question": "راهنمای شبکه"})
                self.assertContains(hybrid, "پاسخ آزمایشی")
                self.assertEqual(calls[0][1]["model"], "agent_test")
                self.assertIn("متن اختصاصی", calls[0][1]["messages"][-1]["content"])
                UserPolicy.objects.create(user=self.user, mode=Policy.Mode.FREE, choose_model=True)
                free = self.ask({"action": "ask", "question": "راهنمای شبکه", "model": other.pk})
                self.assertContains(free, "پاسخ آزمایشی")
                self.assertEqual(calls[1][1]["model"], "agent_other")
                self.assertNotIn("متن اختصاصی", calls[1][1]["messages"][-1]["content"])
            finally:
                server.shutdown()
                thread.join()

    def test_auth_mode_change_revokes_local_chat_access_but_admin_can_recover(self):
        self.sign_in()
        self.organization.auth_mode = Organization.Auth.AD
        self.organization.save()
        self.assertRedirects(self.client.get("/"), "/login/")
        local = self.client.post("/login/", {"username": "member", "password": "test-passphrase-only", "method": "local"})
        self.assertEqual(local.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        User.objects.create_superuser("root", password="admin-test-passphrase")
        admin = self.client.post("/admin/login/", {"username": "root", "password": "admin-test-passphrase"})
        self.assertRedirects(admin, "/admin/")

    def test_directory_account_cannot_use_local_password(self):
        DirectoryIdentity.objects.create(user=self.user, username="MEMBER")
        self.client.post("/login/", {"username": "member", "password": "test-passphrase-only", "method": "local"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_admin_and_csrf_protect_sensitive_actions(self):
        self.sign_in()
        self.assertRedirects(self.client.get("/admin/nexa/audit/"), "/admin/login/?next=/admin/nexa/audit/",
                             fetch_redirect_response=False)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.user)
        response = protected.post("/", {"action": "ask", "question": "متن"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Audit.objects.exists())

    def test_directory_login_searches_then_binds_over_verified_tls(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        self.organization.auth_mode = Organization.Auth.BOTH
        self.organization.ldap_url = "ldaps://directory.example.com"
        self.organization.ldap_base = "dc=example,dc=com"
        self.organization.ldap_bind_dn = "cn=service,dc=example,dc=com"
        self.organization.full_clean()
        self.organization.save()
        identity = DirectoryIdentity.objects.create(user=self.user, username="Member")
        user_dn = "cn=Member,dc=example,dc=com"
        search, user_bind = MagicMock(), MagicMock()
        search.__enter__.return_value = search
        search.entries = [SimpleNamespace(entry_dn=user_dn)]
        with patch.dict("os.environ", {"NEXA_LDAP_BIND_PASSWORD": "service-test-secret"}):
            with patch("nexa.directory.Connection", side_effect=[search, user_bind]) as connect:
                response = self.client.post("/login/", {
                    "username": "Member", "password": "directory-test-password", "method": "ad",
                })
        self.assertRedirects(response, "/")
        self.assertEqual(identity.username, "member")
        search.search.assert_called_once_with(
            "dc=example,dc=com", "(&(objectClass=user)(sAMAccountName=member))",
            attributes=["distinguishedName"], size_limit=2, time_limit=5,
        )
        first, second = connect.call_args_list
        self.assertTrue(first.args[0].ssl)
        self.assertFalse(first.kwargs["auto_referrals"])
        self.assertEqual(second.args[1:], (user_dn, "directory-test-password"))

    def test_monthly_limit_is_enforced_even_when_daily_limit_allows_more(self):
        self.organization.monthly = 1
        self.organization.save()
        self.sign_in()
        self.ask({"action": "ask", "question": "پرسش اول"})
        response = self.ask({"action": "ask", "question": "پرسش دوم"})
        self.assertContains(response, "سهمیه پرسش شما به پایان رسیده است.")
        self.assertEqual(Audit.objects.count(), 1)

    def test_failed_model_call_does_not_consume_quota(self):
        self.organization.mode = Policy.Mode.FREE
        self.organization.daily = 1
        self.organization.default_model = Model.objects.create(
            name="خاموش", endpoint="http://127.0.0.1:9/v1/chat/completions", identifier="down")
        self.organization.save()
        self.sign_in()
        with self.assertLogs("nexa.services", "WARNING"):
            failed = self.ask({"action": "ask", "question": "پرسش"})
        self.assertContains(failed, "دریافت پاسخ از مدل ممکن نشد.")
        self.assertContains(failed, 'data-number="1"')
        with patch("nexa.views.generate", side_effect=RuntimeError("boom")), self.assertLogs("nexa.views", "ERROR"):
            crashed = self.ask({"action": "ask", "question": "پرسش"})
        self.assertContains(crashed, "خطای داخلی")
        self.assertNotContains(crashed, "boom")
        self.assertEqual(Audit.objects.filter(status=Audit.Status.FAILED).count(), 2)
        self.assertContains(crashed, 'data-number="1"')

    def test_knowledge_answer_is_returned_in_full(self):
        content = "الف " * 2000
        Knowledge.objects.create(title="راهنمای طولانی", content=content, approved=True)
        self.sign_in()
        response = self.ask({"action": "ask", "question": "راهنمای طولانی"})
        self.assertContains(response, content.strip())

    def test_question_redirects_into_private_conversation_with_history(self):
        self.organization.mode = Policy.Mode.FREE
        self.organization.default_model = Model.objects.create(name="m", endpoint="http://x.invalid/", identifier="m")
        self.organization.save()
        self.sign_in()
        with patch("nexa.views.generate", side_effect=["**پاسخ اول** <script>x</script>", "پاسخ دوم"]) as provider:
            response = self.client.post("/", {"action": "ask", "question": "پرسش اول"})
            conversation = Conversation.objects.get()
            self.assertRedirects(response, f"/c/{conversation.pk}/")
            page = self.client.get(response.url)
            self.assertContains(page, "<strong>پاسخ اول</strong> &lt;script&gt;", html=False)
            self.client.post(response.url, {"action": "ask", "question": "پرسش دوم"})
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(provider.call_args.args[3], [("پرسش اول", "**پاسخ اول** <script>x</script>")])
        self.assertEqual(conversation.turns.count(), 2)
        other = Client()
        other.force_login(User.objects.create_user("stranger"))
        self.assertEqual(other.get(response.url).status_code, 404)
        self.assertEqual(other.post(f"/c/{conversation.pk}/delete/").status_code, 404)
        self.client.post(f"/c/{conversation.pk}/delete/")
        self.assertFalse(Conversation.objects.exists())
        self.assertEqual(Audit.objects.filter(kind=Audit.Event.CHAT).count(), 2)

    def test_refused_question_leaves_no_empty_conversation(self):
        self.organization.daily = 0
        self.organization.save()
        self.sign_in()
        response = self.ask({"action": "ask", "question": "پرسش"})
        self.assertContains(response, "سهمیه پرسش شما به پایان رسیده است.")
        self.assertFalse(Conversation.objects.exists())

    @override_settings(NEXA_MAX_PENDING_UPLOADS=1)
    def test_upload_limits_pending_files_and_reports_oversized_file(self):
        UserPolicy.objects.create(user=self.user, upload=True)
        self.sign_in()
        big = self.ask({"action": "upload", "file": SimpleUploadedFile("big.txt", b"a" * (300 * 1024))})
        self.assertContains(big, "۲۵۶ کیلوبایت")
        self.ask({"action": "upload", "file": SimpleUploadedFile("one.txt", "متن".encode())})
        second = self.ask({"action": "upload", "file": SimpleUploadedFile("two.txt", "متن".encode())})
        self.assertContains(second, "به سقف رسیده است")
        self.assertEqual(Knowledge.objects.count(), 1)

    def test_staff_keeps_admin_session_when_chat_requires_directory_login(self):
        User.objects.create_superuser("root", password="admin-test-passphrase")
        self.client.post("/admin/login/", {"username": "root", "password": "admin-test-passphrase"})
        self.organization.auth_mode = Organization.Auth.AD
        self.organization.save()
        self.assertRedirects(self.client.get("/"), "/admin/")
        self.assertIn("_auth_user_id", self.client.session)

    def test_admin_dashboard_publishes_pending_knowledge_and_shows_integrations(self):
        root = User.objects.create_superuser("root", password="admin-test-passphrase")
        self.client.force_login(root)
        document = Knowledge.objects.create(title="سند تازه", content="متن")
        page = self.client.get("/admin/")
        self.assertContains(page, "سند تازه")
        self.assertContains(page, "وضعیت اتصال‌ها")
        self.assertContains(page, "مدل فعالی ثبت نشده است.")
        self.assertEqual(self.client.get(f"/admin/nexa/knowledge/{document.pk}/publish/").status_code, 302)
        document.refresh_from_db()
        self.assertFalse(document.approved)
        self.client.post(f"/admin/nexa/knowledge/{document.pk}/publish/")
        document.refresh_from_db()
        self.assertTrue(document.approved)
        member = Client()
        member.force_login(self.user)
        self.assertEqual(member.post("/admin/integrations/check/").status_code, 302)
        self.assertEqual(self.client.post("/admin/integrations/check/", follow=True).status_code, 200)

    def streaming_provider(self, pieces):
        class Provider(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for piece in pieces:
                    chunk = {"choices": [{"delta": {"content": piece}}]}
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.organization.mode = Policy.Mode.FREE
        self.organization.default_model = Model.objects.create(
            name="stream", endpoint=f"http://127.0.0.1:{server.server_port}/v1/chat/completions", identifier="s")
        self.organization.save()

    @staticmethod
    def events(response):
        parsed = []
        for block in b"".join(response.streaming_content).decode().strip().split("\n\n"):
            name, data = block.split("\n")
            parsed.append((name.removeprefix("event: "), json.loads(data.removeprefix("data: "))))
        return parsed

    def test_streaming_answer_sends_events_and_stores_turn(self):
        self.streaming_provider(["سلام ", "**دنیا**"])
        self.sign_in()
        response = self.client.post("/stream/", {"question": "پرسش"})
        self.assertEqual(response["Content-Type"], "text/event-stream")
        events = self.events(response)
        conversation = Conversation.objects.get()
        self.assertEqual(events[0], ("meta", {"conversation": conversation.pk, "title": "پرسش",
                                              "url": f"/c/{conversation.pk}/"}))
        self.assertEqual([data["text"] for name, data in events if name == "delta"], ["سلام ", "**دنیا**"])
        self.assertEqual(events[-1][0], "done")
        self.assertIn("<strong>دنیا</strong>", events[-1][1]["html"])
        self.assertIn('data-number="1"', events[-1][1]["quota"])
        turn = Audit.objects.get()
        self.assertEqual((turn.status, turn.answer, turn.conversation), (Audit.Status.DONE, "سلام **دنیا**", conversation))

    def test_streaming_refusal_returns_json_before_recording(self):
        self.organization.daily = 0
        self.organization.save()
        self.sign_in()
        response = self.client.post("/stream/", {"question": "پرسش"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"error": "سهمیه پرسش شما به پایان رسیده است."})
        self.assertFalse(Conversation.objects.exists())
        other = Conversation.objects.create(user=User.objects.create_user("stranger"), title="x")
        self.assertEqual(self.client.post("/stream/", {"question": "پرسش", "conversation": other.pk}).status_code, 404)

    def test_reader_stopping_the_stream_keeps_partial_answer(self):
        self.streaming_provider(["بخش اول ", "بخش دوم"])
        self.sign_in()
        response = self.client.post("/stream/", {"question": "پرسش"})
        content = iter(response.streaming_content)
        next(content)
        next(content)
        response.close()
        turn = Audit.objects.get()
        self.assertEqual(turn.status, Audit.Status.DONE)
        self.assertTrue(turn.answer.startswith("بخش اول "))
        self.assertIn("ناتمام", turn.answer)

    def test_index_ranks_like_full_scan_and_follows_content_changes(self):
        from .services import retrieve

        manual = "مقدمه " * 300 + "راهنمای شبکه بی‌سیم دفتر مرکزی"
        Knowledge.objects.create(title="manual.txt", source_name="manual.txt", content=manual, approved=True)
        qa = Knowledge.objects.create(title="شبکه بی‌سیم دفتر", content="پاسخ کوتاه", approved=True)
        Knowledge.objects.create(title="شبکه دفتر", content="غیرفعال", approved=True, enabled=False)
        hits = retrieve(self.user, "شبکه بی‌سیم دفتر")
        self.assertEqual([title for title, _ in hits], ["شبکه بی‌سیم دفتر", "manual.txt"])
        self.assertIn("راهنمای شبکه بی‌سیم", hits[1][1])
        self.assertEqual(len(hits[1][1]), len(manual) - 1600)
        qa.title = "تلفن داخلی"
        qa.save()
        self.assertEqual([title for title, _ in retrieve(self.user, "شبکه بی‌سیم دفتر")], ["manual.txt"])
        Knowledge.objects.filter(source_name="manual.txt").delete()
        self.assertEqual(retrieve(self.user, "شبکه بی‌سیم دفتر"), [])
        with self.assertNumQueries(2):
            self.assertEqual([title for title, _ in retrieve(self.user, "تلفن داخلی")], ["تلفن داخلی"])

    def test_persian_terms_fold_letter_variants_digits_and_zwnj(self):
        from .services import terms

        self.assertEqual(terms("مي‌خواهم كتاب ۱۲"), terms("میخواهم کتاب 12"))

    @override_settings(NEXA_LOGIN_MAX_ATTEMPTS=3)
    def test_failed_logins_are_throttled_per_account_before_any_directory_bind(self):
        bad = {"username": "Member", "password": "wrong-password", "method": "local"}
        for _ in range(3):
            self.assertEqual(self.client.post("/login/", bad).status_code, 200)
        self.organization.auth_mode = Organization.Auth.BOTH
        self.organization.save()
        with patch("nexa.directory.Connection") as connect:
            response = self.client.post("/login/", {**bad, "method": "ad"})
        connect.assert_not_called()
        self.assertContains(response, "تلاش‌های ناموفق ورود زیاد بوده است.")
        good = {"username": "member", "password": "test-passphrase-only", "method": "local"}
        self.assertEqual(self.client.post("/login/", good).status_code, 200)
        admin = self.client.post("/admin/login/", {"username": "member", "password": "test-passphrase-only"})
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(admin.status_code, 200)
        cache.clear()
        self.sign_in()
