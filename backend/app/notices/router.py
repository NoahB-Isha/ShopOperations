"""Admin notices — the top-bar inbox.

Admins post announcements; every signed-in user reads them. Opening the
inbox marks everything read (per user). This is a bulletin board only: no
emails, no WhatsApp, no outbox rows.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth.deps import AuthedUser, get_current_user, require_roles
from ..db import get_db
from ..models import Notice, NoticeRead, Role, User, utcnow

router = APIRouter(prefix="/notices", tags=["notices"])

MAX_NOTICES = 50  # the inbox shows the recent window, not an archive


class NoticeOut(BaseModel):
    id: int
    title: str
    body: str
    author: str
    created_at: datetime
    read: bool


class InboxOut(BaseModel):
    unread: int
    items: list[NoticeOut]


class NoticeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=5000)


def _inbox(db: Session, authed: AuthedUser) -> InboxOut:
    notices = db.scalars(
        select(Notice).order_by(Notice.id.desc()).limit(MAX_NOTICES)
    ).all()
    read_ids = {
        nid
        for (nid,) in db.execute(
            select(NoticeRead.notice_id).where(NoticeRead.user_id == authed.id)
        )
    }
    authors = {
        u.id: (u.display_name or u.email or "")
        for u in db.scalars(
            select(User).where(
                User.id.in_({n.created_by_id for n in notices if n.created_by_id} or {-1})
            )
        )
    }
    items = [
        NoticeOut(
            id=n.id,
            title=n.title,
            body=n.body,
            author=authors.get(n.created_by_id or -1, ""),
            created_at=n.created_at,
            read=n.id in read_ids,
        )
        for n in notices
    ]
    return InboxOut(unread=sum(1 for i in items if not i.read), items=items)


@router.get("", response_model=InboxOut)
def list_notices(
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> InboxOut:
    return _inbox(db, authed)


@router.post("/read", response_model=InboxOut)
def mark_all_read(
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(get_current_user),
) -> InboxOut:
    """Opening the inbox marks everything currently in it as read."""
    unread = db.scalars(
        select(Notice)
        .where(
            ~Notice.id.in_(
                select(NoticeRead.notice_id).where(NoticeRead.user_id == authed.id)
            )
        )
        .order_by(Notice.id.desc())
        .limit(MAX_NOTICES)
    ).all()
    for n in unread:
        db.add(NoticeRead(notice_id=n.id, user_id=authed.id, read_at=utcnow()))
    db.commit()
    return _inbox(db, authed)


@router.post("", response_model=NoticeOut, status_code=201)
def post_notice(
    body: NoticeIn,
    db: Session = Depends(get_db),
    authed: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> NoticeOut:
    notice = Notice(
        title=body.title.strip(),
        body=body.body.strip(),
        created_by_id=authed.id,
    )
    db.add(notice)
    # the author has obviously read their own notice
    db.flush()
    db.add(NoticeRead(notice_id=notice.id, user_id=authed.id, read_at=utcnow()))
    db.commit()
    db.refresh(notice)
    return NoticeOut(
        id=notice.id,
        title=notice.title,
        body=notice.body,
        author=authed.user.display_name or authed.user.email or "",
        created_at=notice.created_at,
        read=True,
    )


@router.delete("/{notice_id}", status_code=204)
def delete_notice(
    notice_id: int,
    db: Session = Depends(get_db),
    _: AuthedUser = Depends(require_roles(Role.ADMIN)),
) -> None:
    notice = db.get(Notice, notice_id)
    if notice is None:
        raise HTTPException(404, "Notice not found.")
    for r in db.scalars(select(NoticeRead).where(NoticeRead.notice_id == notice_id)):
        db.delete(r)
    db.delete(notice)
    db.commit()
