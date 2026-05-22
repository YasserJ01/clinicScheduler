import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

import httpx
from sqlalchemy import select

from app.db.session import async_session_factory
from app.models import Webhook, WebhookDelivery

logger = logging.getLogger("clinic.webhooks")

RETRY_DELAYS = [1, 5, 25]


def sign_payload(secret: str, payload: str) -> str:
    signature = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"sha256={signature}"


async def _deliver(
    webhook: Webhook,
    event_type: str,
    data: dict[str, Any],
) -> None:
    async with async_session_factory() as session:
        payload = json.dumps(
            {
                "event": event_type,
                "timestamp": data.get("timestamp", ""),
                "data": data,
            }
        )
        signature = sign_payload(webhook.secret, payload)

        delivery = WebhookDelivery(
            webhook_id=webhook.id,
            tenant_id=webhook.tenant_id,
            event_type=event_type,
            payload=payload,
        )

        for attempt in range(1, len(RETRY_DELAYS) + 2):
            delivery.attempt = attempt
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        webhook.url,
                        content=payload,
                        headers={
                            "Content-Type": "application/json",
                            "X-Webhook-Signature": signature,
                            "X-Webhook-Event": event_type,
                        },
                    )
                    delivery.response_status = resp.status_code
                    delivery.response_body = resp.text[:1000]
                    delivery.success = 200 <= resp.status_code < 300

                    if delivery.success:
                        logger.info(
                            "Webhook delivered: id=%s event=%s status=%s",
                            webhook.id,
                            event_type,
                            resp.status_code,
                        )
                        break
                    else:
                        logger.warning(
                            "Webhook failed (attempt %d): id=%s event=%s status=%s",
                            attempt,
                            webhook.id,
                            event_type,
                            resp.status_code,
                        )
                        if attempt <= len(RETRY_DELAYS):
                            await asyncio.sleep(RETRY_DELAYS[attempt - 1])
            except Exception as e:
                delivery.response_status = 0
                delivery.response_body = str(e)[:1000]
                delivery.success = False
                logger.error(
                    "Webhook error (attempt %d): id=%s event=%s error=%s",
                    attempt,
                    webhook.id,
                    event_type,
                    e,
                )
                if attempt <= len(RETRY_DELAYS):
                    await asyncio.sleep(RETRY_DELAYS[attempt - 1])

        session.add(delivery)
        await session.flush()


async def trigger_webhooks(
    event_type: str,
    data: dict[str, Any],
) -> None:
    async with async_session_factory() as session:
        result = await session.execute(
            select(Webhook).where(Webhook.is_active.is_(True))
        )
        webhooks = result.scalars().all()

    for webhook in webhooks:
        events = json.loads(webhook.events)
        if event_type in events:
            asyncio.create_task(_deliver(webhook, event_type, data))
