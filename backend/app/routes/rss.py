from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..database import connect
from ..db import log, now, save_settings
from ..schemas import ReorderPayload, RssSubscriptionPayload
from ..utils import row_to_dict, rows_to_dicts


router = APIRouter()


@router.get("/api/rss-subscriptions")
async def api_rss_subscriptions() -> dict:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM rss_subscriptions ORDER BY enabled DESC, priority ASC, id ASC").fetchall()
    return {"items": rows_to_dicts(rows)}


@router.post("/api/rss-subscriptions")
async def api_create_rss_subscription(payload: RssSubscriptionPayload) -> dict:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="RSS 地址不能为空")
    kind = payload.kind.strip() or "mikan"
    if kind != "mikan":
        raise HTTPException(status_code=400, detail="当前只支持 Mikan RSS")
    ts = now()
    with connect() as conn:
        priority_row = conn.execute("SELECT COALESCE(MAX(priority), 0) + 1 AS priority FROM rss_subscriptions").fetchone()
        priority = int(priority_row["priority"] or 1) if priority_row else 1
        conn.execute(
            """
            INSERT INTO rss_subscriptions (name, url, kind, enabled, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(url) DO UPDATE SET
              name=excluded.name,
              kind=excluded.kind,
              enabled=excluded.enabled,
              updated_at=excluded.updated_at
            """,
            (payload.name.strip() or "Mikan RSS", url, kind, int(payload.enabled), priority, ts, ts),
        )
        row = conn.execute("SELECT * FROM rss_subscriptions WHERE url=?", (url,)).fetchone()
    if payload.enabled:
        save_settings({"rss_url": url})
    log("info", f"RSS 订阅已保存: kind={kind} url={url}")
    return {"status": "saved", "item": row_to_dict(row)}


@router.put("/api/rss-subscriptions/{subscription_id}")
async def api_update_rss_subscription(subscription_id: int, payload: RssSubscriptionPayload) -> dict:
    url = payload.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="RSS 地址不能为空")
    kind = payload.kind.strip() or "mikan"
    if kind != "mikan":
        raise HTTPException(status_code=400, detail="当前只支持 Mikan RSS")
    ts = now()
    with connect() as conn:
        existing = conn.execute("SELECT id FROM rss_subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="RSS 订阅不存在")
        conn.execute(
            """
            UPDATE rss_subscriptions
            SET name=?, url=?, kind=?, enabled=?, updated_at=?
            WHERE id=?
            """,
            (payload.name.strip() or "Mikan RSS", url, kind, int(payload.enabled), ts, subscription_id),
        )
        row = conn.execute("SELECT * FROM rss_subscriptions WHERE id=?", (subscription_id,)).fetchone()
    if payload.enabled:
        save_settings({"rss_url": url})
    return {"status": "saved", "item": row_to_dict(row)}


@router.delete("/api/rss-subscriptions/{subscription_id}")
async def api_delete_rss_subscription(subscription_id: int) -> dict[str, str]:
    with connect() as conn:
        row = conn.execute("SELECT id FROM rss_subscriptions WHERE id=?", (subscription_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="RSS 订阅不存在")
        conn.execute("DELETE FROM rss_subscriptions WHERE id=?", (subscription_id,))
    return {"status": "deleted", "message": "RSS 订阅已删除"}


@router.post("/api/rss-subscriptions/reorder")
async def api_reorder_rss_subscriptions(payload: ReorderPayload) -> dict:
    ids = [int(item) for item in payload.ids if int(item or 0) > 0]
    ts = now()
    with connect() as conn:
        existing = {
            int(row["id"])
            for row in conn.execute("SELECT id FROM rss_subscriptions").fetchall()
        }
        for index, subscription_id in enumerate(ids):
            if subscription_id in existing:
                conn.execute(
                    "UPDATE rss_subscriptions SET priority=?, updated_at=? WHERE id=?",
                    (index + 1, ts, subscription_id),
                )
        rows = conn.execute("SELECT * FROM rss_subscriptions ORDER BY enabled DESC, priority ASC, id ASC").fetchall()
    return {"status": "saved", "items": rows_to_dicts(rows)}
