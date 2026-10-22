"""Prometheus HTTP instrumentation with bounded route labels."""

from time import perf_counter

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

REQUESTS = Counter(
    "medical_http_requests_total",
    "HTTP requests handled by the medical RAG platform",
    ("service", "method", "route", "status"),
)
LATENCY = Histogram(
    "medical_http_request_duration_seconds",
    "HTTP request latency",
    ("service", "method", "route"),
)
IN_PROGRESS = Gauge(
    "medical_http_requests_in_progress",
    "HTTP requests currently in progress",
    ("service",),
)


def metrics_app():
    return make_asgi_app()


def instrument(service: str):
    async def middleware(request, call_next):
        started = perf_counter()
        IN_PROGRESS.labels(service).inc()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            route_name = getattr(route, "path", request.url.path)
            REQUESTS.labels(service, request.method, route_name, str(status)).inc()
            LATENCY.labels(service, request.method, route_name).observe(perf_counter() - started)
            IN_PROGRESS.labels(service).dec()

    return middleware

