from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from auth import (
    DUMMY_PASSWORD_HASH,
    hash_password,
    normalize_login_id,
    validate_login_id,
    verify_password,
)
from db import INTEGRITY_ERRORS, get_db, init_db
from i18n import (
    LANGUAGE_COOKIE,
    SUPPORTED_LANGUAGES,
    get_language,
    template_context,
    translate,
    weekday_labels,
)
from location_search import LocationSearchError, search_timezones
from scheduler import (
    blocked_by_confirmed_lesson,
    blocked_by_exception,
    fmt_local,
    inside_recurring_availability,
    to_utc_iso,
)
from settings import SETTINGS
from timezones import get_timezone

BASE_DIR = Path(__file__).resolve().parent
COORDINATOR_TIMEZONE = "Asia/Tokyo"
ROLES = {"owner", "admin", "instructor", "student"}
MANAGEMENT_ROLES = {"owner", "admin"}
INSTRUCTOR_CAPABLE_ROLES = MANAGEMENT_ROLES | {"instructor"}
SELF_REGISTRATION_ROLES = {"instructor", "student"}
logger = logging.getLogger("sat_scheduler")
app = FastAPI(
    title="Everydayprep scheduler",
    version="0.2.0",
    docs_url=None if SETTINGS.is_production else "/docs",
    redoc_url=None if SETTINGS.is_production else "/redoc",
    openapi_url=None if SETTINGS.is_production else "/openapi.json",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(
    directory=BASE_DIR / "templates",
    context_processors=[template_context],
)
templates.env.globals["t"] = translate


def current_user(request: Request | None) -> dict | None:
    if request is None:
        return None
    return getattr(request.state, "current_user", None)


def require_role(request: Request | None, allowed_roles: set[str]) -> dict | None:
    """Enforce a role for HTTP calls while keeping direct domain-level tests usable."""
    user = current_user(request)
    if request is not None and (not user or user["role"] not in allowed_roles):
        raise HTTPException(403, "この操作を行う権限がありません。")
    return user


def is_assigned_instructor(user: dict | None, lesson) -> bool:
    return bool(
        user
        and user["role"] == "instructor"
        and user.get("account_role", user["role"]) in INSTRUCTOR_CAPABLE_ROLES
        and user["id"] == lesson["instructor_id"]
    )


def lesson_participant_role(user: dict | None, lesson) -> str | None:
    if is_assigned_instructor(user, lesson):
        return "instructor"
    if user and user["role"] == "student" and user["id"] == lesson["student_id"]:
        return "student"
    return None


def can_access_lesson(user: dict | None, lesson) -> bool:
    if user is None or user["role"] in MANAGEMENT_ROLES:
        return True
    if user["role"] == "instructor":
        return lesson["instructor_id"] == user["id"]
    if user["role"] == "student":
        return lesson["student_id"] == user["id"]
    return False


def require_self_or_manager(request: Request | None, user_id: int) -> dict | None:
    user = current_user(request)
    if request is not None and (
        not user
        or (user["role"] not in MANAGEMENT_ROLES and user["id"] != user_id)
    ):
        raise HTTPException(403, "他の利用者のスケジュールは変更できません。")
    return user


@app.middleware("http")
async def authentication_gate(request: Request, call_next):
    path = request.url.path
    if path in {"/healthz", "/readyz"}:
        return await call_next(request)
    user = None
    user_id = request.session.get("user_id")
    with get_db() as con:
        if user_id is not None:
            row = con.execute(
                """
                SELECT id, name, role, login_id, timezone, email
                FROM users
                WHERE id=? AND is_active=1 AND login_id IS NOT NULL
                      AND password_hash IS NOT NULL
                """,
                (user_id,),
            ).fetchone()
            if row:
                user = dict(row)
                user["account_role"] = user["role"]
                if user["account_role"] in MANAGEMENT_ROLES:
                    user["permission_mode"] = (
                        "instructor"
                        if request.session.get("permission_mode") == "instructor"
                        else "management"
                    )
                    if user["permission_mode"] == "instructor":
                        user["role"] = "instructor"
                else:
                    user["permission_mode"] = user["role"]
            else:
                request.session.clear()
        has_accounts = bool(
            con.execute(
                """
                SELECT 1 FROM users
                WHERE is_active=1 AND login_id IS NOT NULL
                      AND password_hash IS NOT NULL
                LIMIT 1
                """
            ).fetchone()
        )
    request.state.current_user = user

    public_path = (
        path.startswith("/static/")
        or path.startswith("/language/")
        or path in {"/login", "/register", "/setup", "/api/timezones/search"}
    )
    if (
        not has_accounts
        and path != "/setup"
        and not path.startswith(("/static/", "/language/"))
    ):
        return redirect("/setup")
    if has_accounts and path == "/setup":
        return redirect("/" if user else "/login")
    if path in {"/login", "/register"} and user:
        return redirect("/")
    if not public_path and not user:
        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return redirect(f"/login?next={quote(next_path, safe='')}")

    management_only = (
        path == "/users"
        or path.startswith("/users/")
        or path == "/lessons/new"
        or (request.method == "POST" and path == "/lessons")
        or (request.method == "POST" and path.endswith("/delete") and path.startswith("/lessons/"))
        or (request.method == "POST" and path.endswith("/reopen"))
        or (request.method == "POST" and path.startswith("/candidates/") and path.endswith("/cancel"))
    )
    if management_only and user and user["role"] not in MANAGEMENT_ROLES:
        return HTMLResponse("この操作を行う権限がありません。", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    if request.url.path not in {"/docs", "/redoc", "/openapi.json"}:
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'self'; form-action 'self'; "
            "frame-ancestors 'none'; object-src 'none'",
        )
    if SETTINGS.is_production:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# Added after the authentication middleware so sessions wrap it and are
# available before the authentication gate reads request.session.
app.add_middleware(
    SessionMiddleware,
    secret_key=SETTINGS.session_secret or secrets.token_urlsafe(48),
    session_cookie="sat_scheduler_session",
    max_age=60 * 60 * 12,
    same_site="lax",
    https_only=SETTINGS.https_only,
)


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def safe_return_path(path: str) -> str:
    parsed = urlsplit(path)
    if (
        not path.startswith("/")
        or path.startswith(("//", "/\\"))
        or parsed.scheme
        or parsed.netloc
    ):
        return "/"
    return path


def group_lessons_by_student(lessons):
    """Group lessons by student while keeping each student's lesson order."""
    groups_by_student = {}
    for lesson in lessons:
        student_id = lesson["student_id"]
        if student_id not in groups_by_student:
            groups_by_student[student_id] = {
                "student_id": student_id,
                "student_name": lesson["student_name"],
                "student_tz": lesson["student_tz"],
                "lessons": [],
            }
        groups_by_student[student_id]["lessons"].append(lesson)

    return sorted(
        groups_by_student.values(),
        key=lambda group: (group["student_name"].casefold(), group["student_id"]),
    )


def update_lesson_status_from_candidates(con, lesson_id: int) -> None:
    candidate_summary = con.execute(
        """
        SELECT COUNT(*) candidate_count,
               COALESCE(MAX(
                   instructor_response='accept' AND student_response='accept'
               ), 0) has_agreement
        FROM candidates
        WHERE lesson_id=?
        """,
        (lesson_id,),
    ).fetchone()
    if candidate_summary["candidate_count"] == 0:
        status = "draft"
    elif candidate_summary["has_agreement"]:
        status = "agreed"
    else:
        status = "responding"
    con.execute(
        "UPDATE lessons SET status=? WHERE id=? AND status!='confirmed'",
        (status, lesson_id),
    )


def confirm_lesson_from_candidate(con, candidate, actor: str = "system") -> None:
    if (
        candidate["instructor_response"] != "accept"
        or candidate["student_response"] != "accept"
    ):
        raise HTTPException(400, "講師と生徒の双方が承諾してから確定してください")

    lesson_id = candidate["lesson_id"]
    lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
    start = datetime.fromisoformat(candidate["start_utc"])
    end = datetime.fromisoformat(candidate["end_utc"])
    ensure_within_instructor_schedule(con, lesson, start, end)
    for user_id in (lesson["instructor_id"], lesson["student_id"]):
        if blocked_by_confirmed_lesson(con, user_id, start, end, lesson_id):
            raise HTTPException(400, "この日時は確定済みの別授業と重複しています")

    con.execute(
        """
        UPDATE lessons
        SET status='confirmed', confirmed_start_utc=?, confirmed_end_utc=?
        WHERE id=?
        """,
        (candidate["start_utc"], candidate["end_utc"], lesson_id),
    )
    con.execute(
        "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
        (
            lesson_id,
            actor,
            "lesson_confirmed",
            f"candidate={candidate['id']} start={candidate['start_utc']}",
        ),
    )


def ensure_timezone(name: str) -> str:
    try:
        get_timezone(name)
        return name
    except RuntimeError as exc:
        # Invalid IANA name and missing tzdata both become a clear client-facing error.
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def ensure_zoom_url(value: str) -> str:
    zoom_url = value.strip()
    parsed = urlsplit(zoom_url)
    if (
        len(zoom_url) > 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or any(character.isspace() for character in zoom_url)
    ):
        raise HTTPException(
            400, "Zoom参加リンクは https:// から始まる正しいURLを入力してください。"
        )
    return zoom_url


def ensure_zoom_credential(value: str, label: str, max_length: int) -> str:
    credential = value.strip()
    if not credential or len(credential) > max_length or any(
        character in credential for character in "\r\n"
    ):
        raise HTTPException(
            400, f"{label}は1〜{max_length}文字で正しく入力してください。"
        )
    return credential


def local_datetime_to_utc(start_local: str, timezone_name: str) -> datetime:
    try:
        local_start = datetime.fromisoformat(start_local)
    except ValueError as exc:
        raise HTTPException(400, "日時を正しく入力してください") from exc
    if local_start.tzinfo is not None:
        raise HTTPException(400, "日時は選択したタイムゾーンの現地時刻で入力してください")

    selected_timezone = get_timezone(ensure_timezone(timezone_name.strip()))
    localized_start = local_start.replace(tzinfo=selected_timezone, fold=0)
    start = localized_start.astimezone(timezone.utc)

    # Reject wall-clock times that do not exist or occur twice around DST changes.
    round_trip = start.astimezone(selected_timezone).replace(tzinfo=None)
    if round_trip != local_start:
        raise HTTPException(400, "夏時間の切り替えにより存在しない現地日時です")
    alternate = local_start.replace(tzinfo=selected_timezone, fold=1)
    if alternate.utcoffset() != localized_start.utcoffset():
        raise HTTPException(400, "夏時間の切り替えにより時刻が重複しています。別の時刻を指定してください")
    return start


def proposal_timezones_for_lesson(lesson, language: str = "ja"):
    timezone_owners = {}
    for timezone_name, owner in (
        (COORDINATOR_TIMEZONE, translate("lesson.japan_time", language)),
        (lesson["instructor_tz"], translate("role.instructor", language)),
        (lesson["student_tz"], translate("role.student", language)),
    ):
        timezone_owners.setdefault(timezone_name, []).append(owner)
    owner_separator = "・" if language == "ja" else ", "
    return [
        {
            "name": timezone_name,
            "label": f"{owner_separator.join(owners)} / {timezone_name}",
        }
        for timezone_name, owners in timezone_owners.items()
    ]


def format_share_datetime_range(
    start_utc: str | datetime,
    end_utc: str | datetime,
    timezone_name: str,
    language: str = "ja",
) -> str:
    selected_timezone = get_timezone(timezone_name)
    start = (
        datetime.fromisoformat(start_utc) if isinstance(start_utc, str) else start_utc
    ).astimezone(selected_timezone)
    end = (
        datetime.fromisoformat(end_utc) if isinstance(end_utc, str) else end_utc
    ).astimezone(selected_timezone)
    start_text = start.strftime("%Y/%m/%d (%a) %H:%M")
    end_text = (
        end.strftime("%H:%M")
        if start.date() == end.date()
        else end.strftime("%Y/%m/%d (%a) %H:%M")
    )
    separator = " -　" if language == "ja" else " - "
    return f"{start_text}{separator}{end_text}"


def build_lesson_share_message(lesson, language: str = "ja") -> str:
    """Build a ready-to-paste message for a confirmed lesson."""
    if lesson["status"] != "confirmed" or not lesson["confirmed_start_utc"]:
        return ""

    confirmed_end_utc = lesson["confirmed_end_utc"] or (
        datetime.fromisoformat(lesson["confirmed_start_utc"])
        + timedelta(minutes=lesson["duration_minutes"])
    )
    jst_range = (
        f"{format_share_datetime_range(lesson['confirmed_start_utc'], confirmed_end_utc, COORDINATOR_TIMEZONE, language)}"
        " JST"
    )
    instructor_range = (
        f"{format_share_datetime_range(lesson['confirmed_start_utc'], confirmed_end_utc, lesson['instructor_tz'], language)}"
        f" ({lesson['instructor_tz']})"
    )
    student_range = (
        f"{format_share_datetime_range(lesson['confirmed_start_utc'], confirmed_end_utc, lesson['student_tz'], language)}"
        f" ({lesson['student_tz']})"
    )
    meeting_url = lesson["meeting_url"]
    zoom_meeting_id = lesson["zoom_meeting_id"]
    zoom_password = lesson["zoom_password"]

    if language == "en":
        lines = [
                "[SAT Lesson Confirmed]",
                "The lesson schedule has been confirmed.",
                "",
                f"Subject: {lesson['subject']}",
                f"Duration: {lesson['duration_minutes']} min",
                f"Japan time: {jst_range}",
                f"Instructor: {lesson['instructor_name']}",
                f"Instructor local time: {instructor_range}",
                f"Student: {lesson['student_name']}",
                f"Student local time: {student_range}",
        ]
        zoom_lines = []
        if meeting_url:
            zoom_lines.append(f"Zoom link: {meeting_url}")
        if zoom_meeting_id:
            zoom_lines.append(f"Meeting ID: {zoom_meeting_id}")
        if zoom_password:
            zoom_lines.append(f"Password: {zoom_password}")
        if zoom_lines:
            lines.extend((*zoom_lines, "", "Please use the Zoom information above."))
        else:
            lines.append("Zoom information: Not provided")
        return "\n".join(lines)

    lines = [
            "【SAT講義日程確定】",
            "講義日程が確定しました。",
            "",
            f"内容: {lesson['subject']}",
            f"授業時間: {lesson['duration_minutes']}分",
            f"日本時間: {jst_range}",
            f"講師: {lesson['instructor_name']}",
            f"講師現地時間: {instructor_range}",
            f"生徒: {lesson['student_name']}",
            f"生徒現地時間: {student_range}",
    ]
    zoom_lines = []
    if meeting_url:
        zoom_lines.append(f"Zoom参加リンク: {meeting_url}")
    if zoom_meeting_id:
        zoom_lines.append(f"ミーティングID: {zoom_meeting_id}")
    if zoom_password:
        zoom_lines.append(f"パスワード: {zoom_password}")
    if zoom_lines:
        lines.extend((*zoom_lines, "", "必要に応じて上記のZoom情報をご利用ください。"))
    else:
        lines.append("Zoom情報: 未登録")
    return "\n".join(lines)


def ensure_within_instructor_schedule(con, lesson, start: datetime, end: datetime) -> None:
    instructor = con.execute(
        """
        SELECT timezone FROM users
        WHERE id=? AND role IN ('owner','admin','instructor')
        """,
        (lesson["instructor_id"],),
    ).fetchone()
    if not instructor:
        raise HTTPException(400, "講師情報を確認できません")

    if not inside_recurring_availability(
        con,
        lesson["instructor_id"],
        start,
        end,
        instructor["timezone"],
    ):
        raise HTTPException(
            400,
            "候補日時は講師が設定した授業可能時間の範囲内で指定してください",
        )
    if blocked_by_exception(con, lesson["instructor_id"], start, end):
        raise HTTPException(400, "候補日時は講師の例外予定と重複しています")


@app.get("/api/timezones/search")
def timezone_search(q: str = ""):
    query = q.strip()
    if len(query) > 100:
        raise HTTPException(status_code=400, detail="地名は100文字以内で入力してください。")
    try:
        return {"results": search_timezones(query)}
    except LocationSearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/healthz", include_in_schema=False)
def healthcheck():
    return {"status": "ok"}


@app.get("/readyz", include_in_schema=False)
def readiness_check():
    try:
        with get_db() as con:
            con.execute("SELECT 1").fetchone()
    except Exception:
        logger.exception("Database readiness check failed")
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return {"status": "ready"}


@app.on_event("startup")
def on_startup() -> None:
    SETTINGS.validate()
    init_db()
    logger.info(
        "Application initialized (environment=%s, database=%s)",
        SETTINGS.environment,
        SETTINGS.database_engine,
    )


@app.get("/language/{language}")
def switch_language(language: str, next: str = "/"):
    if language not in SUPPORTED_LANGUAGES:
        raise HTTPException(404, "Unsupported language")
    response = redirect(safe_return_path(next))
    response.set_cookie(
        LANGUAGE_COOKIE,
        language,
        max_age=60 * 60 * 24 * 365,
        httponly=True,
        samesite="lax",
    )
    return response


def render_auth_page(
    request: Request,
    template_name: str,
    *,
    error: str | None = None,
    next_path: str = "/",
    form_values: dict[str, str] | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        request,
        template_name,
        {
            "error": error,
            "next_path": safe_return_path(next_path),
            "form_values": form_values or {},
        },
        status_code=status_code,
    )


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request):
    return render_auth_page(request, "setup.html")


