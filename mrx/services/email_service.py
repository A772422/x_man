"""Email via a provider interface. Ships an IMAP/SMTP provider (works with Gmail/Outlook/etc. using an
app-specific password held in the OS keychain or env vars — never in settings, DB or logs).
Workflow is Draft → user review → send. Sending is a separate permission."""
from __future__ import annotations

import asyncio
import email as emaillib
import imaplib
import json
import mimetypes
import smtplib
import time
from abc import ABC, abstractmethod
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

from ..tools.registry import Outcome, Tool, ToolError


class EmailProvider(ABC):
    name = "abstract"

    @abstractmethod
    def configured(self) -> tuple[bool, str]: ...
    @abstractmethod
    def list(self, limit: int, unread_only: bool) -> list[dict]: ...
    @abstractmethod
    def search(self, query: str, limit: int) -> list[dict]: ...
    @abstractmethod
    def get(self, uid: str) -> dict: ...
    @abstractmethod
    def send(self, msg: EmailMessage) -> dict: ...
    @abstractmethod
    def mark(self, uid: str, read: bool) -> bool: ...


def _dh(v: str | None) -> str:
    return str(make_header(decode_header(v))) if v else ""


class ImapSmtpProvider(EmailProvider):
    name = "imap-smtp"

    def __init__(self, secrets):
        self.s = secrets

    def cfg(self) -> dict:
        addr = self.s.get("MRX_EMAIL_ADDRESS")
        pw = self.s.get("MRX_EMAIL_PASSWORD")
        domain = (addr or "@").split("@")[-1]
        guess = {"gmail.com": ("imap.gmail.com", "smtp.gmail.com"), "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
                 "hotmail.com": ("outlook.office365.com", "smtp.office365.com"), "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
                 "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com")}.get(domain, (None, None))
        return {"addr": addr, "pw": pw, "imap": self.s.get("MRX_IMAP_HOST") or guess[0],
                "smtp": self.s.get("MRX_SMTP_HOST") or guess[1], "smtp_port": int(self.s.get("MRX_SMTP_PORT") or 465)}

    def configured(self):
        c = self.cfg()
        missing = [k for k, n in (("addr", "MRX_EMAIL_ADDRESS"), ("pw", "MRX_EMAIL_PASSWORD"), ("imap", "MRX_IMAP_HOST"), ("smtp", "MRX_SMTP_HOST")) if not c[k]]
        return (not missing, "" if not missing else "email is not configured (set " + ", ".join(
            n for k, n in (("addr", "MRX_EMAIL_ADDRESS"), ("pw", "MRX_EMAIL_PASSWORD (app password)"), ("imap", "MRX_IMAP_HOST"), ("smtp", "MRX_SMTP_HOST")) if k in missing) + ")")

    def _imap(self):
        c = self.cfg()
        m = imaplib.IMAP4_SSL(c["imap"], timeout=20)
        m.login(c["addr"], c["pw"])
        m.select("INBOX")
        return m

    def _summ(self, m, uid: bytes) -> dict:
        _, d = m.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])")
        raw = next(x[1] for x in d if isinstance(x, tuple))
        msg = emaillib.message_from_bytes(raw)
        flags = d[-1].decode() if isinstance(d[-1], bytes) else str(d[0][0])
        try:
            date = parsedate_to_datetime(msg["Date"]).isoformat()
        except Exception:
            date = msg["Date"]
        return {"id": uid.decode(), "from": _dh(msg["From"]), "to": _dh(msg["To"]), "subject": _dh(msg["Subject"]),
                "date": date, "unread": "\\Seen" not in flags}

    def _run(self, criteria: list, limit: int) -> list[dict]:
        m = self._imap()
        try:
            _, data = m.uid("search", None, *criteria)
            uids = data[0].split()[-limit:][::-1]
            return [self._summ(m, u) for u in uids]
        finally:
            m.logout()

    def list(self, limit, unread_only):
        return self._run(["UNSEEN"] if unread_only else ["ALL"], limit)

    def search(self, query, limit):
        return self._run(["OR", "OR", "FROM", f'"{query}"', "SUBJECT", f'"{query}"', "BODY", f'"{query}"'], limit)

    def get(self, uid):
        m = self._imap()
        try:
            _, d = m.uid("fetch", uid.encode(), "(BODY.PEEK[])")
            raw = next((x[1] for x in d if isinstance(x, tuple)), None)
            if not raw:
                raise ToolError(f"email {uid} not found")
            msg = emaillib.message_from_bytes(raw)
            body, atts = "", []
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    atts.append({"filename": _dh(part.get_filename()), "type": part.get_content_type(), "size": len(part.get_payload(decode=True) or b"")})
                elif part.get_content_type() == "text/plain" and not body:
                    body = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", "replace")
            return {"id": uid, "from": _dh(msg["From"]), "to": _dh(msg["To"]), "subject": _dh(msg["Subject"]),
                    "date": msg["Date"], "message_id": msg["Message-ID"], "body": body[:20000], "attachments": atts}
        finally:
            m.logout()

    def attachments(self, uid: str, dest: Path) -> list[Path]:
        m = self._imap()
        try:
            _, d = m.uid("fetch", uid.encode(), "(BODY.PEEK[])")
            msg = emaillib.message_from_bytes(next(x[1] for x in d if isinstance(x, tuple)))
            out = []
            dest.mkdir(parents=True, exist_ok=True)
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    name = Path(_dh(part.get_filename()) or "attachment").name  # strip any path components
                    p = dest / name
                    p.write_bytes(part.get_payload(decode=True) or b"")
                    out.append(p)
            return out
        finally:
            m.logout()

    def send(self, msg):
        c = self.cfg()
        msg["From"] = c["addr"]
        with smtplib.SMTP_SSL(c["smtp"], c["smtp_port"], timeout=30) as s:
            s.login(c["addr"], c["pw"])
            return s.send_message(msg)  # dict of refused recipients; empty = accepted for all

    def mark(self, uid, read):
        m = self._imap()
        try:
            typ, _ = m.uid("store", uid.encode(), "+FLAGS" if read else "-FLAGS", "(\\Seen)")
            return typ == "OK"
        finally:
            m.logout()


