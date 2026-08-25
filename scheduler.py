from __future__ import annotations

from datetime import date, datetime, time, timedelta

from timezones import UTC, get_timezone

JST = get_timezone("Asia/Tokyo")


def parse_hhmm(value: str) -> time:
    return datetime.strptime(value, "%H:%M").time()


def to_utc_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def fmt_local(value: str | datetime, tz_name: str) -> str:
    dt = from_iso(value) if isinstance(value, str) else value
    return dt.astimezone(get_timezone(tz_name)).strftime("%Y/%m/%d (%a) %H:%M")


def overlaps(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    return start_a < end_b and start_b < end_a


def inside_recurring_availability(con, user_id: int, start_utc: datetime, end_utc: datetime, tz_name: str) -> bool:
    tz = get_timezone(tz_name)
    local_start = start_utc.astimezone(tz)
    local_end = end_utc.astimezone(tz)

    rows = con.execute(
        "SELECT weekday, start_time, end_time FROM availability WHERE user_id=?",
        (user_id,),
    ).fetchall()
    # An entirely unconfigured recurring schedule is unrestricted. Once at least
    # one range exists, only explicitly configured ranges are available.
    if not rows:
        return True

    if local_start.date() != local_end.date():
        return False

    for row in rows:
        if row["weekday"] != local_start.weekday():
            continue
        win_start = datetime.combine(local_start.date(), parse_hhmm(row["start_time"]), tzinfo=tz)
        win_end = datetime.combine(local_start.date(), parse_hhmm(row["end_time"]), tzinfo=tz)
        if win_start <= local_start and local_end <= win_end:
            return True
    return False


def blocked_by_exception(con, user_id: int, start_utc: datetime, end_utc: datetime) -> bool:
    rows = con.execute(
        "SELECT start_utc, end_utc FROM exceptions WHERE user_id=?",
        (user_id,),
    ).fetchall()
    for row in rows:
        if overlaps(start_utc, end_utc, from_iso(row["start_utc"]), from_iso(row["end_utc"])):
            return True
    return False


def blocked_by_confirmed_lesson(con, user_id: int, start_utc: datetime, end_utc: datetime, ignore_lesson_id: int | None = None) -> bool:
    params: list[object] = [user_id, user_id]
    sql = """
        SELECT id, confirmed_start_utc, confirmed_end_utc
        FROM lessons
        WHERE status='confirmed'
          AND (instructor_id=? OR student_id=?)
          AND confirmed_start_utc IS NOT NULL
    """
    if ignore_lesson_id is not None:
        sql += " AND id != ?"
        params.append(ignore_lesson_id)
    rows = con.execute(sql, params).fetchall()
    for row in rows:
        if overlaps(start_utc, end_utc, from_iso(row["confirmed_start_utc"]), from_iso(row["confirmed_end_utc"])):
            return True
    return False


def generate_candidates(
    con,
    lesson_id: int,
    start_date_jst: date,
    end_date_jst: date,
    max_candidates: int = 8,
    slot_minutes: int = 30,
) -> list[tuple[datetime, datetime]]:
    lesson = con.execute(
        """
        SELECT l.*, i.timezone instructor_tz, s.timezone student_tz
        FROM lessons l
        JOIN users i ON i.id=l.instructor_id
        JOIN users s ON s.id=l.student_id
        WHERE l.id=?
        """,
        (lesson_id,),
    ).fetchone()
    if lesson is None:
        raise ValueError("lesson not found")

    duration = timedelta(minutes=lesson["duration_minutes"])
    search_start = datetime.combine(start_date_jst, time.min, tzinfo=JST).astimezone(UTC)
    search_end = (datetime.combine(end_date_jst, time.min, tzinfo=JST) + timedelta(days=1)).astimezone(UTC)

    results: list[tuple[datetime, datetime]] = []
    cursor = search_start
    step = timedelta(minutes=slot_minutes)
    while cursor + duration <= search_end and len(results) < max_candidates:
        end = cursor + duration
        instructor_ok = inside_recurring_availability(
            con, lesson["instructor_id"], cursor, end, lesson["instructor_tz"]
        )
        student_ok = inside_recurring_availability(
            con, lesson["student_id"], cursor, end, lesson["student_tz"]
        )
        if instructor_ok and student_ok:
            blocked = (
                blocked_by_exception(con, lesson["instructor_id"], cursor, end)
                or blocked_by_exception(con, lesson["student_id"], cursor, end)
                or blocked_by_confirmed_lesson(con, lesson["instructor_id"], cursor, end, lesson_id)
                or blocked_by_confirmed_lesson(con, lesson["student_id"], cursor, end, lesson_id)
            )
            if not blocked:
                results.append((cursor, end))
        cursor += step
    return results
