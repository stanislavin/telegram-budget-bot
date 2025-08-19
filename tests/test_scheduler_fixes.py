import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio

from util.scheduler import DailySummaryScheduler


@pytest.mark.asyncio
async def test_daily_summary_scheduler_multiple_chats():
    """Test DailySummaryScheduler can handle multiple chats."""
    scheduler = DailySummaryScheduler()
    
    assert len(scheduler.chat_schedulers) == 0
    assert scheduler.is_running is False
    assert scheduler.task is None
    
    # Add multiple chats
    scheduler.add_chat("12345", "UTC")
    scheduler.add_chat("67890", "America/New_York")
    
    assert len(scheduler.chat_schedulers) == 2
    assert "12345" in scheduler.chat_schedulers
    assert "67890" in scheduler.chat_schedulers