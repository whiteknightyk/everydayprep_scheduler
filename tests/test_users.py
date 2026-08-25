import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException

import db
from app import delete_user, promote_user_to_admin, templates


class UsersPageTests(unittest.TestCase):
    def test_registered_users_have_delete_buttons(self):
        html = templates.env.get_template("users.html").render(
            users=[
                {
                    "id": 7,
                    "name": "Teacher",
                    "role": "instructor",
                    "timezone": "Asia/Tokyo",
                }
            ]
        )

        self.assertIn('action="/users/7/delete"', html)
        self.assertIn('action="/users/7/promote-to-admin"', html)
        self.assertIn('class="secondary small">管理者に変更</button>', html)
        self.assertIn('class="danger small">削除</button>', html)
        self.assertIn("元に戻せません", html)


class DeleteUserTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "scheduler.db"
        db.init_db()

        with db.get_db() as con:
            self.instructor_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Teacher', 'instructor')"
            ).lastrowid
            self.student_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Student', 'student')"
            ).lastrowid
            con.execute(
                """
                INSERT INTO availability(user_id, weekday, start_time, end_time)
                VALUES(?, 0, '09:00', '12:00')
                """,
                (self.instructor_id,),
            )
            con.execute(
                """
                INSERT INTO exceptions(user_id, start_utc, end_utc, reason)
                VALUES(?, '2026-08-24T01:00:00+00:00',
                           '2026-08-24T02:00:00+00:00', 'Busy')
                """,
                (self.instructor_id,),
            )
            self.lesson_id = con.execute(
                """
                INSERT INTO lessons(instructor_id, student_id, subject)
                VALUES(?, ?, 'SAT Math')
                """,
                (self.instructor_id, self.student_id),
            ).lastrowid
            con.execute(
                """
                INSERT INTO candidates(lesson_id, start_utc, end_utc, proposed_by)
                VALUES(?, '2026-08-25T01:00:00+00:00',
                           '2026-08-25T02:00:00+00:00', 'admin')
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

    def test_delete_user_removes_user_and_all_related_records(self):
        response = delete_user(self.instructor_id)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/users")
        with db.get_db() as con:
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) count FROM users WHERE id=?",
                    (self.instructor_id,),
                ).fetchone()["count"],
                0,
            )
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) count FROM users WHERE id=?", (self.student_id,)
                ).fetchone()["count"],
                1,
            )
            for table, column, record_id in (
                ("availability", "user_id", self.instructor_id),
                ("exceptions", "user_id", self.instructor_id),
                ("lessons", "id", self.lesson_id),
                ("candidates", "lesson_id", self.lesson_id),
                ("audit_logs", "lesson_id", self.lesson_id),
            ):
                self.assertEqual(
                    con.execute(
                        f"SELECT COUNT(*) count FROM {table} WHERE {column}=?",
                        (record_id,),
                    ).fetchone()["count"],
                    0,
                )

    def test_delete_missing_user_returns_not_found(self):
        with self.assertRaises(HTTPException) as raised:
            delete_user(9999)

        self.assertEqual(raised.exception.status_code, 404)

    def test_promote_instructor_to_admin_preserves_assigned_data(self):
        response = promote_user_to_admin(self.instructor_id)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/users")
        with db.get_db() as con:
            promoted = con.execute(
                "SELECT role FROM users WHERE id=?", (self.instructor_id,)
            ).fetchone()
            lesson_count = con.execute(
                "SELECT COUNT(*) count FROM lessons WHERE instructor_id=?",
                (self.instructor_id,),
            ).fetchone()["count"]
            availability_count = con.execute(
                "SELECT COUNT(*) count FROM availability WHERE user_id=?",
                (self.instructor_id,),
            ).fetchone()["count"]
        self.assertEqual(promoted["role"], "admin")
        self.assertEqual(lesson_count, 1)
        self.assertEqual(availability_count, 1)

    def test_only_instructors_can_be_promoted_to_admin(self):
        with self.assertRaisesRegex(HTTPException, "講師だけ"):
            promote_user_to_admin(self.student_id)


if __name__ == "__main__":
    unittest.main()