@app.post("/setup", response_class=HTMLResponse)
def setup_owner(
    request: Request,
    name: str = Form(...),
    login_id: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    try:
        normalized_login_id = validate_login_id(login_id)
        if password != password_confirm:
            raise ValueError("確認用パスワードが一致しません。")
        password_hash = hash_password(password)
        if not name.strip():
            raise ValueError("氏名を入力してください。")
    except ValueError as exc:
        return render_auth_page(
            request, "setup.html", error=str(exc), status_code=400
        )

    try:
        with get_db() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute(
                """
                SELECT 1 FROM users
                WHERE is_active=1 AND login_id IS NOT NULL
                      AND password_hash IS NOT NULL
                LIMIT 1
                """
            ).fetchone():
                return redirect("/login")
            user_id = con.execute(
                """
                INSERT INTO users(name, role, login_id, password_hash)
                VALUES(?, 'owner', ?, ?)
                """,
                (name.strip(), normalized_login_id, password_hash),
            ).lastrowid
    except INTEGRITY_ERRORS:
        return render_auth_page(
            request,
            "setup.html",
            error="このログインIDはすでに使用されています。",
            status_code=400,
        )
    request.session.clear()
    request.session["user_id"] = user_id
    return redirect("/")


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return render_auth_page(request, "login.html", next_path=next)


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return render_auth_page(
        request,
        "register.html",
        form_values={"role": "student", "timezone_name": "Asia/Tokyo"},
    )


@app.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    name: str = Form(...),
    role: str = Form(...),
    login_id: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    timezone_name: str = Form("Asia/Tokyo"),
    email: str = Form(""),
):
    form_values = {
        "name": name.strip(),
        "role": role,
        "login_id": login_id.strip(),
        "timezone_name": timezone_name.strip() or "Asia/Tokyo",
        "email": email.strip(),
    }
    try:
        if role not in SELF_REGISTRATION_ROLES:
            raise ValueError("新規登録で選択できる役割は講師または生徒です。")
        if not form_values["name"]:
            raise ValueError("氏名を入力してください。")
        normalized_login_id = validate_login_id(login_id)
        if password != password_confirm:
            raise ValueError("確認用パスワードが一致しません。")
        password_hash = hash_password(password)
        validated_timezone = ensure_timezone(form_values["timezone_name"])
    except HTTPException as exc:
        return render_auth_page(
            request,
            "register.html",
            error=str(exc.detail),
            form_values=form_values,
            status_code=400,
        )
    except ValueError as exc:
        return render_auth_page(
            request,
            "register.html",
            error=str(exc),
            form_values=form_values,
            status_code=400,
        )

    try:
        with get_db() as con:
            user_id = con.execute(
                """
                INSERT INTO users(
                    name, role, login_id, password_hash, timezone, email
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    form_values["name"],
                    role,
                    normalized_login_id,
                    password_hash,
                    validated_timezone,
                    form_values["email"],
                ),
            ).lastrowid
    except INTEGRITY_ERRORS:
        return render_auth_page(
            request,
            "register.html",
            error="このログインIDはすでに使用されています。",
            form_values=form_values,
            status_code=400,
        )

    request.session.clear()
    request.session["user_id"] = user_id
    return redirect("/")


@app.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    login_id: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    normalized_login_id = normalize_login_id(login_id)
    password_to_check = password if len(password) <= 128 else ""
    with get_db() as con:
        user = con.execute(
            """
            SELECT id, password_hash FROM users
            WHERE login_id=? AND is_active=1
            """,
            (normalized_login_id,),
        ).fetchone()
        valid_password = verify_password(
            password_to_check,
            user["password_hash"] if user else DUMMY_PASSWORD_HASH,
        )
        if valid_password:
            con.execute(
                "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",
                (user["id"],),
            )
    if not user or not valid_password:
        return render_auth_page(
            request,
            "login.html",
            error="ログインIDまたはパスワードが正しくありません。",
            next_path=next,
            status_code=400,
        )
    request.session.clear()
    request.session["user_id"] = user["id"]
    return redirect(safe_return_path(next))


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.post("/permission-mode")
def switch_permission_mode(
    request: Request,
    mode: str = Form(...),
    next: str = Form("/"),
):
    actor = current_user(request)
    account_role = actor.get("account_role", actor["role"]) if actor else None
    if account_role not in MANAGEMENT_ROLES:
        raise HTTPException(403, "権限を切り替えられるのは塾長または管理者だけです。")
    if mode not in {"management", "instructor"}:
        raise HTTPException(400, "切り替える権限を正しく選択してください。")
    request.session["permission_mode"] = mode
    return redirect(safe_return_path(next))


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, student_id: str | None = None):
    user = current_user(request)
    selected_student_id = None
    if user and user["role"] == "student":
        selected_student_id = user["id"]
    elif student_id:
        try:
            selected_student_id = int(student_id)
        except ValueError as exc:
            raise HTTPException(400, "生徒を正しく選択してください") from exc

    with get_db() as con:
        status_rows = con.execute(
            "SELECT status, COUNT(*) count FROM lessons GROUP BY status ORDER BY status"
        ).fetchall()
        if user and user["role"] == "student":
            students = con.execute(
                "SELECT id, name FROM users WHERE id=?", (user["id"],)
            ).fetchall()
        elif user and user["role"] == "instructor":
            students = con.execute(
                """
                SELECT DISTINCT s.id, s.name
                FROM users s
                JOIN lessons l ON l.student_id=s.id
                WHERE l.instructor_id=?
                ORDER BY s.name, s.id
                """,
                (user["id"],),
            ).fetchall()
        else:
            students = con.execute(
                "SELECT id, name FROM users WHERE role='student' ORDER BY name, id"
            ).fetchall()
        if selected_student_id is not None and not any(
            student["id"] == selected_student_id for student in students
        ):
            raise HTTPException(404, "生徒が見つかりません")

        filters = []
        lesson_params: list[object] = []
        if user and user["role"] == "instructor":
            filters.append("l.instructor_id=?")
            lesson_params.append(user["id"])
        elif user and user["role"] == "student":
            filters.append("l.student_id=?")
            lesson_params.append(user["id"])
        if selected_student_id is not None:
            filters.append("l.student_id=?")
            lesson_params.append(selected_student_id)
        lesson_filter = f"WHERE {' AND '.join(filters)}" if filters else ""
        lessons = con.execute(
            f"""
            SELECT l.*, i.name instructor_name, s.name student_name,
                   i.timezone instructor_tz, s.timezone student_tz
            FROM lessons l
            JOIN users i ON i.id=l.instructor_id
            JOIN users s ON s.id=l.student_id
            {lesson_filter}
            ORDER BY CASE WHEN l.confirmed_start_utc IS NULL THEN 1 ELSE 0 END,
                     l.confirmed_start_utc, l.id DESC
            LIMIT 30
            """,
            tuple(lesson_params),
        ).fetchall()
        if user and user["role"] in {"instructor", "student"}:
            stats = {}
            for lesson in lessons:
                stats[lesson["status"]] = stats.get(lesson["status"], 0) + 1
        else:
            stats = {row["status"]: row["count"] for row in status_rows}
        lesson_groups = group_lessons_by_student(lessons)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "stats": stats,
            "students": students,
            "selected_student_id": selected_student_id,
            "lesson_groups": lesson_groups,
            "fmt_local": fmt_local,
        },
    )


@app.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        users = con.execute("SELECT * FROM users ORDER BY role, name").fetchall()
    return templates.TemplateResponse(request, "users.html", {"users": users})


@app.post("/users")
def create_user(
    name: str = Form(...),
    role: str = Form(...),
    timezone_name: str = Form("Asia/Tokyo"),
    email: str = Form(""),
    login_id: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
    request: Request = None,
):
    actor = require_role(request, MANAGEMENT_ROLES)
    timezone_name = ensure_timezone(timezone_name.strip())
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="invalid role")
    if actor and actor["role"] != "owner" and role == "owner":
        raise HTTPException(403, "塾長アカウントを登録できるのは塾長だけです。")
    try:
        normalized_login_id = validate_login_id(login_id)
        if password != password_confirm:
            raise ValueError("確認用パスワードが一致しません。")
        password_hash = hash_password(password)
        if not name.strip():
            raise ValueError("氏名を入力してください。")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        with get_db() as con:
            con.execute(
                """
                INSERT INTO users(
                    name, role, login_id, password_hash, timezone, email
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    name.strip(),
                    role,
                    normalized_login_id,
                    password_hash,
                    timezone_name,
                    email.strip(),
                ),
            )
    except INTEGRITY_ERRORS as exc:
        raise HTTPException(400, "このログインIDはすでに使用されています。") from exc
    return redirect("/users")


