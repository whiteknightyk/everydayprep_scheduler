import unittest

from starlette.requests import Request

from app import safe_return_path, switch_language, templates
from i18n import LANGUAGE_COOKIE, template_context, translate, weekday_labels


def make_request(
    path: str = "/",
    query_string: str = "",
    language: str | None = None,
) -> Request:
    headers = []
    if language:
        headers.append(
            (b"cookie", f"{LANGUAGE_COOKIE}={language}".encode("ascii"))
        )
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": path,
            "query_string": query_string.encode("ascii"),
            "headers": headers,
        }
    )


class LanguageSwitchTests(unittest.TestCase):
    def test_english_cookie_renders_english_ui_and_japanese_switch(self):
        request = make_request("/users", language="en")

        response = templates.TemplateResponse(
            request,
            "users.html",
            {"users": []},
        )
        html = response.body.decode()

        self.assertIn('<html lang="en">', html)
        self.assertIn("Add User", html)
        self.assertIn("Registered Users", html)
        self.assertIn('class="language-toggle"', html)
        self.assertIn('href="/language/ja?next=%2Fusers"', html)
        self.assertIn(">日本語</a>", html)
        self.assertNotIn("新規登録", html)

    def test_switch_url_preserves_the_current_query_string(self):
        request = make_request("/", "student_id=4")

        context = template_context(request)

        self.assertEqual(context["lang"], "ja")
        self.assertEqual(
            context["language_switch_url"],
            "/language/en?next=%2F%3Fstudent_id%3D4",
        )

    def test_main_pages_render_their_english_headings(self):
        request = make_request(language="en")
        pages = (
            (
                "dashboard.html",
                {
                    "stats": {},
                    "students": [],
                    "selected_student_id": None,
                    "lesson_groups": [],
                    "fmt_local": lambda value, timezone: value,
                },
                "Lessons",
            ),
            (
                "availability.html",
                {
                    "users": [],
                    "selected": None,
                    "rows": [],
                    "exceptions": [],
                    "fmt_local": lambda value, timezone: value,
                    "weekdays": weekday_labels("en"),
                },
                "Availability &amp; Exceptions",
            ),
            (
                "lesson_new.html",
                {"instructors": [], "students": []},
                "Create a Lesson",
            ),
            (
                "register.html",
                {
                    "error": None,
                    "form_values": {
                        "role": "student",
                        "timezone_name": "Asia/Tokyo",
                    },
                },
                "Create Account",
            ),
        )

        for template_name, context, heading in pages:
            with self.subTest(template=template_name):
                html = templates.TemplateResponse(
                    request,
                    template_name,
                    context,
                ).body.decode()
                self.assertIn(heading, html)

    def test_language_route_sets_cookie_and_returns_to_current_page(self):
        response = switch_language("en", "/availability?user_id=4")

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/availability?user_id=4")
        cookie = response.headers["set-cookie"]
        self.assertIn(f"{LANGUAGE_COOKIE}=en", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)

    def test_lesson_creation_uses_content_label(self):
        context = {
            "instructors": [
                {"id": 1, "name": "講師", "role": "instructor", "timezone": "Asia/Tokyo"}
            ],
            "students": [{"id": 2, "name": "生徒", "timezone": "Asia/Tokyo"}],
        }

        japanese_html = templates.TemplateResponse(
            make_request(language="ja"),
            "lesson_new.html",
            context,
        ).body.decode()
        english_html = templates.TemplateResponse(
            make_request(language="en"),
            "lesson_new.html",
            context,
        ).body.decode()

        self.assertIn("<label>内容<input", japanese_html)
        self.assertNotIn("<label>科目<input", japanese_html)
        self.assertIn("<label>Content<input", english_html)

    def test_external_return_url_is_rejected(self):
        self.assertEqual(safe_return_path("https://example.com"), "/")
        self.assertEqual(safe_return_path("//example.com"), "/")
        self.assertEqual(safe_return_path("/users"), "/users")

    def test_translation_helpers_default_to_japanese(self):
        self.assertEqual(translate("common.subject"), "内容")
        self.assertEqual(translate("nav.users"), "利用者")
        self.assertEqual(translate("nav.users", "en"), "Users")
        self.assertEqual(weekday_labels("en")[0], "Mon")


if __name__ == "__main__":
    unittest.main()
