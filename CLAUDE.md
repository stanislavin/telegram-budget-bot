# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

*   **Install dependencies:** `pip install -r requirements.txt`
*   **Run the bot:** `python bot.py`
*   **Run tests:** `pytest tests/`

## High-Level Code Architecture

The project is a Telegram bot designed to process messages and save them to Google Sheets.

*   **Main Entry Point:** `bot.py` is the primary execution file that initializes and starts the bot's services.
*   **Core Utilities (`util/` directory):**
    *   `telegram.py`: Manages communication with the Telegram Bot API, including message polling and handling.
    *   `openrouter.py`: Integrates with the OpenRouter API to process and transform incoming messages, likely for extracting structured data.
    *   `sheets.py`: Handles interactions with Google Sheets, responsible for writing processed data to the configured spreadsheet.
    *   `health.py`: Contains a Flask-based health check server and a "nudge pinger" to ensure the bot remains active on deployment platforms.
    *   `config.py`: (Assumed) Manages loading and providing configuration parameters from environment variables.
*   **Data Flow:** User messages from Telegram are received, processed (potentially with external API assistance from OpenRouter), and then stored in Google Sheets.
*   **Configuration:** Key parameters like Telegram bot token, Google Sheet ID, and message format are configured via environment variables.
*   **Testing:** Unit and integration tests are located in the `tests/` directory and executed using `pytest`.
