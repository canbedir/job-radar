"""Telegram delivery.

The Bot API is free with no per-message cost, which is why it was chosen over
WhatsApp (whose Business API bills per conversation and requires a verified
business account).

Credentials come from the environment only, never from config.yml:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import html
import os
import sys

import requests

from .models import Job

API = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram rejects messages above 4096 characters.
MAX_LEN = 4000


class Telegram:
    def __init__(self, token: str | None = None, chat_id: str | None = None) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def _post(self, payload: dict) -> bool:
        try:
            resp = requests.post(
                API.format(token=self.token),
                json={"chat_id": self.chat_id, **payload},
                timeout=15,
            )
        except requests.RequestException as exc:
            print(f"WARN telegram request failed: {exc}", file=sys.stderr)
            return False

        if resp.status_code != 200:
            # Surface the API's own reason; it is usually a bad token or chat id.
            print(f"WARN telegram HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
            return False
        return True

    def send_job(self, job: Job) -> bool:
        title = html.escape(job.title)
        company = html.escape(job.company)
        location = html.escape(job.location or "-")

        lines = [f"<b>{title}</b>", f"🏢 {company}"]
        lines.append(f"📍 {location}" + ("  ·  🌍 remote" if job.remote else ""))
        if job.posted_at:
            lines.append(f"🗓 {job.posted_at}")
        if job.years_required is not None:
            # Shown, never filtered on: the posting's own wish, for the reader
            # to weigh rather than the radar to decide.
            lines.append(f"⏳ asks for {job.years_required}+ years")
        lines.append(f"⭐ score {job.score}  ·  <i>{html.escape(job.source)}</i>")
        if job.matched_terms:
            terms = html.escape(", ".join(job.matched_terms[:8]))
            lines.append(f"<code>{terms}</code>")

        text = "\n".join(lines)[:MAX_LEN]
        return self._post({
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": [[{"text": "Apply ↗", "url": job.url}]]},
        })

    def send_text(self, text: str) -> bool:
        return self._post({
            "text": text[:MAX_LEN],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })

    def send_warning(self, message: str) -> bool:
        """Report a degraded run so failures are never silent."""
        return self.send_text(f"⚠️ <b>job radar</b>\n{html.escape(message)}")
