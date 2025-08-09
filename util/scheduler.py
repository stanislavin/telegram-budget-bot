import asyncio
import logging
import os
from datetime import datetime, time
from typing import Optional
import pytz
from telegram.ext import ContextTypes

from util.sheets import get_daily_summary
from util.config import TELEGRAM_BOT_TOKEN

logger = logging.getLogger(__name__)

class DailySummaryScheduler:
    def __init__(self, chat_id: str, timezone_str: str = "UTC"):
        self.chat_id = chat_id
        self.timezone = pytz.timezone(timezone_str)
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
    
    async def send_daily_summary(self, context: ContextTypes.DEFAULT_TYPE):
        """Send daily summary to the user."""
        try:
            summary_text, chart_path = await get_daily_summary()
            
            # Send the chart image if it was generated successfully
            if chart_path and os.path.exists(chart_path):
                # Send the chart image with the summary text as caption
                await context.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=open(chart_path, 'rb'),
                    caption=f"🕐 Daily Summary (17:00):\n\n{summary_text}"
                )
                # Clean up the temporary chart file
                os.remove(chart_path)
            else:
                # Send only the text if chart generation failed
                await context.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"🕐 Daily Summary (17:00):\n\n{summary_text}"
                )
            
            logger.info(f"Daily summary sent to chat {self.chat_id}")
        except Exception as e:
            logger.error(f"Failed to send daily summary to chat {self.chat_id}: {str(e)}")
    
    async def schedule_loop(self, context: ContextTypes.DEFAULT_TYPE):
        """Main scheduling loop that runs daily at 17:00."""
        while self.is_running:
            try:
                # Get current time in the specified timezone
                now = datetime.now(self.timezone)
                target_time = time(17, 0)  # 17:00
                
                # Calculate next 17:00
                next_run = now.replace(hour=17, minute=0, second=0, microsecond=0)
                
                # If we've already passed 17:00 today, schedule for tomorrow
                if now.time() >= target_time:
                    next_run = next_run.replace(day=next_run.day + 1)
                
                # Calculate seconds until next run
                sleep_seconds = (next_run - now).total_seconds()
                
                logger.info(f"Next daily summary scheduled for {next_run} (in {sleep_seconds:.0f} seconds)")
                
                # Sleep until the target time
                await asyncio.sleep(sleep_seconds)
                
                # Send the daily summary
                if self.is_running:  # Check if still running after sleep
                    await self.send_daily_summary(context)
                
            except Exception as e:
                logger.error(f"Error in schedule loop: {str(e)}")
                # Sleep for 1 hour before retrying
                await asyncio.sleep(3600)
    
    def start(self, context: ContextTypes.DEFAULT_TYPE):
        """Start the daily summary scheduler."""
        if not self.is_running:
            self.is_running = True
            self.task = asyncio.create_task(self.schedule_loop(context))
            logger.info(f"Daily summary scheduler started for chat {self.chat_id}")
    
    def stop(self):
        """Stop the daily summary scheduler."""
        self.is_running = False
        if self.task and not self.task.done():
            self.task.cancel()
            logger.info(f"Daily summary scheduler stopped for chat {self.chat_id}")

# Global scheduler instance
_scheduler: Optional[DailySummaryScheduler] = None

def start_daily_summary_scheduler(chat_id: str, context: ContextTypes.DEFAULT_TYPE, timezone_str: str = "UTC"):
    """Start the daily summary scheduler for a specific chat."""
    global _scheduler
    
    # Stop existing scheduler if running
    if _scheduler:
        _scheduler.stop()
    
    # Create and start new scheduler
    _scheduler = DailySummaryScheduler(chat_id, timezone_str)
    _scheduler.start(context)
    
    logger.info(f"Daily summary scheduler initialized for chat {chat_id} with timezone {timezone_str}")

def stop_daily_summary_scheduler():
    """Stop the daily summary scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.stop()
        _scheduler = None