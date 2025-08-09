import logging
import requests
import time
from flask import Flask
from threading import Thread
from requests.exceptions import Timeout, RequestException

from util.config import SERVICE_URL, HEALTH_CHECK_PORT, HEALTH_CHECK_HOST

logger = logging.getLogger(__name__)

def start_health_check():
    """Start the Flask server for health checks."""
    app = Flask(__name__)
    
    @app.route('/health')
    def health_check():
        return 'OK', 200
    
    @app.route('/nudge')
    def nudge():
        """Endpoint to keep the service alive."""
        return 'OK', 200
    
    def run_flask():
        app.run(host=HEALTH_CHECK_HOST, port=HEALTH_CHECK_PORT)
    
    # Start Flask in a separate thread
    flask_thread = Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    logger.info("Health check server started")

def nudge_pinger():
    """Run the nudge pinger with proper error handling and timeout."""
    nudge_url = f"{SERVICE_URL}/nudge"
    logger.info(f"Starting nudge pinger for {nudge_url}...")
    
    consecutive_failures = 0
    max_consecutive_failures = 3
    
    while True:
        try:
            # Add timeout to prevent hanging
            response = requests.get(nudge_url, timeout=10)
            if response.status_code == 200:
                logger.info(f"Successfully pinged {nudge_url}")
                consecutive_failures = 0  # Reset failure counter on success
            else:
                logger.error(f"Failed to ping {nudge_url}: {response.status_code}")
                consecutive_failures += 1
        except Timeout:
            logger.error(f"Timeout while pinging {nudge_url}")
            consecutive_failures += 1
        except RequestException as e:
            logger.error(f"Request error pinging {nudge_url}: {str(e)}")
            consecutive_failures += 1
        except Exception as e:
            logger.error(f"Unexpected error pinging {nudge_url}: {str(e)}")
            consecutive_failures += 1
        
        # If we have too many consecutive failures, log a warning
        if consecutive_failures >= max_consecutive_failures:
            logger.warning(f"Multiple consecutive failures ({consecutive_failures}) pinging {nudge_url}")
        
        time.sleep(60)  # Sleep for 1 minute

def start_nudge():
    """Run nudge pinger in a separate thread with monitoring and restart capability."""
    def monitor_and_restart():
        while True:
            nudge_thread = Thread(target=nudge_pinger)
            nudge_thread.daemon = True
            nudge_thread.start()
            
            # Wait for the thread to complete (it shouldn't unless there's an error)
            nudge_thread.join()
            
            logger.warning("Nudge pinger thread died, restarting...")
            time.sleep(5)  # Wait a bit before restarting
    
    monitor_thread = Thread(target=monitor_and_restart)
    monitor_thread.daemon = True
    monitor_thread.start()
    logger.info("Nudge pinger monitor started") 