@app.post("/users/{user_id}/delete")
def delete_user(user_id: int, request: Request = None):
    actor = require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        user = con.execute(
            "SELECT id, role FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user:
            raise HTTPException(404)
        if actor and actor["id"] == user_id:
            raise HTTPException(400, "ログイン中の自分自身は削除できません。")
        if actor and user["role"] == "owner" and actor["role"] != "owner":
            raise HTTPException(403, "塾長アカウントを削除できるのは塾長だけです。")
        if user["role"] == "owner":
            owner_count = con.execute(
                "SELECT COUNT(*) count FROM users WHERE role='owner' AND is_active=1"
            ).fetchone()["count"]
            if owner_count <= 1:
                raise HTTPException(400, "最後の塾長アカウントは削除できません。")

        # lessons references users without ON DELETE CASCADE. Delete the lessons
        # first so their candidates and audit logs are removed by their cascades.
        con.execute(
            "DELETE FROM lessons WHERE instructor_id=? OR student_id=?",
            (user_id, user_id),
        )
        con.execute("DELETE FROM users WHERE id=?", (user_id,))
    return redirect("/users")


@app.post("/users/{user_id}/credentials")
def update_user_credentials(
    user_id: int,
    login_id: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
    request: Request = None,
):
    actor = require_role(request, MANAGEMENT_ROLES)
    try:
        normalized_login_id = validate_login_id(login_id)
        if password != password_confirm:
            raise ValueError("確認用パスワードが一致しません。")
        password_hash = hash_password(password)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    try:
        with get_db() as con:
            user = con.execute(
                "SELECT id, role FROM users WHERE id=?", (user_id,)
            ).fetchone()
            if not user:
                raise HTTPException(404)
            if actor and user["role"] == "owner" and actor["role"] != "owner":
                raise HTTPException(
                    403, "塾長のログイン情報を変更できるのは塾長だけです。"
                )
            con.execute(
                """
                UPDATE users
                SET login_id=?, password_hash=?, is_active=1
                WHERE id=?
                """,
                (normalized_login_id, password_hash, user_id),
            )
    except INTEGRITY_ERRORS as exc:
        raise HTTPException(400, "このログインIDはすでに使用されています。") from exc
    return redirect("/users")


@app.post("/users/{user_id}/promote-to-admin")
def promote_user_to_admin(user_id: int, request: Request = None):
    require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        user = con.execute(
            "SELECT id, role FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not user:
            raise HTTPException(404)
        if user["role"] != "instructor":
            raise HTTPException(400, "管理者に変更できるのは講師だけです。")
        con.execute("UPDATE users SET role='admin' WHERE id=?", (user_id,))
    return redirect("/users")


@app.get("/availability", response_class=HTMLResponse)
def availability_page(request: Request, user_id: int | None = None):
    language = get_language(request)
    user = current_user(request)
    with get_db() as con:
        if user and user["role"] not in MANAGEMENT_ROLES:
            users = con.execute(
                "SELECT * FROM users WHERE id=?",
                (user["id"],),
            ).fetchall()
            user_id = user["id"]
        else:
            users = con.execute(
                """
                SELECT * FROM users
                WHERE role IN ('owner','admin','instructor','student')
                ORDER BY name
                """
            ).fetchall()
        selected = None
        rows = []
        exceptions = []
        if user_id:
            selected = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            rows = con.execute(
                "SELECT * FROM availability WHERE user_id=? ORDER BY weekday,start_time",
                (user_id,),
            ).fetchall()
            exceptions = con.execute(
                "SELECT * FROM exceptions WHERE user_id=? ORDER BY start_utc",
                (user_id,),
            ).fetchall()
    return templates.TemplateResponse(
        request,
        "availability.html",
        {
            "users": users,
            "selected": selected,
            "rows": rows,
            "exceptions": exceptions,
            "fmt_local": fmt_local,
            "weekdays": weekday_labels(language),
        },
    )


@app.post("/availability")
def add_availability(
    user_id: int = Form(...),
    weekday: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    request: Request = None,
):
    require_self_or_manager(request, user_id)
    if start_time >= end_time:
        raise HTTPException(status_code=400, detail="終了時刻は開始時刻より後にしてください")
    with get_db() as con:
        con.execute(
            "INSERT OR IGNORE INTO availability(user_id,weekday,start_time,end_time) VALUES(?,?,?,?)",
            (user_id, weekday, start_time, end_time),
        )
    return redirect(f"/availability?user_id={user_id}")


@app.post("/availability/{availability_id}/delete")
def delete_availability(availability_id: int, request: Request = None):
    with get_db() as con:
        row = con.execute("SELECT user_id FROM availability WHERE id=?", (availability_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        user_id = row["user_id"]
        require_self_or_manager(request, user_id)
        con.execute("DELETE FROM availability WHERE id=?", (availability_id,))
    return redirect(f"/availability?user_id={user_id}")


@app.post("/exceptions")
def add_exception(
    user_id: int = Form(...),
    start_local: str = Form(...),
    end_local: str = Form(...),
    reason: str = Form(""),
    request: Request = None,
):
    require_self_or_manager(request, user_id)
    with get_db() as con:
        user = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise HTTPException(404)
        tz = get_timezone(user["timezone"])
        start = datetime.fromisoformat(start_local).replace(tzinfo=tz).astimezone(timezone.utc)
        end = datetime.fromisoformat(end_local).replace(tzinfo=tz).astimezone(timezone.utc)
        if end <= start:
            raise HTTPException(400, "終了時刻は開始時刻より後にしてください")
        con.execute(
            "INSERT INTO exceptions(user_id,start_utc,end_utc,reason) VALUES(?,?,?,?)",
            (user_id, to_utc_iso(start), to_utc_iso(end), reason.strip()),
        )
    return redirect(f"/availability?user_id={user_id}")


@app.post("/exceptions/{exception_id}/delete")
def delete_exception(exception_id: int, request: Request = None):
    with get_db() as con:
        row = con.execute("SELECT user_id FROM exceptions WHERE id=?", (exception_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        user_id = row["user_id"]
        require_self_or_manager(request, user_id)
        con.execute("DELETE FROM exceptions WHERE id=?", (exception_id,))
    return redirect(f"/availability?user_id={user_id}")


@app.get("/lessons/new", response_class=HTMLResponse)
def lesson_new_page(request: Request):
    require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        instructors = con.execute(
            """
            SELECT * FROM users
            WHERE role IN ('owner','admin','instructor')
            ORDER BY name
            """
        ).fetchall()
        students = con.execute("SELECT * FROM users WHERE role='student' ORDER BY name").fetchall()
    return templates.TemplateResponse(
        request,
        "lesson_new.html",
        {"instructors": instructors, "students": students},
    )


@app.post("/lessons")
def create_lesson(
    instructor_id: int = Form(...),
    student_id: int = Form(...),
    subject: str = Form("SAT"),
    duration_minutes: int = Form(60),
    request: Request = None,
):
    actor = require_role(request, MANAGEMENT_ROLES)
    if duration_minutes < 30 or duration_minutes > 240:
        raise HTTPException(400, "duration out of range")
    with get_db() as con:
        instructor = con.execute(
            """
            SELECT id FROM users
            WHERE id=? AND role IN ('owner','admin','instructor')
            """,
            (instructor_id,),
        ).fetchone()
        student = con.execute(
            "SELECT id FROM users WHERE id=? AND role='student'",
            (student_id,),
        ).fetchone()
        if not instructor:
            raise HTTPException(400, "担当講師を正しく選択してください")
        if not student:
            raise HTTPException(400, "生徒を正しく選択してください")
        cur = con.execute(
            "INSERT INTO lessons(instructor_id,student_id,subject,duration_minutes,status) VALUES(?,?,?,?, 'draft')",
            (instructor_id, student_id, subject.strip() or "SAT", duration_minutes),
        )
        lesson_id = cur.lastrowid
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (
                lesson_id,
                actor["role"] if actor else "admin",
                "lesson_created",
                subject,
            ),
        )
    return redirect(f"/lessons/{lesson_id}")


@app.post("/lessons/{lesson_id}/delete")
def delete_lesson(lesson_id: int, request: Request = None):
    require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        lesson = con.execute(
            "SELECT id FROM lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        if not lesson:
            raise HTTPException(404)
        con.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
    return redirect("/")


@app.get("/lessons/{lesson_id}", response_class=HTMLResponse)
def lesson_detail(request: Request, lesson_id: int):
    language = get_language(request)
    user = current_user(request)
    with get_db() as con:
        lesson = con.execute(
            """
            SELECT l.*, i.name instructor_name, i.timezone instructor_tz,
                   s.name student_name, s.timezone student_tz
            FROM lessons l
            JOIN users i ON i.id=l.instructor_id
            JOIN users s ON s.id=l.student_id
            WHERE l.id=?
            """,
            (lesson_id,),
        ).fetchone()
        if not lesson:
            raise HTTPException(404)
        if not can_access_lesson(user, lesson):
            raise HTTPException(403, "この授業を閲覧する権限がありません。")
        candidates = con.execute(
            "SELECT * FROM candidates WHERE lesson_id=? ORDER BY start_utc",
            (lesson_id,),
        ).fetchall()
        instructor_availability = con.execute(
            """
            SELECT * FROM availability
            WHERE user_id=?
            ORDER BY weekday,start_time
            """,
            (lesson["instructor_id"],),
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "lesson_detail.html",
        {
            "lesson": lesson,
            "share_message": build_lesson_share_message(lesson, language),
            "zoom_ready": bool(
                lesson["meeting_url"]
                or lesson["zoom_meeting_id"]
                or lesson["zoom_password"]
            ),
            "candidates": candidates,
            "fmt_local": fmt_local,
            "response_labels": {
                response: translate(f"response.{response}", language)
                for response in ("pending", "accept", "reject")
            },
            "proposal_timezones": proposal_timezones_for_lesson(lesson, language),
            "instructor_availability": instructor_availability,
            "weekdays": weekday_labels(language),
            "can_act_as_instructor": is_assigned_instructor(user, lesson),
        },
    )


@app.post("/lessons/{lesson_id}/zoom")
def update_lesson_zoom(
    lesson_id: int,
    meeting_url: Annotated[str, Form()] = "",
    zoom_meeting_id: Annotated[str, Form()] = "",
    zoom_password: Annotated[str, Form()] = "",
    request: Request = None,
):
    actor = current_user(request)

    with get_db() as con:
        lesson = con.execute(
            "SELECT id, instructor_id, status FROM lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        if not lesson:
            raise HTTPException(404)
        if request is not None and (
            not actor
            or not is_assigned_instructor(actor, lesson)
        ):
            raise HTTPException(
                403, "Zoom情報を登録できるのは担当講師だけです。"
            )
        if lesson["status"] != "confirmed":
            raise HTTPException(400, "Zoom情報は講義日程の確定後に登録してください。")

        meeting_url = meeting_url.strip()
        zoom_meeting_id = zoom_meeting_id.strip()
        zoom_password = zoom_password.strip()
        meeting_url = ensure_zoom_url(meeting_url) if meeting_url else None
        zoom_meeting_id = (
            ensure_zoom_credential(zoom_meeting_id, "ミーティングID", 64)
            if zoom_meeting_id
            else None
        )
        zoom_password = (
            ensure_zoom_credential(zoom_password, "パスワード", 128)
            if zoom_password
            else None
        )
        con.execute(
            """
            UPDATE lessons
            SET meeting_url=?, zoom_meeting_id=?, zoom_password=?
            WHERE id=?
            """,
            (meeting_url, zoom_meeting_id, zoom_password, lesson_id),
        )
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (
                lesson_id,
                actor["role"] if actor else "instructor",
                "zoom_information_updated",
                "Zoom link, meeting ID, and password updated",
            ),
        )
    return redirect(f"/lessons/{lesson_id}")


@app.post("/lessons/{lesson_id}/proposals")
def propose_lesson_candidate(
    lesson_id: int,
    start_local: Annotated[str, Form()],
    proposed_by: Annotated[str, Form()] = "admin",
    input_timezone: Annotated[str, Form()] = COORDINATOR_TIMEZONE,
    request: Request = None,
):
    if proposed_by not in {"admin", "instructor", "student"}:
        raise HTTPException(400, "提示者を正しく選択してください")
    if proposed_by == "admin":
        input_timezone = COORDINATOR_TIMEZONE

    with get_db() as con:
        lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        if not lesson:
            raise HTTPException(404)
        actor = current_user(request)
        if request is not None:
            if not actor or not can_access_lesson(actor, lesson):
                raise HTTPException(403, "この授業を変更する権限がありません。")
            participant_role = lesson_participant_role(actor, lesson)
            if actor["role"] in MANAGEMENT_ROLES:
                proposed_by = (
                    "instructor"
                    if proposed_by == "instructor" and participant_role == "instructor"
                    else "admin"
                )
            elif participant_role:
                proposed_by = participant_role
            else:
                raise HTTPException(403, "候補日時を提示する権限がありません。")
            if proposed_by == "admin":
                input_timezone = COORDINATOR_TIMEZONE
        if lesson["status"] == "confirmed":
            raise HTTPException(400, "確定済み授業には日時を提示できません")

        start = local_datetime_to_utc(start_local, input_timezone)
        end = start + timedelta(minutes=lesson["duration_minutes"])
        ensure_within_instructor_schedule(con, lesson, start, end)

        for user_id in (lesson["instructor_id"], lesson["student_id"]):
            if blocked_by_confirmed_lesson(con, user_id, start, end, lesson_id):
                raise HTTPException(400, "この日時は確定済みの別授業と重複しています")

        start_utc = to_utc_iso(start)
        if con.execute(
            "SELECT 1 FROM candidates WHERE lesson_id=? AND start_utc=?",
            (lesson_id, start_utc),
        ).fetchone():
            raise HTTPException(400, "同じ日時はすでに提示されています")

        instructor_response = "accept" if proposed_by == "instructor" else "pending"
        student_response = "accept" if proposed_by == "student" else "pending"
        candidate_id = con.execute(
            """
            INSERT INTO candidates(
                lesson_id, start_utc, end_utc, proposed_by,
                instructor_response, student_response
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                lesson_id,
                start_utc,
                to_utc_iso(end),
                proposed_by,
                instructor_response,
                student_response,
            ),
        ).lastrowid
        update_lesson_status_from_candidates(con, lesson_id)
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (
                lesson_id,
                proposed_by,
                "datetime_proposed",
                (
                    f"candidate={candidate_id} start={start_utc} "
                    f"input={start_local} timezone={input_timezone}"
                ),
            ),
        )
    return redirect(f"/lessons/{lesson_id}")


@app.post("/candidates/{candidate_id}/cancel")
def cancel_candidate(candidate_id: int, request: Request = None):
    actor = require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        candidate = con.execute(
            "SELECT * FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if not candidate:
            raise HTTPException(404)

        lesson_id = candidate["lesson_id"]
        lesson = con.execute(
            "SELECT status FROM lessons WHERE id=?", (lesson_id,)
        ).fetchone()
        if lesson["status"] == "confirmed":
            raise HTTPException(400, "確定済み授業の候補は取り消せません")

        con.execute("DELETE FROM candidates WHERE id=?", (candidate_id,))
        update_lesson_status_from_candidates(con, lesson_id)
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (
                lesson_id,
                actor["role"] if actor else "admin",
                "datetime_cancelled",
                f"candidate={candidate_id} start={candidate['start_utc']}",
            ),
        )
    return redirect(f"/lessons/{lesson_id}")


@app.post("/candidates/{candidate_id}/respond")
def respond_candidate(
    candidate_id: int,
    role: str = Form(...),
    response: str = Form(...),
    request: Request = None,
):
    actor = current_user(request)
    if role not in {"instructor", "student"} or response not in {"accept", "reject", "pending"}:
        raise HTTPException(400, "invalid response")
    with get_db() as con:
        candidate = con.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if not candidate:
            raise HTTPException(404)
        lesson_id = candidate["lesson_id"]
        lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        if request is not None:
            participant_role = lesson_participant_role(actor, lesson)
            if not participant_role:
                raise HTTPException(403, "回答できるのは担当の講師または生徒だけです。")
            role = participant_role
        column = "instructor_response" if role == "instructor" else "student_response"
        if actor and not can_access_lesson(actor, lesson):
            raise HTTPException(403, "この授業へ回答する権限がありません。")
        if lesson["status"] == "confirmed":
            raise HTTPException(400, "確定済み授業の回答は変更できません")
        if candidate["proposed_by"] == role:
            raise HTTPException(400, "提示者本人は承諾済みのため回答を変更できません")
        con.execute(f"UPDATE candidates SET {column}=? WHERE id=?", (response, candidate_id))
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (lesson_id, role, "candidate_response", f"candidate={candidate_id} response={response}"),
        )
        updated_candidate = con.execute(
            "SELECT * FROM candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if (
            updated_candidate["instructor_response"] == "accept"
            and updated_candidate["student_response"] == "accept"
        ):
            confirm_lesson_from_candidate(con, updated_candidate)
        else:
            update_lesson_status_from_candidates(con, lesson_id)
    return redirect(f"/lessons/{lesson_id}")


@app.post("/lessons/{lesson_id}/reopen")
def reopen_lesson(lesson_id: int, request: Request = None):
    actor = require_role(request, MANAGEMENT_ROLES)
    with get_db() as con:
        lesson = con.execute("SELECT * FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        if not lesson:
            raise HTTPException(404)
        con.execute(
            """
            UPDATE lessons
            SET status='draft', confirmed_start_utc=NULL, confirmed_end_utc=NULL,
                meeting_url=NULL, zoom_meeting_id=NULL, zoom_password=NULL
            WHERE id=?
            """,
            (lesson_id,),
        )
        con.execute("DELETE FROM candidates WHERE lesson_id=?", (lesson_id,))
        con.execute(
            "INSERT INTO audit_logs(lesson_id,actor,action,detail) VALUES(?,?,?,?)",
            (
                lesson_id,
                actor["role"] if actor else "admin",
                "lesson_reopened",
                "schedule change requested",
            ),
        )
    return redirect(f"/lessons/{lesson_id}")
