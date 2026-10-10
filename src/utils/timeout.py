import asyncio
from typing import Any, Coroutine, TypeVar

T = TypeVar("T")


async def async_timeout(coro: Coroutine[Any, Any, T], timeout_sec: float) -> T:
    """Wraps an asyncio coroutine with a timeout limit.
    
    Args:
        coro: The async coroutine to await.
        timeout_sec: Timeout duration in seconds.
        
    Returns:
        The result of the coroutine if completed within timeout_sec.
        
    Raises:
        asyncio.TimeoutError: If execution exceeds timeout_sec.
    """
    return await asyncio.wait_for(coro, timeout=timeout_sec)
