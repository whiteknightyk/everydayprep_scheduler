from __future__ import annotations

def send_notification(channel: str, recipient: str, message: str) -> None:
    """MVPでは送信せず標準出力に記録するだけ。

    本番では LINE Messaging API / Email 送信処理へ差し替える。
    """
    print(f"[{channel}] to={recipient}: {message}")
