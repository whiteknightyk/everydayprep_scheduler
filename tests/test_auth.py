import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient

import app
import db
from auth import hash_password, verify_password


class PasswordTests(unittest.TestCase):
    def test_passwords_are_salted_and_verifiable(self):
        first = hash_password("correct-horse")
        second = hash_password("correct-horse")

        self.assertNotEqual(first, second)
        self.assertNotIn("correct-horse", first)
        self.assertTrue(verify_password("correct-horse", first))
        self.assertFalse(verify_password("wrong-password", first))
        self.assertFalse(verify_password("correct-horse", "invalid"))


class AuthenticationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "scheduler.db"
        db.init_db()
        self.client = TestClient(app.app, follow_redirects=False)

    def tearDown(self):
        self.client.close()
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def create_owner(self):
        return self.client.post(
            "/setup",
            data={
                "name": "Owner",
                "login_id": "owner",
                "password": "owner-password",
                "password_confirm": "owner-password",
            },
        )

    def create_user(self, login_id: str, role: str, password: str = "password123"):
        return self.client.post(
            "/users",
            data={
                "name": login_id.title(),
                "login_id": login_id,
                "password": password,
                "password_confirm": password,
                "role": role,
                "timezone_name": "Asia/Tokyo",
                "email": f"{login_id}@example.com",
            },
        )

    def test_setup_login_logout_and_password_storage(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")

        response = self.create_owner()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/")
        with db.get_db() as con:
            owner = con.execute(
                "SELECT * FROM users WHERE login_id='owner'"
            ).fetchone()
        self.assertEqual(owner["role"], "owner")
        self.assertNotEqual(owner["password_hash"], "owner-password")
        self.assertTrue(verify_password("owner-password", owner["password_hash"]))

        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.post("/logout").status_code, 303)
        response = self.client.get("/users")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/login?next="))

        wrong = self.client.post(
            "/login",
            data={"login_id": "owner", "password": "wrong", "next": "/users"},
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertIn("正しくありません", wrong.text)
        correct = self.client.post(
            "/login",
            data={
                "login_id": "OWNER",
                "password": "owner-password",
                "next": "/users",
            },
        )
        self.assertEqual(correct.status_code, 303)
        self.assertEqual(correct.headers["location"], "/users")

    def test_language_can_be_switched_before_initial_setup(self):
        response = self.client.get("/language/en?next=/setup")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/setup")
        setup = self.client.get("/setup")
        self.assertIn("Initial Setup", setup.text)

    def test_instructors_and_students_can_register_from_the_login_page(self):
        self.create_owner()
        self.client.post("/logout")

        login_page = self.client.get("/login")
        self.assertIn('href="/register"', login_page.text)

        registration_page = self.client.get("/register")
        self.assertEqual(registration_page.status_code, 200)
        self.assertIn('action="/register"', registration_page.text)
        self.assertIn('<option value="student" selected>', registration_page.text)
        self.assertIn('<option value="instructor"', registration_page.text)
        self.assertNotIn('<option value="owner"', registration_page.text)
        self.assertNotIn('<option value="admin"', registration_page.text)
        self.assertIn('/static/timezone-search.js', registration_page.text)

        forbidden_role = self.client.post(
            "/register",
            data={
                "name": "Unauthorized Admin",
                "role": "admin",
                "login_id": "unauthorized-admin",
                "password": "password123",
                "password_confirm": "password123",
                "timezone_name": "Asia/Tokyo",
                "email": "admin@example.com",
            },
        )
        self.assertEqual(forbidden_role.status_code, 400)
        self.assertIn("講師または生徒", forbidden_role.text)

        registered = self.client.post(
            "/register",
            data={
                "name": "New Student",
                "role": "student",
                "login_id": "new-student",
                "password": "password123",
                "password_confirm": "password123",
                "timezone_name": "America/New_York",
                "email": "student@example.com",
            },
        )
        self.assertEqual(registered.status_code, 303)
        self.assertEqual(registered.headers["location"], "/")
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/register").headers["location"], "/")

        with db.get_db() as con:
            student = con.execute(
                "SELECT * FROM users WHERE login_id='new-student'"
            ).fetchone()
            unauthorized_admin = con.execute(
                "SELECT 1 FROM users WHERE login_id='unauthorized-admin'"
            ).fetchone()
        self.assertEqual(student["role"], "student")
        self.assertEqual(student["timezone"], "America/New_York")
        self.assertTrue(verify_password("password123", student["password_hash"]))
        self.assertIsNone(unauthorized_admin)

    def test_all_four_roles_and_management_permissions(self):
        self.create_owner()
        for login_id, role in (
            ("manager", "admin"),
            ("teacher", "instructor"),
            ("learner", "student"),
        ):
            response = self.create_user(login_id, role)
            self.assertEqual(response.status_code, 303)

        with db.get_db() as con:
            roles = {
                row["role"]
                for row in con.execute("SELECT role FROM users").fetchall()
            }
            teacher_id = con.execute(
                "SELECT id FROM users WHERE login_id='teacher'"
            ).fetchone()["id"]
            learner_id = con.execute(
                "SELECT id FROM users WHERE login_id='learner'"
            ).fetchone()["id"]
            own_lesson_id = con.execute(
                """
                INSERT INTO lessons(instructor_id, student_id, subject)
                VALUES(?, ?, 'Own Lesson')
                """,
                (teacher_id, learner_id),
            ).lastrowid
            other_teacher_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Other Teacher', 'instructor')"
            ).lastrowid
            other_student_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Other Student', 'student')"
            ).lastrowid
            other_lesson_id = con.execute(
                """
                INSERT INTO lessons(instructor_id, student_id, subject)
                VALUES(?, ?, 'Other Lesson')
                """,
                (other_teacher_id, other_student_id),
            ).lastrowid
        self.assertEqual(roles, {"owner", "admin", "instructor", "student"})

        self.client.post("/logout")
        self.client.post(
            "/login",
            data={"login_id": "teacher", "password": "password123"},
        )
        self.assertEqual(self.client.get("/users").status_code, 403)
        self.assertEqual(self.client.get("/lessons/new").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/permission-mode",
                data={"mode": "management", "next": "/"},
            ).status_code,
            403,
        )
        self.assertEqual(self.client.get(f"/lessons/{own_lesson_id}").status_code, 200)
        self.assertEqual(
            self.client.get(f"/lessons/{other_lesson_id}").status_code, 403
        )
        self.assertEqual(
            self.client.post(
                "/availability",
                data={
                    "user_id": learner_id,
                    "weekday": 0,
                    "start_time": "09:00",
                    "end_time": "12:00",
                },
            ).status_code,
            403,
        )
        proposal = self.client.post(
            f"/lessons/{own_lesson_id}/proposals",
            data={
                "start_local": "2026-10-15T10:00",
                "proposed_by": "admin",
                "input_timezone": "Asia/Tokyo",
            },
        )
        self.assertEqual(proposal.status_code, 303)
        with db.get_db() as con:
            proposed_by = con.execute(
                "SELECT proposed_by FROM candidates WHERE lesson_id=?",
                (own_lesson_id,),
            ).fetchone()["proposed_by"]
            con.execute(
                """
                UPDATE lessons
                SET status='confirmed',
                    confirmed_start_utc='2026-10-15T01:00:00+00:00',
                    confirmed_end_utc='2026-10-15T02:00:00+00:00'
                WHERE id=?
                """,
                (own_lesson_id,),
            )
        self.assertEqual(proposed_by, "instructor")

        zoom_response = self.client.post(
            f"/lessons/{own_lesson_id}/zoom",
            data={
                "meeting_url": "https://us02web.zoom.us/j/1234567890",
                "zoom_meeting_id": "123 456 7890",
                "zoom_password": "SAT-2026",
            },
        )
        self.assertEqual(zoom_response.status_code, 303)

        self.client.post("/logout")
        self.client.post(
            "/login",
            data={"login_id": "learner", "password": "password123"},
        )
        denied_zoom_update = self.client.post(
            f"/lessons/{own_lesson_id}/zoom",
            data={
                "meeting_url": "https://zoom.us/j/999999999",
                "zoom_meeting_id": "999 999 999",
                "zoom_password": "changed",
            },
        )
        self.assertEqual(denied_zoom_update.status_code, 403)
        student_detail = self.client.get(f"/lessons/{own_lesson_id}")
        self.assertEqual(student_detail.status_code, 200)
        self.assertIn("https://us02web.zoom.us/j/1234567890", student_detail.text)
        self.assertIn("123 456 7890", student_detail.text)
        self.assertIn("SAT-2026", student_detail.text)
        self.assertNotIn(
            f'action="/lessons/{own_lesson_id}/zoom"', student_detail.text
        )
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (own_lesson_id,)
            ).fetchone()
        self.assertEqual(lesson["zoom_password"], "SAT-2026")

    def test_owner_and_admin_can_switch_to_assigned_instructor_access(self):
        self.create_owner()
        self.create_user("manager", "admin")
        self.create_user("learner", "student")

        with db.get_db() as con:
            owner_id = con.execute(
                "SELECT id FROM users WHERE login_id='owner'"
            ).fetchone()["id"]
            manager_id = con.execute(
                "SELECT id FROM users WHERE login_id='manager'"
            ).fetchone()["id"]
            learner_id = con.execute(
                "SELECT id FROM users WHERE login_id='learner'"
            ).fetchone()["id"]

        lesson_form = self.client.get("/lessons/new")
        availability_page = self.client.get("/availability")
        for user_id in (owner_id, manager_id):
            self.assertIn(f'<option value="{user_id}">', lesson_form.text)
            self.assertIn(f'<option value="{user_id}"', availability_page.text)

        actors = (
            (
                "owner",
                "owner-password",
                owner_id,
                "2026-10-15T10:00",
                "2026-10-15T11:00",
            ),
            (
                "manager",
                "password123",
                manager_id,
                "2026-10-15T13:00",
                "2026-10-15T14:00",
            ),
        )
        created_lesson_ids = []
        for index, (
            login_id,
            password,
            instructor_id,
            participant_start,
            coordinator_start,
        ) in enumerate(actors):
            if index:
                self.client.post("/logout")
                login = self.client.post(
                    "/login",
                    data={"login_id": login_id, "password": password},
                )
                self.assertEqual(login.status_code, 303)

            created = self.client.post(
                "/lessons",
                data={
                    "instructor_id": instructor_id,
                    "student_id": learner_id,
                    "subject": f"Managed Lesson {index}",
                    "duration_minutes": 60,
                },
            )
            self.assertEqual(created.status_code, 303)
            lesson_id = int(created.headers["location"].rsplit("/", 1)[-1])
            created_lesson_ids.append(lesson_id)

            detail = self.client.get(f"/lessons/{lesson_id}")
            self.assertIn("管理者権限", detail.text)
            self.assertIn('name="mode" value="instructor"', detail.text)
            self.assertNotIn('name="proposed_by" value="instructor"', detail.text)

            coordinator_proposal = self.client.post(
                f"/lessons/{lesson_id}/proposals",
                data={
                    "start_local": coordinator_start,
                    "proposed_by": "admin",
                    "input_timezone": "Asia/Tokyo",
                },
            )
            self.assertEqual(coordinator_proposal.status_code, 303)
            with db.get_db() as con:
                coordinator_candidate = con.execute(
                    """
                    SELECT * FROM candidates
                    WHERE lesson_id=? AND proposed_by='admin'
                    """,
                    (lesson_id,),
                ).fetchone()

            switched = self.client.post(
                "/permission-mode",
                data={
                    "mode": "instructor",
                    "next": f"/lessons/{lesson_id}",
                },
            )
            self.assertEqual(switched.status_code, 303)
            self.assertEqual(
                switched.headers["location"], f"/lessons/{lesson_id}"
            )
            instructor_detail = self.client.get(f"/lessons/{lesson_id}")
            self.assertIn("講師権限", instructor_detail.text)
            self.assertIn(
                'name="proposed_by" value="instructor"', instructor_detail.text
            )
            self.assertNotIn('href="/users"', instructor_detail.text)
            self.assertEqual(self.client.get("/users").status_code, 403)
            own_availability = self.client.get("/availability")
            self.assertEqual(own_availability.status_code, 200)
            self.assertIn(
                f'<option value="{instructor_id}" selected>',
                own_availability.text,
            )
            other_management_name = "Manager" if not index else "Owner"
            self.assertNotIn(
                f">{other_management_name} /",
                own_availability.text,
            )
            if index:
                self.assertEqual(
                    self.client.get(
                        f"/lessons/{created_lesson_ids[0]}"
                    ).status_code,
                    403,
                )

            proposed = self.client.post(
                f"/lessons/{lesson_id}/proposals",
                data={
                    "start_local": participant_start,
                    "proposed_by": "instructor",
                    "input_timezone": "Asia/Tokyo",
                },
            )
            self.assertEqual(proposed.status_code, 303)
            with db.get_db() as con:
                candidate = con.execute(
                    "SELECT * FROM candidates WHERE lesson_id=?",
                    (lesson_id,),
                ).fetchone()
            self.assertEqual(candidate["proposed_by"], "instructor")
            self.assertEqual(candidate["instructor_response"], "accept")

            instructor_response = self.client.post(
                f"/candidates/{coordinator_candidate['id']}/respond",
                data={"role": "instructor", "response": "accept"},
            )
            self.assertEqual(instructor_response.status_code, 303)
            with db.get_db() as con:
                saved_response = con.execute(
                    "SELECT instructor_response FROM candidates WHERE id=?",
                    (coordinator_candidate["id"],),
                ).fetchone()["instructor_response"]
            self.assertEqual(saved_response, "accept")

            app.respond_candidate(candidate["id"], "student", "accept")
            zoom = self.client.post(
                f"/lessons/{lesson_id}/zoom",
                data={
                    "meeting_url": f"https://zoom.us/j/{1000 + index}",
                    "zoom_meeting_id": str(1000 + index),
                    "zoom_password": "managed-teacher",
                },
            )
            self.assertEqual(zoom.status_code, 303)

            switched_back = self.client.post(
                "/permission-mode",
                data={"mode": "management", "next": "/users"},
            )
            self.assertEqual(switched_back.status_code, 303)
            self.assertEqual(switched_back.headers["location"], "/users")
            management_page = self.client.get("/users")
            self.assertEqual(management_page.status_code, 200)
            self.assertIn("管理者権限", management_page.text)

    def test_owner_and_admin_can_promote_instructors_to_admin(self):
        self.create_owner()
        self.create_user("manager", "admin")
        self.create_user("owner-teacher", "instructor")
        self.create_user("manager-teacher", "instructor")

        with db.get_db() as con:
            owner_teacher_id = con.execute(
                "SELECT id FROM users WHERE login_id='owner-teacher'"
            ).fetchone()["id"]
            manager_teacher_id = con.execute(
                "SELECT id FROM users WHERE login_id='manager-teacher'"
            ).fetchone()["id"]

        users_page = self.client.get("/users")
        self.assertIn(
            f'action="/users/{owner_teacher_id}/promote-to-admin"',
            users_page.text,
        )
        owner_promotion = self.client.post(
            f"/users/{owner_teacher_id}/promote-to-admin"
        )
        self.assertEqual(owner_promotion.status_code, 303)

        self.client.post("/logout")
        manager_login = self.client.post(
            "/login",
            data={"login_id": "manager", "password": "password123"},
        )
        self.assertEqual(manager_login.status_code, 303)
        manager_promotion = self.client.post(
            f"/users/{manager_teacher_id}/promote-to-admin"
        )
        self.assertEqual(manager_promotion.status_code, 303)

        with db.get_db() as con:
            promoted_roles = {
                row["login_id"]: row["role"]
                for row in con.execute(
                    """
                    SELECT login_id, role FROM users
                    WHERE id IN (?, ?)
                    """,
                    (owner_teacher_id, manager_teacher_id),
                ).fetchall()
            }
        self.assertEqual(promoted_roles["owner-teacher"], "admin")
        self.assertEqual(promoted_roles["manager-teacher"], "admin")

    def test_legacy_user_can_receive_login_credentials_without_data_loss(self):
        self.create_owner()
        with db.get_db() as con:
            legacy_id = con.execute(
                "INSERT INTO users(name, role) VALUES('Legacy Student', 'student')"
            ).lastrowid

        response = self.client.post(
            f"/users/{legacy_id}/credentials",
            data={
                "login_id": "legacy-student",
                "password": "new-password",
                "password_confirm": "new-password",
            },
        )

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            legacy = con.execute(
                "SELECT * FROM users WHERE id=?", (legacy_id,)
            ).fetchone()
        self.assertEqual(legacy["login_id"], "legacy-student")
        self.assertTrue(verify_password("new-password", legacy["password_hash"]))


class UserMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "legacy.db"

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def test_old_users_are_preserved_and_four_role_schema_is_installed(self):
        with closing(sqlite3.connect(db.DB_PATH)) as con:
            con.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL
                        CHECK (role IN ('instructor','student','admin')),
                    timezone TEXT NOT NULL DEFAULT 'Asia/Tokyo',
                    email TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE availability (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    weekday INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    UNIQUE(user_id, weekday, start_time, end_time)
                );
                CREATE TABLE lessons (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    instructor_id INTEGER NOT NULL REFERENCES users(id),
                    student_id INTEGER NOT NULL REFERENCES users(id),
                    subject TEXT NOT NULL DEFAULT 'SAT',
                    duration_minutes INTEGER NOT NULL DEFAULT 60,
                    status TEXT NOT NULL DEFAULT 'draft',
                    confirmed_start_utc TEXT,
                    confirmed_end_utc TEXT,
                    meeting_url TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users(name, role) VALUES('Legacy Teacher', 'instructor');
                INSERT INTO availability(user_id, weekday, start_time, end_time)
                VALUES(1, 0, '09:00', '12:00');
                """
            )
            con.commit()

        db.init_db()

        with db.get_db() as con:
            legacy = con.execute("SELECT * FROM users WHERE id=1").fetchone()
            self.assertEqual(legacy["name"], "Legacy Teacher")
            self.assertIsNone(legacy["login_id"])
            self.assertEqual(
                con.execute("SELECT COUNT(*) count FROM availability").fetchone()[
                    "count"
                ],
                1,
            )
            lesson_columns = {
                row["name"] for row in con.execute("PRAGMA table_info(lessons)")
            }
            self.assertIn("zoom_meeting_id", lesson_columns)
            self.assertIn("zoom_password", lesson_columns)
            con.execute(
                """
                INSERT INTO users(name, role, login_id, password_hash)
                VALUES('New Owner', 'owner', 'new-owner', 'placeholder')
                """
            )
            self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
