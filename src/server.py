import os
import sys
from pathlib import Path

# Add project root to sys.path so both `python src/server.py` and `python -m src.server` work seamlessly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from src.config import settings
from src.db.migrations import init_db


def main():
    """Server entrypoint: Initializes database schema and runs Uvicorn."""
    print("Initializing SQLite database tables and seed tenants...")
    init_db()

    display_host = "localhost" if settings.HOST in ("0.0.0.0", "127.0.0.1") else settings.HOST
    print(f"Server URL: http://{display_host}:{settings.PORT}")
    print(f"Swagger API Docs: http://{display_host}:{settings.PORT}/docs")
    uvicorn.run(
        "src.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
    )


if __name__ == "__main__":
    main()
