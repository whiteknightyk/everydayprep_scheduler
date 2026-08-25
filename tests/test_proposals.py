import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi import HTTPException

import app
import db


def create_lesson():
    with db.get_db() as con:
        instructor_id = con.execute(
            "INSERT INTO users(name,role,timezone) VALUES(?,?,?)",
            ("London instructor", "instructor", "Europe/London"),
        ).lastrowid
        student_id = con.execute(
            "INSERT INTO users(name,role,timezone) VALUES(?,?,?)",
            ("Tokyo student", "student", "Asia/Tokyo"),
        ).lastrowid
        con.execute(
            """
            INSERT INTO availability(user_id,weekday,start_time,end_time)
            VALUES(?,?,?,?)
            """,
            (instructor_id, 3, "00:00", "23:59"),
        )
        return con.execute(
            """
            INSERT INTO lessons(instructor_id,student_id,subject,duration_minutes)
            VALUES(?,?,?,?)
            """,
            (instructor_id, student_id, "SAT Math", 60),
        ).lastrowid


def create_legacy_candidates_table(con, with_proposer: bool):
    proposer_column = (
        "proposed_by TEXT CHECK (proposed_by IN ('instructor','student'))," 
        if with_proposer
        else ""
    )
    con.execute(
        f"""
        CREATE TABLE candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lesson_id INTEGER NOT NULL REFERENCES lessons(id) ON DELETE CASCADE,
            start_utc TEXT NOT NULL,
            end_utc TEXT NOT NULL,
            {proposer_column}
            instructor_response TEXT NOT NULL DEFAULT 'pending'
                CHECK (instructor_response IN ('pending','accept','reject')),
            student_response TEXT NOT NULL DEFAULT 'pending'
                CHECK (student_response IN ('pending','accept','reject')),
            expires_at_utc TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(lesson_id, start_utc)
        )
        """
    )


class ProposalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_db_path = db.DB_PATH
        db.DB_PATH = Path(self.temp_dir.name) / "scheduler.db"
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.original_db_path
        self.temp_dir.cleanup()

    def candidate_id_for(self, lesson_id: int) -> int:
        with db.get_db() as con:
            return con.execute(
                "SELECT id FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()["id"]

    def test_coordinator_can_propose_a_jst_datetime(self):
        lesson_id = create_lesson()

        response = app.propose_lesson_candidate(
            lesson_id,
            start_local="2026-10-15T22:00",
        )

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            candidate = con.execute(
                "SELECT * FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()
            lesson = con.execute(
                "SELECT status FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            log = con.execute(
                "SELECT * FROM audit_logs WHERE lesson_id=? ORDER BY id DESC LIMIT 1",
                (lesson_id,),
            ).fetchone()
        self.assertEqual(candidate["proposed_by"], "admin")
        self.assertEqual(candidate["start_utc"], "2026-10-15T13:00:00+00:00")
        self.assertEqual(candidate["end_utc"], "2026-10-15T14:00:00+00:00")
        self.assertEqual(candidate["instructor_response"], "pending")
        self.assertEqual(candidate["student_response"], "pending")
        self.assertEqual(lesson["status"], "responding")
        self.assertEqual(log["actor"], "admin")
        self.assertEqual(log["action"], "datetime_proposed")

    def test_instructor_can_propose_in_a_selected_timezone(self):
        lesson_id = create_lesson()

        response = app.propose_lesson_candidate(
            lesson_id,
            start_local="2026-10-15T14:00",
            proposed_by="instructor",
            input_timezone="Europe/London",
        )

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            candidate = con.execute(
                "SELECT * FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()
            log = con.execute(
                "SELECT * FROM audit_logs WHERE lesson_id=? ORDER BY id DESC LIMIT 1",
                (lesson_id,),
            ).fetchone()
        self.assertEqual(candidate["proposed_by"], "instructor")
        self.assertEqual(candidate["start_utc"], "2026-10-15T13:00:00+00:00")
        self.assertIn("22:00", app.fmt_local(candidate["start_utc"], "Asia/Tokyo"))
        self.assertEqual(candidate["instructor_response"], "accept")
        self.assertEqual(candidate["student_response"], "pending")
        self.assertEqual(log["actor"], "instructor")
        self.assertIn("timezone=Europe/London", log["detail"])

    def test_student_proposal_is_confirmed_when_instructor_accepts(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(
            lesson_id,
            start_local="2026-10-15T09:00",
            proposed_by="student",
            input_timezone="America/New_York",
        )
        candidate_id = self.candidate_id_for(lesson_id)

        response = app.respond_candidate(candidate_id, "instructor", "accept")

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            candidate = con.execute(
                "SELECT * FROM candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        self.assertEqual(candidate["start_utc"], "2026-10-15T13:00:00+00:00")
        self.assertEqual(candidate["student_response"], "accept")
        self.assertEqual(lesson["status"], "confirmed")
        self.assertEqual(lesson["confirmed_start_utc"], "2026-10-15T13:00:00+00:00")

    def test_participant_cannot_change_their_implicit_acceptance(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(
            lesson_id,
            start_local="2026-10-15T14:00",
            proposed_by="instructor",
            input_timezone="Europe/London",
        )
        candidate_id = self.candidate_id_for(lesson_id)

        with self.assertRaisesRegex(HTTPException, "提示者本人は承諾済み"):
            app.respond_candidate(candidate_id, "instructor", "reject")

    def test_candidate_outside_instructor_schedule_is_rejected(self):
        lesson_id = create_lesson()
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT instructor_id FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            con.execute("DELETE FROM availability WHERE user_id=?", (lesson["instructor_id"],))
            con.execute(
                """
                INSERT INTO availability(user_id,weekday,start_time,end_time)
                VALUES(?,?,?,?)
                """,
                (lesson["instructor_id"], 3, "15:00", "18:00"),
            )

        with self.assertRaisesRegex(HTTPException, "講師が設定した授業可能時間"):
            app.propose_lesson_candidate(
                lesson_id,
                start_local="2026-10-15T14:00",
                proposed_by="instructor",
                input_timezone="Europe/London",
            )

        with db.get_db() as con:
            count = con.execute(
                "SELECT COUNT(*) count FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()["count"]
        self.assertEqual(count, 0)

    def test_candidate_can_be_proposed_when_instructor_schedule_is_unconfigured(self):
        lesson_id = create_lesson()
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT instructor_id FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            con.execute("DELETE FROM availability WHERE user_id=?", (lesson["instructor_id"],))

        response = app.propose_lesson_candidate(
            lesson_id,
            start_local="2026-10-15T03:00",
            proposed_by="instructor",
            input_timezone="Europe/London",
        )

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            candidate = con.execute(
                "SELECT * FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()
        self.assertEqual(candidate["start_utc"], "2026-10-15T02:00:00+00:00")

    def test_exception_still_blocks_an_unconfigured_instructor_schedule(self):
        lesson_id = create_lesson()
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT instructor_id FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            con.execute("DELETE FROM availability WHERE user_id=?", (lesson["instructor_id"],))
            con.execute(
                """
                INSERT INTO exceptions(user_id,start_utc,end_utc,reason)
                VALUES(?,?,?,?)
                """,
                (
                    lesson["instructor_id"],
                    "2026-10-15T02:30:00+00:00",
                    "2026-10-15T03:30:00+00:00",
                    "unavailable",
                ),
            )

        with self.assertRaisesRegex(HTTPException, "講師の例外予定"):
            app.propose_lesson_candidate(
                lesson_id,
                start_local="2026-10-15T03:00",
                proposed_by="instructor",
                input_timezone="Europe/London",
            )

    def test_candidate_overlapping_instructor_exception_is_rejected(self):
        lesson_id = create_lesson()
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT instructor_id FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            con.execute(
                """
                INSERT INTO exceptions(user_id,start_utc,end_utc,reason)
                VALUES(?,?,?,?)
                """,
                (
                    lesson["instructor_id"],
                    "2026-10-15T13:30:00+00:00",
                    "2026-10-15T14:30:00+00:00",
                    "unavailable",
                ),
            )

        with self.assertRaisesRegex(HTTPException, "講師の例外予定"):
            app.propose_lesson_candidate(
                lesson_id,
                start_local="2026-10-15T14:00",
                proposed_by="instructor",
                input_timezone="Europe/London",
            )

    def test_confirmation_rechecks_the_instructor_schedule(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)
        app.respond_candidate(candidate_id, "instructor", "accept")
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT instructor_id FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            con.execute("DELETE FROM availability WHERE user_id=?", (lesson["instructor_id"],))
            con.execute(
                """
                INSERT INTO availability(user_id,weekday,start_time,end_time)
                VALUES(?,?,?,?)
                """,
                (lesson["instructor_id"], 3, "15:00", "18:00"),
            )

        with self.assertRaisesRegex(HTTPException, "講師が設定した授業可能時間"):
            app.respond_candidate(candidate_id, "student", "accept")

        with db.get_db() as con:
            lesson = con.execute(
                "SELECT status FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            candidate = con.execute(
                "SELECT student_response FROM candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        self.assertEqual(lesson["status"], "responding")
        self.assertEqual(candidate["student_response"], "pending")

    def test_both_parties_must_accept_before_lesson_is_automatically_confirmed(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)

        app.respond_candidate(candidate_id, "instructor", "accept")

        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
        self.assertEqual(lesson["status"], "responding")
        self.assertIsNone(lesson["confirmed_start_utc"])

        response = app.respond_candidate(candidate_id, "student", "accept")

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            latest_log = con.execute(
                "SELECT * FROM audit_logs WHERE lesson_id=? ORDER BY id DESC LIMIT 1",
                (lesson_id,),
            ).fetchone()
        self.assertEqual(lesson["status"], "confirmed")
        self.assertEqual(lesson["confirmed_start_utc"], "2026-10-15T13:00:00+00:00")
        self.assertEqual(lesson["confirmed_end_utc"], "2026-10-15T14:00:00+00:00")
        self.assertIsNone(lesson["meeting_url"])
        self.assertIsNone(lesson["zoom_meeting_id"])
        self.assertIsNone(lesson["zoom_password"])
        self.assertEqual(latest_log["actor"], "system")
        self.assertEqual(latest_log["action"], "lesson_confirmed")

    def test_instructor_can_enter_zoom_details_after_confirmation(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)
        app.respond_candidate(candidate_id, "instructor", "accept")
        app.respond_candidate(candidate_id, "student", "accept")

        response = app.update_lesson_zoom(
            lesson_id,
            "https://us02web.zoom.us/j/1234567890",
            "123 456 7890",
            "SAT-2026",
        )

        self.assertEqual(response.status_code, 303)
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            log = con.execute(
                "SELECT * FROM audit_logs WHERE lesson_id=? ORDER BY id DESC LIMIT 1",
                (lesson_id,),
            ).fetchone()
        self.assertEqual(
            lesson["meeting_url"], "https://us02web.zoom.us/j/1234567890"
        )
        self.assertEqual(lesson["zoom_meeting_id"], "123 456 7890")
        self.assertEqual(lesson["zoom_password"], "SAT-2026")
        self.assertEqual(log["action"], "zoom_information_updated")
        self.assertNotIn("SAT-2026", log["detail"])

    def test_zoom_details_require_a_confirmed_lesson_and_https_url(self):
        lesson_id = create_lesson()
        with self.assertRaisesRegex(HTTPException, "確定後"):
            app.update_lesson_zoom(
                lesson_id,
                "https://zoom.us/j/1234567890",
                "1234567890",
                "secret",
            )

        with self.assertRaisesRegex(HTTPException, "https://"):
            app.ensure_zoom_url("javascript:alert(1)")

    def test_zoom_details_are_optional_and_can_be_cleared(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)
        app.respond_candidate(candidate_id, "instructor", "accept")
        app.respond_candidate(candidate_id, "student", "accept")

        partial = app.update_lesson_zoom(
            lesson_id,
            zoom_meeting_id="987 654 3210",
        )
        self.assertEqual(partial.status_code, 303)
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
        self.assertIsNone(lesson["meeting_url"])
        self.assertEqual(lesson["zoom_meeting_id"], "987 654 3210")
        self.assertIsNone(lesson["zoom_password"])

        cleared = app.update_lesson_zoom(lesson_id)
        self.assertEqual(cleared.status_code, 303)
        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
        self.assertIsNone(lesson["meeting_url"])
        self.assertIsNone(lesson["zoom_meeting_id"])
        self.assertIsNone(lesson["zoom_password"])

    def test_rejection_does_not_confirm_the_lesson(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)

        app.respond_candidate(candidate_id, "instructor", "accept")
        app.respond_candidate(candidate_id, "student", "reject")

        with db.get_db() as con:
            lesson = con.execute(
                "SELECT * FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
        self.assertEqual(lesson["status"], "responding")
        self.assertIsNone(lesson["confirmed_start_utc"])

    def test_coordinator_can_cancel_the_last_candidate(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)

        response = app.cancel_candidate(candidate_id)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], f"/lessons/{lesson_id}")
        with db.get_db() as con:
            candidate_count = con.execute(
                "SELECT COUNT(*) count FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()["count"]
            lesson = con.execute(
                "SELECT status FROM lessons WHERE id=?", (lesson_id,)
            ).fetchone()
            log = con.execute(
                "SELECT * FROM audit_logs WHERE lesson_id=? ORDER BY id DESC LIMIT 1",
                (lesson_id,),
            ).fetchone()
        self.assertEqual(candidate_count, 0)
        self.assertEqual(lesson["status"], "draft")
        self.assertEqual(log["actor"], "admin")
        self.assertEqual(log["action"], "datetime_cancelled")

    def test_confirmed_lesson_candidate_cannot_be_cancelled(self):
        lesson_id = create_lesson()
        app.propose_lesson_candidate(lesson_id, "2026-10-15T22:00")
        candidate_id = self.candidate_id_for(lesson_id)
        app.respond_candidate(candidate_id, "instructor", "accept")
        app.respond_candidate(candidate_id, "student", "accept")

        with self.assertRaisesRegex(HTTPException, "確定済み授業"):
            app.cancel_candidate(candidate_id)

        with db.get_db() as con:
            candidate_count = con.execute(
                "SELECT COUNT(*) count FROM candidates WHERE lesson_id=?", (lesson_id,)
            ).fetchone()["count"]
        self.assertEqual(candidate_count, 1)

    def test_lesson_template_shows_the_coordinator_workflow(self):
        template = app.templates.env.get_template("lesson_detail.html")
        html = template.render(
            lesson={
                "id": 7,
                "subject": "SAT Math",
                "instructor_name": "Teacher",
                "student_name": "Student",
                "duration_minutes": 60,
                "status": "responding",
                "instructor_id": 4,
                "instructor_tz": "Europe/London",
                "student_tz": "Asia/Tokyo",
            },
            candidates=[
                {
                    "id": 12,
                    "start_utc": "2026-10-15T13:00:00+00:00",
                    "proposed_by": "admin",
                    "instructor_response": "pending",
                    "student_response": "pending",
                }
            ],
            logs=[],
            fmt_local=lambda value, timezone_name: value,
            response_labels={"pending": "回答待ち", "accept": "承諾", "reject": "不可"},
            proposal_timezones=[
                {"name": "Asia/Tokyo", "label": "日本時間・生徒 / Asia/Tokyo"},
                {"name": "Europe/London", "label": "講師 / Europe/London"},
            ],
            instructor_availability=[
                {"weekday": 3, "start_time": "09:00", "end_time": "17:00"}
            ],
            weekdays=["月", "火", "水", "木", "金", "土", "日"],
        )

        self.assertIn("管理者が提示", html)
        self.assertIn("講師・生徒が提示", html)
        self.assertIn("提示する日時（日本時間）", html)
        self.assertIn('action="/lessons/7/proposals"', html)
        self.assertIn('type="datetime-local"', html)
        self.assertIn('name="proposed_by" value="admin"', html)
        self.assertIn('name="proposed_by" required', html)
        self.assertIn('name="input_timezone" required', html)
        self.assertIn("の授業可能時間", html)
        self.assertIn("木 09:00–17:00", html)
        self.assertIn("/availability?user_id=4", html)
        self.assertIn('name="role" value="instructor"', html)
        self.assertIn('name="role" value="student"', html)
        self.assertNotIn("/candidates/12/confirm", html)
        self.assertIn('action="/candidates/12/cancel"', html)
        self.assertNotIn("変更履歴", html)

    def test_lesson_template_allows_proposals_when_schedule_is_unconfigured(self):
        template = app.templates.env.get_template("lesson_detail.html")
        html = template.render(
            lesson={
                "id": 7,
                "subject": "SAT Math",
                "instructor_name": "Teacher",
                "student_name": "Student",
                "duration_minutes": 60,
                "status": "draft",
                "instructor_id": 4,
                "instructor_tz": "Europe/London",
                "student_tz": "Asia/Tokyo",
            },
            candidates=[],
            logs=[],
            fmt_local=lambda value, timezone_name: value,
            response_labels={"pending": "回答待ち", "accept": "承諾", "reject": "不可"},
            proposal_timezones=[],
            instructor_availability=[],
            weekdays=["月", "火", "水", "木", "金", "土", "日"],
        )

        self.assertIn("時間帯の制限はありません", html)
        self.assertIn("自由に候補日時を追加できます", html)
        self.assertNotIn("disabled", html)

    def test_confirmed_lesson_has_a_copyable_share_message(self):
        lesson = {
            "id": 7,
            "subject": "SAT Math",
            "instructor_name": "Teacher",
            "student_name": "Student",
            "duration_minutes": 60,
            "status": "confirmed",
            "instructor_id": 4,
            "instructor_tz": "Europe/London",
            "student_tz": "Asia/Tokyo",
            "confirmed_start_utc": "2026-10-15T13:00:00+00:00",
            "confirmed_end_utc": "2026-10-15T14:00:00+00:00",
            "meeting_url": "https://us02web.zoom.us/j/1234567890",
            "zoom_meeting_id": "123 456 7890",
            "zoom_password": "SAT-2026",
        }

        message = app.build_lesson_share_message(lesson)
        html = app.templates.env.get_template("lesson_detail.html").render(
            lesson=lesson,
            share_message=message,
            zoom_ready=True,
            fmt_local=app.fmt_local,
        )

        self.assertIn("【SAT講義日程確定】", message)
        self.assertIn("内容: SAT Math", message)
        self.assertNotIn("講義内容: SAT Math", message)
        self.assertNotIn("科目: SAT Math", message)
        self.assertIn(
            "日本時間: 2026/10/15 (Thu) 22:00 -　23:00 JST",
            message,
        )
        self.assertNotIn("-　2026/10/15 (Thu) 23:00", message)
        self.assertIn("講師現地時間:", message)
        self.assertIn("生徒現地時間:", message)
        self.assertIn("Zoom参加リンク: https://us02web.zoom.us/j/1234567890", message)
        self.assertIn("ミーティングID: 123 456 7890", message)
        self.assertIn("パスワード: SAT-2026", message)
        self.assertIn('id="lesson-share-message"', html)
        self.assertIn("メッセージをコピー", html)
        self.assertIn('/static/share-message.js', html)
        self.assertIn('action="/lessons/7/zoom"', html)
        self.assertNotIn(" required", html)

    def test_partial_zoom_details_only_show_registered_fields(self):
        lesson = {
            "id": 7,
            "subject": "SAT Math",
            "instructor_name": "Teacher",
            "student_name": "Student",
            "duration_minutes": 60,
            "status": "confirmed",
            "instructor_id": 4,
            "instructor_tz": "Europe/London",
            "student_tz": "Asia/Tokyo",
            "confirmed_start_utc": "2026-10-15T13:00:00+00:00",
            "confirmed_end_utc": "2026-10-15T14:00:00+00:00",
            "meeting_url": None,
            "zoom_meeting_id": "987 654 3210",
            "zoom_password": None,
        }

        message = app.build_lesson_share_message(lesson)
        html = app.templates.env.get_template("lesson_detail.html").render(
            lesson=lesson,
            share_message=message,
            zoom_ready=True,
            fmt_local=app.fmt_local,
        )

        self.assertIn("ミーティングID: 987 654 3210", message)
        self.assertNotIn("Zoom参加リンク:", message)
        self.assertNotIn("パスワード:", message)
        self.assertIn("<code>987 654 3210</code>", html)
        self.assertNotIn('href="None"', html)

    def test_share_message_is_localized_in_english(self):
        lesson = {
            "subject": "SAT Reading",
            "instructor_name": "Teacher",
            "student_name": "Student",
            "duration_minutes": 90,
            "status": "confirmed",
            "instructor_tz": "Europe/London",
            "student_tz": "America/New_York",
            "confirmed_start_utc": "2026-10-15T13:00:00+00:00",
            "confirmed_end_utc": None,
            "meeting_url": None,
            "zoom_meeting_id": None,
            "zoom_password": None,
        }

        message = app.build_lesson_share_message(lesson, "en")

        self.assertIn("[SAT Lesson Confirmed]", message)
        self.assertIn("Duration: 90 min", message)
        self.assertIn(
            "Japan time: 2026/10/15 (Thu) 22:00 - 23:30 JST",
            message,
        )
        self.assertIn("Zoom information: Not provided", message)

    def test_share_message_keeps_the_end_date_when_lesson_crosses_midnight(self):
        lesson = {
            "subject": "SAT Reading",
            "instructor_name": "Teacher",
            "student_name": "Student",
            "duration_minutes": 60,
            "status": "confirmed",
            "instructor_tz": "Asia/Tokyo",
            "student_tz": "Asia/Tokyo",
            "confirmed_start_utc": "2026-08-28T14:30:00+00:00",
            "confirmed_end_utc": "2026-08-28T15:30:00+00:00",
            "meeting_url": None,
            "zoom_meeting_id": None,
            "zoom_password": None,
        }

        message = app.build_lesson_share_message(lesson)

        self.assertIn(
            "日本時間: 2026/08/28 (Fri) 23:30 -　2026/08/29 (Sat) 00:30 JST",
            message,
        )

    def test_init_db_adds_admin_capable_proposer_column_to_older_database(self):
        database_path = Path(self.temp_dir.name) / "legacy_without_proposer.db"
        with closing(sqlite3.connect(database_path)) as con:
            create_legacy_candidates_table(con, with_proposer=False)
            con.commit()
        db.DB_PATH = database_path

        db.init_db()
        lesson_id = create_lesson()
        with db.get_db() as con:
            con.execute(
                """
                INSERT INTO candidates(lesson_id,start_utc,end_utc,proposed_by)
                VALUES(?,?,?,?)
                """,
                (
                    lesson_id,
                    "2026-10-15T13:00:00+00:00",
                    "2026-10-15T14:00:00+00:00",
                    "admin",
                ),
            )

    def test_init_db_rebuilds_old_proposer_constraint_and_preserves_data(self):
        database_path = Path(self.temp_dir.name) / "legacy_constraint.db"
        with closing(sqlite3.connect(database_path)) as con:
            con.execute("CREATE TABLE lessons (id INTEGER PRIMARY KEY)")
            con.execute("INSERT INTO lessons(id) VALUES(1)")
            create_legacy_candidates_table(con, with_proposer=True)
            legacy_candidate_id = con.execute(
                """
                INSERT INTO candidates(
                    lesson_id,start_utc,end_utc,proposed_by,instructor_response
                ) VALUES(?,?,?,?,?)
                """,
                (
                    1,
                    "2026-10-15T13:00:00+00:00",
                    "2026-10-15T14:00:00+00:00",
                    "instructor",
                    "accept",
                ),
            ).lastrowid
            con.commit()
        db.DB_PATH = database_path

        db.init_db()

        with db.get_db() as con:
            legacy_candidate = con.execute(
                "SELECT * FROM candidates WHERE id=?", (legacy_candidate_id,)
            ).fetchone()
            con.execute(
                """
                INSERT INTO candidates(lesson_id,start_utc,end_utc,proposed_by)
                VALUES(?,?,?,?)
                """,
                (
                    1,
                    "2026-10-16T13:00:00+00:00",
                    "2026-10-16T14:00:00+00:00",
                    "admin",
                ),
            )
        self.assertEqual(legacy_candidate["proposed_by"], "instructor")
        self.assertEqual(legacy_candidate["instructor_response"], "accept")


if __name__ == "__main__":
    unittest.main()
