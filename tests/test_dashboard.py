import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import db
from app import dashboard, delete_lesson, group_lessons_by_student, templates
from starlette.requests import Request


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.lessons = [
            {
                "id": 1,
                "student_id": 20,
                "student_name": "Yuki",
                "student_tz": "Asia/Tokyo",
                "subject": "SAT Math",
                "instructor_name": "Teacher A",
                "status": "draft",
                "confirmed_start_utc": None,
            },
            {
                "id": 2,
                "student_id": 10,
                "student_name": "Akari",
                "student_tz": "America/New_York",
                "subject": "SAT Reading",
                "instructor_name": "Teacher B",
                "status": "responding",
                "confirmed_start_utc": None,
            },
            {
                "id": 3,
                "student_id": 20,
                "student_name": "Yuki",
                "student_tz": "Asia/Tokyo",
                "subject": "SAT Writing",
                "instructor_name": "Teacher A",
                "status": "confirmed",
                "confirmed_start_utc": "2026-08-24T01:00:00+00:00",
            },
        ]

    def test_lessons_are_grouped_and_groups_are_sorted_by_student(self):
        groups = group_lessons_by_student(self.lessons)

        self.assertEqual([group["student_name"] for group in groups], ["Akari", "Yuki"])
        self.assertEqual([lesson["id"] for lesson in groups[1]["lessons"]], [1, 3])

    def test_dashboard_renders_one_section_per_student(self):
        groups = group_lessons_by_student(self.lessons)
        html = templates.env.get_template("dashboard.html").render(
            stats={},
            students=[
                {"id": 10, "name": "Akari"},
                {"id": 20, "name": "Yuki"},
            ],
            selected_student_id=20,
            lesson_groups=groups,
            fmt_local=lambda value, timezone: value,
        )

        self.assertEqual(html.count('class="student-lessons"'), 2)
        self.assertIn("Akari", html)
        self.assertIn("Yuki", html)
        self.assertIn("2件", html)
        self.assertNotIn("<th>生徒</th>", html)
        self.assertEqual(html.count('class="danger small">削除</button>'), 3)
        self.assertIn('action="/lessons/3/delete"', html)
        self.assertIn("元に戻せません", html)
        self.assertIn('name="student_id"', html)
        self.assertNotIn("すべての生徒", html)
        self.assertIn('<option value="20" selected>Yuki</option>', html)


class DashboardStudentFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "scheduler.db"
        db.init_db()

        with db.get_db() as con:
            instructor_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Teacher', 'instructor')"
            ).lastrowid
            self.akari_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Akari', 'student')"
            ).lastrowid
            self.yuki_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Yuki', 'student')"
            ).lastrowid
            con.execute(
                """
                INSERT INTO lessons(instructor_id, student_id, subject)
                VALUES(?, ?, 'Akari Math')
                """,
                (instructor_id, self.akari_id),
            )
            con.execute(
                """
                INSERT INTO lessons(instructor_id, student_id, subject)
                VALUES(?, ?, 'Yuki Reading')
                """,
                (instructor_id, self.yuki_id),
            )

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_dashboard_only_lists_lessons_for_selected_student(self):
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/",
                "headers": [],
                "query_string": f"student_id={self.akari_id}".encode(),
            }
        )

        response = dashboard(request, str(self.akari_id))
        html = response.body.decode()

        self.assertIn("Akari Math", html)
        self.assertNotIn("Yuki Reading", html)
        self.assertIn(
            f'<option value="{self.akari_id}" selected>Akari</option>', html
        )


class DeleteLessonTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "scheduler.db"
        db.init_db()

        with db.get_db() as con:
            instructor_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Teacher', 'instructor')"
            ).lastrowid
            student_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Student', 'student')"
            ).lastrowid
            self.lesson_id = con.execute(
                """
                INSERT INTO lessons(instructor_id, student_id, subject)
                VALUES(?, ?, 'SAT Math')
                """,
                (instructor_id, student_id),
            ).lastrowid
            con.execute(
                """
                INSERT INTO candidates(lesson_id, start_utc, end_utc, proposed_by)
                VALUES(?, '2026-08-24T01:00:00+00:00',
                           '2026-08-24T02:00:00+00:00', 'admin')
                """,
                (self.lesson_id,),
            )
            con.execute(
                """
                INSERT INTO audit_logs(lesson_id, actor, action)
                VALUES(?, 'admin', 'lesson_created')
                """,
                (self.lesson_id,),
            )

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_delete_lesson_removes_lesson_and_related_records(self):
        response = delete_lesson(self.lesson_id)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        with db.get_db() as con:
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) count FROM lessons WHERE id=?",
                    (self.lesson_id,),
                ).fetchone()["count"],
                0,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) count FROM candidates WHERE lesson_id=?",
                    (self.lesson_id,),
                ).fetchone()["count"],
                0,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) count FROM audit_logs WHERE lesson_id=?",
                    (self.lesson_id,),
                ).fetchone()["count"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
