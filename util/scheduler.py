import asyncio
import logging
import os
from datetime import datetime, time
from typing import Optional, Dict
import pytz
from telegram.ext import ContextTypes

from util.sheets import get_daily_summary
from util.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

class DailySummaryScheduler:
    def __init__(self):
        self.chat_schedulers: Dict[str, ChatScheduler] = {}
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
    
    async def send_daily_summary_to_all(self, context: ContextTypes.DEFAULT_TYPE):
        """Send daily summary to all registered chats."""
        logger.info(f"Sending daily summary to {len(self.chat_schedulers)} chats")
        for chat_id, chat_scheduler in self.chat_schedulers.items():
            try:
                logger.info(f"Generating daily summary for chat {chat_id}")
                summary_text, chart_path = await get_daily_summary()
                logger.info(f"Generated summary for chat {chat_id}: {summary_text[:100]}...")
                
                # Send the chart image if it was generated successfully
                if chart_path and os.path.exists(chart_path):
                    # Send the chart image with the summary text as caption
                    await context.bot.send_photo(
                        chat_id=chat_id,
                        photo=open(chart_path, 'rb'),
                        caption=f"🕐 Daily Summary (17:00):\n\n{summary_text}"
                    )
                    # Clean up the temporary chart file
                    os.remove(chart_path)
                    logger.info(f"Sent summary with chart to chat {chat_id}")
                else:
                    # Send only the text if chart generation failed
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=f"🕐 Daily Summary (17:00):\n\n{summary_text}"
                    )
                    logger.info(f"Sent summary text to chat {chat_id}")
                
                logger.info(f"Daily summary sent to chat {chat_id}")
            except Exception as e:
                logger.error(f"Failed to send daily summary to chat {chat_id}: {str(e)}", exc_info=True)
    
    async def schedule_loop(self, context: ContextTypes.DEFAULT_TYPE):
        """Main scheduling loop that runs daily at 17:00 UTC."""
        logger.info("Daily summary scheduler loop started")
        while self.is_running:
            try:
                # Get current time in UTC
                now = datetime.now(pytz.UTC)
                target_time = time(17, 0)  # 17:00
                
                # Calculate next 17:00 UTC
                next_run = now.replace(hour=17, minute=0, second=0, microsecond=0)
                
                # If we've already passed 17:00 today, schedule for tomorrow
                if now.time() >= target_time:
                    next_run = next_run.replace(day=next_run.day + 1)
                
                # Calculate seconds until next run
                sleep_seconds = (next_run - now).total_seconds()
                
                logger.info(f"Next daily summary scheduled for {next_run} UTC (in {sleep_seconds:.0f} seconds)")
                
                # Sleep until the target time
                if sleep_seconds > 0:
                    await asyncio.sleep(min(sleep_seconds, 3600))  # Sleep in smaller chunks for better responsiveness
                else:
                    # Send the daily summary immediately
                    if self.is_running:  # Check if still running
                        await self.send_daily_summary_to_all(context)
                    
                    # Schedule for next day
                    next_run = next_run.replace(day=next_run.day + 1)
                    sleep_seconds = (next_run - datetime.now(pytz.UTC)).total_seconds()
                    await asyncio.sleep(max(0, sleep_seconds))
                
            except Exception as e:
                logger.error(f"Error in schedule loop: {str(e)}", exc_info=True)
                # Sleep for 1 hour before retrying
                await asyncio.sleep(3600)
    
    def add_chat(self, chat_id: str, timezone_str: str = "UTC"):
        """Add a chat to receive daily summaries."""
        self.chat_schedulers[chat_id] = ChatScheduler(chat_id, timezone_str)
        logger.info(f"Added chat {chat_id} to daily summary scheduler")
    
    def remove_chat(self, chat_id: str):
        """Remove a chat from receiving daily summaries."""
        if chat_id in self.chat_schedulers:
            del self.chat_schedulers[chat_id]
            logger.info(f"Removed chat {chat_id} from daily summary scheduler")
    
    def start(self, context: ContextTypes.DEFAULT_TYPE):
        """Start the daily summary scheduler."""
        if not self.is_running:
            self.is_running = True
            self.task = asyncio.create_task(self.schedule_loop(context))
            logger.info("Daily summary scheduler started")
    
    def stop(self):
        """Stop the daily summary scheduler."""
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info("Daily summary scheduler stopped")

class ChatScheduler:
    def __init__(self, chat_id: str, timezone_str: str = "UTC"):
        self.chat_id = chat_id
        self.timezone = pytz.timezone(timezone_str)

# Global scheduler instance
_scheduler: Optional[DailySummaryScheduler] = None

def start_daily_summary_scheduler(chat_id: str, context: ContextTypes.DEFAULT_TYPE, timezone_str: str = "UTC"):
    """Start the daily summary scheduler and add a chat to it."""
    global _scheduler
    
    # Create scheduler if it doesn't exist
    if _scheduler is None:
        _scheduler = DailySummaryScheduler()
        logger.info("Created new daily summary scheduler")
    
    # Start the scheduler if not already running
    if not _scheduler.is_running:
        _scheduler.start(context)
        logger.info("Started daily summary scheduler task")
    
    # Add the chat to the scheduler
    _scheduler.add_chat(chat_id, timezone_str)
    
    logger.info(f"Chat {chat_id} added to daily summary scheduler with timezone {timezone_str}")

def stop_daily_summary_scheduler():
    """Stop the daily summary scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None
        logger.info("Daily summary scheduler stopped and cleared")