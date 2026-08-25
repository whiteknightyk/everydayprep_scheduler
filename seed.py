from __future__ import annotations

from auth import hash_password
from db import get_db, init_db
from settings import SETTINGS


def main():
    if SETTINGS.is_production:
        raise RuntimeError(
            "Refusing to create predictable demo accounts in production."
        )
    init_db()
    with get_db() as con:
        if con.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
            print("seed skipped: users already exist")
            return
        instructor = con.execute(
            """
            INSERT INTO users(name,role,login_id,password_hash,timezone,email)
            VALUES(?,?,?,?,?,?)
            """,
            (
                "田中先生",
                "instructor",
                "instructor",
                hash_password("teacher123"),
                "Europe/London",
                "tanaka@example.com",
            ),
        ).lastrowid
        student = con.execute(
            """
            INSERT INTO users(name,role,login_id,password_hash,timezone,email)
            VALUES(?,?,?,?,?,?)
            """,
            (
                "山田さん",
                "student",
                "student",
                hash_password("student123"),
                "America/New_York",
                "yamada@example.com",
            ),
        ).lastrowid
        con.execute(
            """
            INSERT INTO users(name,role,login_id,password_hash,timezone,email)
            VALUES(?,?,?,?,?,?)
            """,
            (
                "管理者",
                "admin",
                "admin",
                hash_password("admin1234"),
                "Asia/Tokyo",
                "admin@example.com",
            ),
        )
        con.execute(
            """
            INSERT INTO users(name,role,login_id,password_hash,timezone,email)
            VALUES(?,?,?,?,?,?)
            """,
            (
                "塾長",
                "owner",
                "owner",
                hash_password("owner1234"),
                "Asia/Tokyo",
                "owner@example.com",
            ),
        )
        con.executemany(
            """
            INSERT INTO availability(user_id,weekday,start_time,end_time)
            VALUES(?,?,?,?)
            """,
            [(instructor, weekday, "09:00", "18:00") for weekday in range(5)],
        )
        lesson_id = con.execute(
            "INSERT INTO lessons(instructor_id,student_id,subject,duration_minutes,status) VALUES(?,?,?,?,?)",
            (instructor, student, "SAT Math", 60, "draft"),
        ).lastrowid
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (lesson_id, "seed", "lesson_created", "demo lesson"),
        )
        print(f"seeded demo lesson #{lesson_id}")
        print("demo logins: owner/owner1234, admin/admin1234, instructor/teacher123, student/student123")


if __name__ == "__main__":
    main()
