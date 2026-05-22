import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.jaeger.thrift import JaegerExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource

logger = logging.getLogger("clinic.telemetry")


def setup_telemetry(app=None, service_name="clinic-scheduler"):
    resource = Resource.create({"service.name": service_name})

    tracer_provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(tracer_provider)

    jaeger_exporter = JaegerExporter(
        agent_host_name="jaeger",
        agent_port=6831,
    )

    span_processor = BatchSpanProcessor(jaeger_exporter)
    tracer_provider.add_span_processor(span_processor)

    if app:
        FastAPIInstrumentor.instrument_app(app)
        try:
            from app.db.session import engine

            SQLAlchemyInstrumentor().instrument(
                engine=engine.sync_engine,
                service="clinic-scheduler-db",
            )
        except Exception as e:
            logger.warning("Could not instrument SQLAlchemy: %s", e)

    logger.info("OpenTelemetry initialized for %s", service_name)
    return tracer_provider
