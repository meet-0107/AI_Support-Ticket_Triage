import sys
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any, Dict

# Ensure project root is in sys.path when running as a direct script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from src.db.migrations import init_db
from src.routes.ticket_routes import router as ticket_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler initializing DB tables and seed tenants on startup."""
    init_db()
    yield


def create_app() -> FastAPI:
    """Factory creating and configuring the FastAPI application."""
    app = FastAPI(
        title="AI Support Ticket Triage API",
        description="Multi-tenant backend for an AI support-ticket triage service.",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Enable CORS for web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Custom exception handler for RequestValidationError -> HTTP 400 with per-field errors
    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ):
        field_errors = []
        for err in exc.errors():
            loc = err.get("loc", [])
            # Filter out 'body' or 'query' prefixes for cleaner field identification
            field_name = ".".join(
                str(x) for x in loc if str(x) not in ("body", "query")
            )
            field_errors.append(
                {
                    "field": field_name or "body",
                    "message": err.get("msg"),
                    "type": err.get("type"),
                }
            )

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "validation_error",
                "message": "Invalid request payload or parameters",
                "detail": field_errors,
            },
        )

    # Custom exception handler for Pydantic ValidationError -> HTTP 400
    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(
        request: Request, exc: ValidationError
    ):
        field_errors = []
        for err in exc.errors():
            loc = err.get("loc", [])
            field_name = ".".join(
                str(x) for x in loc if str(x) not in ("body", "query")
            )
            field_errors.append(
                {
                    "field": field_name or "body",
                    "message": err.get("msg"),
                    "type": err.get("type"),
                }
            )

        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={
                "error": "validation_error",
                "message": "Model validation failed",
                "detail": field_errors,
            },
        )

    # Root route redirect to docs
    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse(url="/docs")

    # Health check endpoint
    @app.get("/health", tags=["Health"])
    def health_check() -> Dict[str, str]:
        return {"status": "ok"}

    # Mount ticket router
    app.include_router(ticket_router)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    from src.config import settings

    display_host = "localhost" if settings.HOST in ("0.0.0.0", "127.0.0.1") else settings.HOST
    print(f"Server URL: http://{display_host}:{settings.PORT}")
    print(f"Swagger API Docs: http://{display_host}:{settings.PORT}/docs")
    uvicorn.run(
        "src.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )
