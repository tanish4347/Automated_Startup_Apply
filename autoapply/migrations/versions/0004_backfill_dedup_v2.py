"""backfill companies, sightings and enrichment for existing jobs; rehash with dedup v2

Revision ID: 0004_backfill_dedup_v2
Revises: 0003_companies_sightings

Data-only. Uses Core SQL on explicit columns (not ORM models) so later model changes can't break
it. The parsing helpers are imported from the app, so re-running this on a fresh DB uses whatever
their logic is at that time. That is acceptable for derived fields.

Jobs whose v2 hash collides with an earlier job are merged, never deleted: the later row gets
dedup_hash=NULL, is_active=0, classification_status='MERGED' and a reject_reason naming the kept
job, its source is added to the kept job's sightings, and its still-DISCOVERED application is
CLOSED.
"""
from dataclasses import replace
from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_backfill_dedup_v2"
down_revision: Union[str, None] = "0003_companies_sightings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of job_service._FILLABLE at the time of this migration.
_FILLABLE = (
    "description_raw", "description_text", "responsibilities", "requirements",
    "preferred_qualifications", "compensation_text", "source_url", "location", "city", "country",
    "stipend_min", "stipend_max", "stipend_currency", "stipend_period", "duration_months",
    "role_family", "ats_platform", "company_info",
)