def _prov(rt) -> ImapSmtpProvider:
    return rt.email


def _ok(rt):
    return _prov(rt).configured()


async def list_emails(rt, limit: int = 10, unread_only: bool = False):
    return {"emails": await asyncio.to_thread(_prov(rt).list, limit, unread_only), "provider": "imap"}


async def search_email(rt, query: str, limit: int = 10):
    return {"emails": await asyncio.to_thread(_prov(rt).search, query, limit), "query": query}


async def read_email(rt, email_id: str):
    return await asyncio.to_thread(_prov(rt).get, email_id)


async def mark_email(rt, email_id: str, read: bool = True):
    ok = await asyncio.to_thread(_prov(rt).mark, email_id, read)
    return Outcome({"email_id": email_id, "read": read}, ok, "IMAP STORE returned OK" if ok else "IMAP refused")


async def download_attachments(rt, email_id: str, dest: str = "Downloads"):
    from ..tools.filesystem import resolve_path
    d = resolve_path(rt, dest)
    files = await asyncio.to_thread(_prov(rt).attachments, email_id, d)
    ok = bool(files) and all(f.is_file() for f in files)
    return Outcome({"files": [str(f) for f in files]}, ok, f"{len(files)} file(s) on disk")


async def draft_email(rt, to: str, subject: str, body: str, cc: str = "", attachments: list | None = None,
                      in_reply_to: str | None = None):
    from ..tools.filesystem import resolve_path
    paths = []
    for a in attachments or []:
        p = resolve_path(rt, a)
        if not p.is_file():
            raise ToolError(f"attachment '{p}' does not exist")
        paths.append(str(p))
    payload = {"to": to, "cc": cc, "subject": subject, "body": body, "attachments": paths, "in_reply_to": in_reply_to}
    did = rt.db.execute("INSERT INTO drafts(kind,provider,payload,status,created_at) VALUES('email','imap-smtp',?,'draft',?)",
                        (json.dumps(payload), time.time()))
    await rt.bus.emit("email.draft_created", {"draft_id": did, "to": to, "subject": subject})
    row = rt.db.one("SELECT id,status FROM drafts WHERE id=?", (did,))
    return Outcome({"draft_id": did, "status": "draft", **{k: payload[k] for k in ("to", "subject")}}, row is not None,
                   "draft saved locally; NOT sent. Ask the user to review, then call send_email")


def _send_risk(args, rt) -> int:
    return 1 if rt.settings.get("email.auto_send", False) else 3


async def send_email(rt, draft_id: int):
    row = rt.db.one("SELECT * FROM drafts WHERE id=? AND kind='email'", (draft_id,))
    if not row:
        raise ToolError(f"draft {draft_id} does not exist")
    if row["status"] == "sent":
        raise ToolError("that draft was already sent")
    p = json.loads(row["payload"])
    msg = EmailMessage()
    msg["To"], msg["Subject"] = p["to"], p["subject"]
    if p.get("cc"):
        msg["Cc"] = p["cc"]
    if p.get("in_reply_to"):
        msg["In-Reply-To"] = p["in_reply_to"]
    msg.set_content(p["body"])
    for f in p.get("attachments", []):
        ct, _ = mimetypes.guess_type(f)
        mt, st = (ct or "application/octet-stream").split("/")
        msg.add_attachment(Path(f).read_bytes(), maintype=mt, subtype=st, filename=Path(f).name)
    refused = await asyncio.to_thread(_prov(rt).send, msg)
    ok = not refused
    if ok:
        rt.db.execute("UPDATE drafts SET status='sent', sent_at=? WHERE id=?", (time.time(), draft_id))
        await rt.bus.emit("email.sent", {"draft_id": draft_id, "to": p["to"]})
    return Outcome({"draft_id": draft_id, "refused": refused}, ok,
                   "SMTP server accepted the message for all recipients (delivery to the inbox is not observable)" if ok
                   else f"SMTP refused recipients: {list(refused)}")


def tools() -> list[Tool]:
    S, I = {"type": "string"}, {"type": "integer"}
    K = dict(plugin="email", scope="email", available=_ok)
    return [
        Tool("read_email", "Read one email by id (body and attachment list).", {"email_id": S}, ["email_id"], read_email, **K),
        Tool("list_emails", "List recent emails (newest first).", {"limit": I, "unread_only": {"type": "boolean"}}, [], list_emails, **K),
        Tool("search_email", "Search emails by sender/subject/body text.", {"query": S, "limit": I}, ["query"], search_email, **K),
        Tool("mark_email", "Mark an email read/unread.", {"email_id": S, "read": {"type": "boolean"}}, ["email_id"], mark_email, **K),
        Tool("download_attachments", "Download an email's attachments to a folder.", {"email_id": S, "dest": S}, ["email_id"], download_attachments, risk=2, **K),
        Tool("draft_email", "Create a draft email (also used for replies/forwards). Never sends.",
             {"to": S, "subject": S, "body": S, "cc": S, "attachments": {"type": "array", "items": S}, "in_reply_to": S},
             ["to", "subject", "body"], draft_email, plugin="email", scope="email", available=_ok),
        Tool("send_email", "Send a previously created draft. Requires user confirmation unless auto-send is enabled.",
             {"draft_id": I}, ["draft_id"], send_email, risk=3, risk_fn=_send_risk, parallel_safe=False, **K),
    ]
