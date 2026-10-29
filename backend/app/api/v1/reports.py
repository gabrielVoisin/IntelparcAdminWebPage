from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import check_site_access, get_site_or_404, require_permission, require_role
from app.i18n import negotiate_locale, translate
from app.models import ReportExport, StaffUser
from app.schemas import ReportExportOut, ReportType
from app.services import queries, reports, settings_store
from app.services.audit import audit

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("")
def list_reports(request: Request, user: StaffUser = Depends(require_role("viewer"))) -> list[dict]:
    locale = negotiate_locale(request, user.locale)
    return [
        {"type": key, "title": translate(f"report.{key}.title", locale), "description": translate(f"report.{key}.desc", locale)}
        for key in reports.REPORTS
    ]


@router.get("/exports", response_model=list[ReportExportOut])
def recent_exports(user: StaffUser = Depends(require_role("viewer")), db: Session = Depends(get_db)) -> list[dict]:
    return queries.recent_exports(db, limit=20)


@router.get("/{report_type}")
def download(
    report_type: ReportType,
    request: Request,
    format: Literal["pdf", "xlsx"] = "pdf",
    site_id: uuid.UUID | None = Query(None, description="Empty = every site"),
    period: Literal["day", "week", "month", "last_month", "year", "custom"] = "month",
    date_from: date | None = None,
    date_to: date | None = None,
    user: StaffUser = Depends(require_permission("download_reports")),
    db: Session = Depends(get_db),
) -> Response:
    """Generate the report and return the file (Content-Disposition: attachment)."""
    locale = negotiate_locale(request, user.locale)
    if site_id is not None:
        site_name = get_site_or_404(db, site_id, user).name
    else:
        site_name = translate("reports.all_sites", locale)
    if period == "custom":
        if not date_from or not date_to or date_to < date_from:
            raise HTTPException(422, "invalid_period")
        start, end = date_from, date_to
    else:
        start, end = queries.period_bounds(period)
    if site_id:
        check_site_access(user, site_id)
    anonymise = bool(settings_store.get(db, "gdpr.anonymise_exports"))
    content, media_type = reports.build(db, report_type, format, site_id, site_name, start, end, locale, anonymise)
    db.add(ReportExport(report_type=report_type, site_id=site_id, period_from=start, period_to=end, format=format,
                        file_size=len(content), created_by=user.id))
    audit(db, "report.download", "report_exports", None, actor_id=user.id, data={"type": report_type, "format": format})
    filename = f"{report_type}_{start:%Y%m%d}-{end:%Y%m%d}.{format}"
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
