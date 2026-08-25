from __future__ import annotations

from urllib.parse import quote

from fastapi import Request


LANGUAGE_COOKIE = "sat_scheduler_language"
SUPPORTED_LANGUAGES = {"ja", "en"}

TRANSLATIONS = {
    "nav.dashboard": {"ja": "授業一覧", "en": "Lessons"},
    "nav.users": {"ja": "利用者", "en": "Users"},
    "nav.availability": {"ja": "スケジュール範囲", "en": "Availability"},
    "nav.new_lesson": {"ja": "授業作成", "en": "New Lesson"},
    "nav.logout": {"ja": "ログアウト", "en": "Log out"},
    "permission.mode.management": {
        "ja": "管理者権限",
        "en": "Administrator Access",
    },
    "permission.mode.instructor": {
        "ja": "講師権限",
        "en": "Instructor Access",
    },
    "permission.switch_to_management": {
        "ja": "管理者権限に切替",
        "en": "Switch to Administrator",
    },
    "permission.switch_to_instructor": {
        "ja": "講師権限に切替",
        "en": "Switch to Instructor",
    },
    "auth.login": {"ja": "ログイン", "en": "Log In"},
    "auth.login_description": {
        "ja": "SAT Schedulerを利用するにはログインしてください。",
        "en": "Log in to use SAT Scheduler.",
    },
    "auth.register": {"ja": "新規登録", "en": "Create Account"},
    "auth.register_description": {
        "ja": "講師または生徒のアカウントを作成します。塾長・管理者アカウントはこの画面では作成できません。",
        "en": "Create an instructor or student account. School owner and administrator accounts cannot be created here.",
    },
    "auth.register_submit": {"ja": "アカウントを作成", "en": "Create Account"},
    "auth.no_account": {
        "ja": "アカウントをお持ちでない方",
        "en": "Don't have an account?",
    },
    "auth.have_account": {
        "ja": "すでにアカウントをお持ちの方",
        "en": "Already have an account?",
    },
    "auth.setup": {"ja": "初回セットアップ", "en": "Initial Setup"},
    "auth.setup_description": {
        "ja": "最初の「塾長」アカウントを作成します。このアカウントでログイン後、管理者・講師・生徒を登録できます。",
        "en": "Create the first School Owner account. After signing in, you can register administrators, instructors, and students.",
    },
    "auth.setup_submit": {
        "ja": "塾長アカウントを作成",
        "en": "Create School Owner Account",
    },
    "footer": {
        "ja": "MVP — 内部時刻はUTC / 表示は日本時間・現地時間",
        "en": "MVP — internal UTC / display JST & local time",
    },
    "role.owner": {"ja": "塾長", "en": "School Owner"},
    "role.instructor": {"ja": "講師", "en": "Instructor"},
    "role.student": {"ja": "生徒", "en": "Student"},
    "role.admin": {"ja": "管理者", "en": "Administrator"},
    "status.draft": {"ja": "日時未提示", "en": "Unscheduled"},
    "status.responding": {"ja": "回答待ち", "en": "Awaiting Responses"},
    "status.agreed": {"ja": "双方合意", "en": "Agreed"},
    "status.confirmed": {"ja": "確定", "en": "Confirmed"},
    "response.pending": {"ja": "回答待ち", "en": "Pending"},
    "response.accept": {"ja": "承諾", "en": "Accepted"},
    "response.reject": {"ja": "不可", "en": "Declined"},
    "common.name": {"ja": "氏名", "en": "Name"},
    "common.role": {"ja": "役割", "en": "Role"},
    "common.student": {"ja": "生徒", "en": "Student"},
    "common.instructor": {"ja": "講師", "en": "Instructor"},
    "common.subject": {"ja": "内容", "en": "Subject"},
    "common.status": {"ja": "状態", "en": "Status"},
    "common.actions": {"ja": "操作", "en": "Actions"},
    "common.display": {"ja": "表示", "en": "Show"},
    "common.add": {"ja": "追加", "en": "Add"},
    "common.delete": {"ja": "削除", "en": "Delete"},
    "common.open": {"ja": "開く", "en": "Open"},
    "common.start": {"ja": "開始", "en": "Start"},
    "common.end": {"ja": "終了", "en": "End"},
    "common.reason": {"ja": "理由", "en": "Reason"},
    "common.weekday": {"ja": "曜日", "en": "Day"},
    "common.time": {"ja": "時間", "en": "Time"},
    "common.select": {"ja": "選択", "en": "Select"},
    "common.optional": {"ja": "（任意）", "en": "(optional)"},
    "dashboard.page_title": {"ja": "授業一覧", "en": "Lessons"},
    "dashboard.heading": {"ja": "授業一覧", "en": "Lessons"},
    "dashboard.lessons": {"ja": "授業一覧", "en": "Lessons"},
    "dashboard.lesson_count": {"ja": "{count}件", "en": "{count} {unit}"},
    "dashboard.confirmed_time": {"ja": "確定日時", "en": "Confirmed Time"},
    "dashboard.delete_confirm": {
        "ja": "この授業を削除しますか？候補日時や履歴も削除され、元に戻せません。",
        "en": "Delete this lesson? Its proposed times and history will also be permanently deleted.",
    },
    "dashboard.empty_selected": {
        "ja": "選択した生徒にはまだ授業がありません。",
        "en": "The selected student has no lessons yet.",
    },
    "dashboard.empty": {
        "ja": "まだ授業がありません。「授業作成」から開始してください。",
        "en": "There are no lessons yet. Start by creating a lesson.",
    },
    "users.page_title": {"ja": "利用者", "en": "Users"},
    "users.new": {"ja": "新規登録", "en": "Add User"},
    "users.location_search": {
        "ja": "地名からタイムゾーンを検索",
        "en": "Search for a timezone by location",
    },
    "users.location_placeholder": {
        "ja": "例: 東京、New York、London",
        "en": "e.g. Tokyo, New York, London",
    },
    "users.search": {"ja": "検索", "en": "Search"},
    "users.search_prompt": {
        "ja": "2文字以上の地名を入力し、候補を選択してください。",
        "en": "Enter at least two characters and select a location.",
    },
    "users.search_results": {"ja": "地名の検索候補", "en": "Location search results"},
    "users.search_data": {"ja": "検索データ", "en": "Search data"},
    "users.timezone": {"ja": "タイムゾーン", "en": "Timezone"},
    "users.timezone_help": {
        "ja": "候補を選ぶと自動入力されます。IANA名を直接入力することもできます。",
        "en": "Selecting a result fills this automatically. You can also enter an IANA name directly.",
    },
    "users.email": {"ja": "メール", "en": "Email"},
    "users.login_id": {"ja": "ログインID", "en": "Login ID"},
    "users.login_id_help": {
        "ja": "3〜64文字の半角英数字と . _ @ + - が使えます。",
        "en": "Use 3–64 letters, numbers, and . _ @ + -.",
    },
    "users.password": {"ja": "パスワード", "en": "Password"},
    "users.password_confirm": {"ja": "パスワード（確認）", "en": "Confirm Password"},
    "users.password_help": {"ja": "8文字以上", "en": "At least 8 characters"},
    "users.no_login": {"ja": "未設定（旧データ）", "en": "Not set (legacy data)"},
    "users.credentials": {"ja": "ログイン設定", "en": "Login Settings"},
    "users.credentials_help": {
        "ja": "ログインIDの変更またはパスワードの再設定",
        "en": "Change the login ID or reset the password",
    },
    "users.credentials_save": {"ja": "ログイン情報を保存", "en": "Save Login"},
    "users.register": {"ja": "登録", "en": "Register"},
    "users.registered": {"ja": "登録済み", "en": "Registered Users"},
    "users.promote_to_admin": {
        "ja": "管理者に変更",
        "en": "Make Administrator",
    },
    "users.promote_to_admin_confirm": {
        "ja": "この講師を管理者に変更しますか？担当中の授業は保持され、講師権限へ切り替えて引き続き担当できます。",
        "en": "Make this instructor an administrator? Assigned lessons will be preserved and remain available in Instructor Access.",
    },
    "users.delete_confirm": {
        "ja": "この利用者を削除しますか？関連する授業、候補日時、空き時間、例外予定、履歴も削除され、元に戻せません。",
        "en": "Delete this user? Related lessons, proposed times, availability, exceptions, and history will also be permanently deleted.",
    },
    "availability.page_title": {"ja": "スケジュール範囲", "en": "Availability"},
    "availability.heading": {
        "ja": "スケジュール範囲・例外予定",
        "en": "Availability & Exceptions",
    },
    "availability.description": {
        "ja": "講師の通常授業可能時間を曜日ごとに設定します。範囲が未設定の場合は時間帯の制限なしとして扱い、設定した場合は授業時間全体が範囲に収まり、例外予定と重複しない候補だけ登録できます。",
        "en": "Set each instructor's regular availability by day. With no range set, there is no time restriction. When ranges are set, a proposed lesson must fit entirely within one and must not overlap an exception.",
    },
    "availability.user": {"ja": "利用者", "en": "User"},
    "availability.regular_for": {
        "ja": "{name} の通常授業可能時間",
        "en": "Regular Availability for {name}",
    },
    "availability.local_time": {
        "ja": "入力時刻は {timezone} の現地時間です。",
        "en": "Enter times in the local time for {timezone}.",
    },
    "availability.unrestricted": {
        "ja": "未設定（時間帯の制限なし）",
        "en": "Not set (no time restriction)",
    },
    "availability.exceptions": {"ja": "例外予定（授業不可）", "en": "Exceptions (Unavailable)"},
    "availability.reason_placeholder": {
        "ja": "ゼミ、移動、学校行事など",
        "en": "Seminar, travel, school event, etc.",
    },
    "availability.local_datetime": {"ja": "現地日時", "en": "Local Date & Time"},
    "availability.no_exceptions": {
        "ja": "未設定（例外による制限なし）",
        "en": "Not set (no exception restrictions)",
    },
    "lesson_new.page_title": {"ja": "授業作成", "en": "New Lesson"},
    "lesson_new.heading": {"ja": "授業を作成", "en": "Create a Lesson"},
    "lesson_new.missing_users": {
        "ja": "先に講師と生徒を登録してください。",
        "en": "Register an instructor and a student first.",
    },
    "lesson_new.content": {"ja": "内容", "en": "Content"},
    "lesson_new.duration": {"ja": "授業時間（分）", "en": "Duration (minutes)"},
    "lesson_new.create": {"ja": "作成", "en": "Create"},
    "lesson.page_title": {"ja": "授業 #{id}", "en": "Lesson #{id}"},
    "lesson.duration": {"ja": "{minutes}分", "en": "{minutes} min"},
    "lesson.confirmed": {"ja": "確定済み", "en": "Confirmed"},
    "lesson.japan_time": {"ja": "日本時間", "en": "Japan Time"},
    "lesson.zoom_heading": {"ja": "Zoom参加情報", "en": "Zoom Access Details"},
    "lesson.zoom_waiting": {
        "ja": "Zoom情報は未登録です。必要な場合のみ入力できます。",
        "en": "No Zoom details have been provided. They can be added if needed.",
    },
    "lesson.zoom_link": {"ja": "Zoom参加リンク", "en": "Zoom Link"},
    "lesson.zoom_meeting_id": {"ja": "ミーティングID", "en": "Meeting ID"},
    "lesson.zoom_password": {"ja": "パスワード", "en": "Password"},
    "lesson.zoom_form_help": {
        "ja": "Zoom情報は任意です。必要な項目だけ入力でき、すべて空欄で保存すると登録済み情報を削除できます。",
        "en": "Zoom details are optional. Enter only the fields needed, or save all fields blank to remove existing details.",
    },
    "lesson.zoom_save": {"ja": "Zoom情報を保存", "en": "Save Zoom Details"},
    "lesson.share_heading": {"ja": "講義情報を共有", "en": "Share Lesson Details"},
    "lesson.share_description": {
        "ja": "下の文面をコピーして、LINEやメールなどへ貼り付けて共有できます。",
        "en": "Copy the message below and paste it into LINE, email, or another messaging app.",
    },
    "lesson.share_copy": {"ja": "メッセージをコピー", "en": "Copy Message"},
    "lesson.share_copied": {"ja": "コピーしました", "en": "Copied"},
    "lesson.share_failed": {
        "ja": "コピーできませんでした。文面を選択してコピーしてください。",
        "en": "Could not copy. Select the message and copy it manually.",
    },
    "lesson.reopen_confirm": {
        "ja": "日程変更として再オープンしますか？",
        "en": "Reopen this lesson to reschedule it?",
    },
    "lesson.reopen": {"ja": "日程変更として再オープン", "en": "Reopen to Reschedule"},
    "lesson.propose": {"ja": "候補日時を提示", "en": "Propose Lesson Times"},
    "lesson.availability_for": {
        "ja": "{name} の授業可能時間",
        "en": "Availability for {name}",
    },
    "lesson.input_basis": {"ja": "入力基準: {timezone}", "en": "Input timezone: {timezone}"},
    "lesson.range_note": {
        "ja": "授業の開始から終了までが上記範囲内に収まる候補だけ追加できます。",
        "en": "You can only add times where the full lesson fits within a range above.",
    },
    "lesson.no_range": {
        "ja": "授業可能時間が未設定のため、時間帯の制限はありません。自由に候補日時を追加できます。",
        "en": "No availability is set, so there is no time restriction. You can freely add proposed times.",
    },
    "lesson.change_range": {"ja": "範囲を設定・変更", "en": "Set or Change Availability"},
    "lesson.coordinator_proposes": {"ja": "管理者が提示", "en": "Administrator Proposal"},
    "lesson.coordinator_method": {
        "ja": "日本時間で候補を指定します。講師と生徒の双方が承諾すると確定します。",
        "en": "Propose a time in Japan time. It is confirmed when both the instructor and student accept.",
    },
    "lesson.proposal_datetime_japan": {
        "ja": "提示する日時（日本時間）",
        "en": "Proposed Date & Time (Japan Time)",
    },
    "lesson.coordinator_submit": {"ja": "管理者として提示", "en": "Propose as Administrator"},
    "lesson.participant_proposes": {
        "ja": "講師・生徒が提示",
        "en": "Instructor or Student Proposal",
    },
    "lesson.participant_method": {
        "ja": "選択したタイムゾーンの現地時刻で入力します。日本時間に変換して候補へ追加され、相手が承諾すると確定します。",
        "en": "Enter a local time in the selected timezone. It is converted to Japan time and confirmed when the other participant accepts.",
    },
    "lesson.proposer": {"ja": "提示者", "en": "Proposed By"},
    "lesson.input_timezone": {"ja": "入力するタイムゾーン", "en": "Input Timezone"},
    "lesson.proposal_datetime_local": {
        "ja": "候補日時（選択したタイムゾーンの現地時刻）",
        "en": "Proposed Date & Time (local time in selected timezone)",
    },
    "lesson.add_candidate": {"ja": "候補日時を追加", "en": "Add Proposed Time"},
    "lesson.candidates": {"ja": "候補", "en": "Proposed Times"},
    "lesson.proposal.admin": {"ja": "管理者からの提示", "en": "Proposed by administrator"},
    "lesson.proposal.instructor": {"ja": "講師からの提示", "en": "Proposed by instructor"},
    "lesson.proposal.student": {"ja": "生徒からの提示", "en": "Proposed by student"},
    "lesson.proposal.legacy": {"ja": "従来の候補", "en": "Existing proposal"},
    "lesson.proposer_accepted": {"ja": "提示者（承諾済み）", "en": "Proposer (accepted)"},
    "lesson.cancel_confirm": {
        "ja": "この候補日時を取り消しますか？",
        "en": "Cancel this proposed time?",
    },
    "lesson.cancel": {"ja": "この候補を取り消す", "en": "Cancel This Proposal"},
    "lesson.no_candidates": {
        "ja": "提示された日時はまだありません。上のいずれかの方法で候補日時を入力してください。",
        "en": "No times have been proposed yet. Add one using either method above.",
    },
}