def upgrade() -> None:
    from autoapply.config import load_search_config
    from autoapply.services.dedup import compute_dedup_hash, normalize_company
    from autoapply.sources.filter import (
        classify_role_family, detect_apply_channel, detect_duration_months, detect_pay, policy_fit,
    )
    from autoapply.sources.location import normalize_location

    conn = op.get_bind()
    policy = load_search_config().location_policy
    # Raw SQL: store in SQLAlchemy's SQLite DateTime format rather than via the deprecated adapter.
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")

    rows = conn.execute(sa.text(
        "SELECT id, title, company, location, work_mode, source, source_url, application_url, "
        "description_text, compensation_text, ats_platform, pay_status, location_fit, discovered_at "
        "FROM jobs ORDER BY id"
    )).mappings().all()

    # 1. Companies, by normalized name.
    company_ids: dict[str, int] = {
        r.normalized_name: r.id
        for r in conn.execute(sa.text("SELECT id, normalized_name FROM companies")).mappings()
    }
    for r in rows:
        norm = normalize_company(r["company"])
        if norm and norm not in company_ids:
            company_ids[norm] = conn.execute(sa.text(
                "INSERT INTO companies (name, normalized_name, ats_type, is_india, priority, "
                "discovered_via, created_at, updated_at) "
                "VALUES (:name, :norm, 'unknown', 0, 1, :via, :now, :now) RETURNING id"
            ), {"name": r["company"].strip(), "norm": norm, "via": r["source"], "now": now}).scalar_one()

    # 2. Derived fields + one sighting per existing row. Clear hashes first so step 3 can't
    #    trip the unique index mid-way.
    conn.execute(sa.text("UPDATE jobs SET dedup_hash = NULL"))
    for r in rows:
        mode = (r["work_mode"] or "UNKNOWN").lower()
        loc = normalize_location(r["location"], r["title"])
        if mode != "unknown":
            loc = replace(loc, work_mode=mode)
        text = f"{r['title'] or ''} {r['description_text'] or ''} {r['compensation_text'] or ''}"
        pay = detect_pay(text)
        conn.execute(sa.text(
            "UPDATE jobs SET company_id = :cid, city = :city, country = :country, "
            "location_fit = COALESCE(location_fit, :fit), role_family = :family, "
            "duration_months = :duration, apply_channel = :channel, "
            "stipend_min = :smin, stipend_max = :smax, stipend_currency = :scur, stipend_period = :sper, "
            "pay_status = CASE WHEN pay_status IS NULL OR pay_status = 'UNKNOWN' THEN :pay ELSE pay_status END "
            "WHERE id = :id"
        ), {
            "id": r["id"], "cid": company_ids.get(normalize_company(r["company"])),
            "city": loc.city, "country": loc.country, "fit": policy_fit(loc, policy),
            "family": classify_role_family(r["title"]),
            "duration": detect_duration_months(f"{r['title'] or ''}\n{r['description_text'] or ''}"),
            "channel": detect_apply_channel(r["source"], r["ats_platform"], r["application_url"]),
            "smin": pay["stipend_min"], "smax": pay["stipend_max"],
            "scur": pay["stipend_currency"], "sper": pay["stipend_period"],
            "pay": pay["pay_status"],
        })
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO job_sightings (job_id, source, source_url, first_seen_at, seen_at) "
            "VALUES (:id, :source, :url, :seen, :seen)"
        ), {"id": r["id"], "source": r["source"], "url": r["source_url"] or r["application_url"] or "",
            "seen": r["discovered_at"] or now})

    # 3. Rehash with dedup v2; merge collisions into the earliest row.
    keeper: dict[str, int] = {}
    for r in rows:
        mode = (r["work_mode"] or "UNKNOWN").lower()
        h = compute_dedup_hash(r["title"], r["company"], r["location"], mode)
        if h not in keeper:
            keeper[h] = r["id"]
            conn.execute(sa.text("UPDATE jobs SET dedup_hash = :h WHERE id = :id"), {"h": h, "id": r["id"]})
            continue
        kept = keeper[h]
        # Same rule as live ingest (_merge_into): fill what the kept row lacks, never overwrite.
        conn.execute(sa.text(
            "UPDATE jobs SET " + ", ".join(
                f"{c} = COALESCE({c}, (SELECT {c} FROM jobs WHERE id = :dup))" for c in _FILLABLE
            ) + " WHERE id = :kept"
        ), {"kept": kept, "dup": r["id"]})
        conn.execute(sa.text(
            "UPDATE jobs SET pay_status = (SELECT pay_status FROM jobs WHERE id = :dup), "
            "pay_evidence = (SELECT pay_evidence FROM jobs WHERE id = :dup) "
            "WHERE id = :kept AND pay_status = 'UNKNOWN'"
        ), {"kept": kept, "dup": r["id"]})
        conn.execute(sa.text(
            "UPDATE jobs SET apply_channel = 'ats_direct', "
            "application_url = (SELECT application_url FROM jobs WHERE id = :dup), "
            "ats_platform = COALESCE((SELECT ats_platform FROM jobs WHERE id = :dup), ats_platform) "
            "WHERE id = :kept AND COALESCE(apply_channel, '') != 'ats_direct' "
            "AND (SELECT apply_channel FROM jobs WHERE id = :dup) = 'ats_direct'"
        ), {"kept": kept, "dup": r["id"]})
        conn.execute(sa.text(
            "UPDATE jobs SET is_active = 0, classification_status = 'MERGED', "
            "reject_reason = :why WHERE id = :id"
        ), {"why": f"Merged into job #{kept} (dedup v2)", "id": r["id"]})
        conn.execute(sa.text(
            "INSERT OR IGNORE INTO job_sightings (job_id, source, source_url, first_seen_at, seen_at) "
            "VALUES (:kept, :source, :url, :seen, :seen)"
        ), {"kept": kept, "source": r["source"], "url": r["source_url"] or r["application_url"] or "",
            "seen": r["discovered_at"] or now})
        conn.execute(sa.text(
            "UPDATE applications SET status = 'CLOSED', error_message = :why "
            "WHERE job_id = :id AND status = 'DISCOVERED'"
        ), {"why": f"Job merged into #{kept} (dedup v2)", "id": r["id"]})


def downgrade() -> None:
    # Data backfill: the schema downgrade in 0003 drops the new columns and tables.
    # Merged rows stay inactive, and their dedup_hash values are not restored.
    pass
