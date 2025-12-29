import asyncio
import logging
import functools
from typing import Type, Tuple, Optional, Callable, Any

logger = logging.getLogger(__name__)

def with_retry(
    max_retries: int = 1,
    delay_seconds: int = 10,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    error_message: str = "Operation failed"
):
    """
    Decorator to retry an async function upon exception.
    
    Args:
        max_retries: Number of times to retry.
        delay_seconds: Seconds to wait between retries.
        exceptions: Tuple of exceptions to catch and retry on.
        error_message: Prefix for the error message in logs/returns.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Tuple[Any, Optional[str]]:
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    result = await func(*args, **kwargs)
                    return result, None
                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        logger.warning(
                            f"{error_message}: {str(e)}. "
                            f"Retrying in {delay_seconds} seconds... (Attempt {attempt + 1}/{max_retries})"
                        )
                        await asyncio.sleep(delay_seconds)
                    else:
                        logger.error(f"{error_message} after {max_retries} retries: {str(e)}")
            
            return None, f"{error_message}: {str(last_exception)}"
            
        return wrapper
    return decorator