def normalize_language(language: object) -> str:
    return language if isinstance(language, str) and language in SUPPORTED_LANGUAGES else "ja"


def translate(key: str, language: object = "ja", **values: object) -> str:
    selected_language = normalize_language(language)
    translations = TRANSLATIONS.get(key)
    text = translations.get(selected_language, key) if translations else key
    if key == "dashboard.lesson_count" and selected_language == "en":
        values["unit"] = "lesson" if values.get("count") == 1 else "lessons"
    return text.format(**values)


def get_language(request: Request) -> str:
    return normalize_language(request.cookies.get(LANGUAGE_COOKIE))


def template_context(request: Request) -> dict[str, object]:
    language = get_language(request)
    next_language = "en" if language == "ja" else "ja"
    current_url = request.url.path
    if request.url.query:
        current_url = f"{current_url}?{request.url.query}"
    auth_context_set = hasattr(request.state, "current_user")
    current_user = getattr(request.state, "current_user", None)
    account_role = (
        current_user.get("account_role", current_user["role"])
        if current_user
        else None
    )
    can_switch_permission_mode = account_role in {"owner", "admin"}
    permission_mode = (
        current_user.get("permission_mode", "management")
        if can_switch_permission_mode
        else None
    )
    return {
        "lang": language,
        "language_switch_url": (
            f"/language/{next_language}?next={quote(current_url, safe='')}"
        ),
        "language_switch_label": "English" if next_language == "en" else "日本語",
        "language_switch_aria": (
            "Switch to English" if next_language == "en" else "日本語に切り替える"
        ),
        "current_user": current_user,
        "current_path": current_url,
        "auth_context_set": auth_context_set,
        "account_role": account_role,
        "can_switch_permission_mode": can_switch_permission_mode,
        "permission_mode": permission_mode,
        "next_permission_mode": (
            "management" if permission_mode == "instructor" else "instructor"
        ),
        "is_management": bool(
            current_user and current_user["role"] in {"owner", "admin"}
        ),
    }


def weekday_labels(language: object = "ja") -> list[str]:
    if normalize_language(language) == "en":
        return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return ["月", "火", "水", "木", "金", "土", "日"]
