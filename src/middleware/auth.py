from typing import Optional
from fastapi import Header, HTTPException, status
from src.db.database import get_db


def get_tenant_by_api_key(api_key: Optional[str], db_path: Optional[str] = None) -> str:
    """Validates an API key against the tenants table and returns tenant ID.

    Raises HTTPException(401) if missing or invalid.
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    cleaned_key = api_key.strip()
    with get_db(db_path) as conn:
        cursor = conn.execute(
            "SELECT id FROM tenants WHERE api_key = ?",
            (cleaned_key,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
                headers={"WWW-Authenticate": "ApiKey"},
            )
        return str(row["id"])


async def get_current_tenant(
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
) -> str:
    """FastAPI dependency that extracts and validates the tenant via the x-api-key header.

    Returns the tenant ID string upon success, or raises HTTP 401.
    """
    return get_tenant_by_api_key(x_api_key)
