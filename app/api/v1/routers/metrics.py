from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from app.core.metrics import metrics_collector

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("", response_class=PlainTextResponse)
async def get_metrics():
    """Prometheus metrics endpoint.

    Returns metrics in Prometheus exposition format for scraping.
    """
    return metrics_collector.get_all_metrics()
