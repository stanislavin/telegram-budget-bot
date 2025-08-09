# Telegram Budget Bot - Project Context

## Project Overview

This is a Telegram bot designed to help users track their expenses. The bot processes messages sent by users, extracts expense details (amount, currency, category, description) using an AI model (OpenRouter API), and saves this data to a Google Sheet. It also provides features like daily spending summaries and expense history.

### Core Technologies
- **Python**: Main programming language
- **python-telegram-bot**: For Telegram bot integration
- **Google Sheets API**: For data storage
- **OpenRouter API**: For processing expense descriptions with AI
- **Flask**: For health check endpoints
- **dotenv**: For environment configuration

### Architecture
1. **Telegram Bot Core**:
   - Handles user messages and interactions
   - Uses OpenRouter API for expense processing
   - Provides interactive UI with buttons for confirming expenses and viewing summaries

2. **Health Monitoring**:
   - Flask server for health checks (`/health` endpoint)
   - Nudge pinger to keep the service alive by periodically pinging `/nudge` endpoint

3. **Data Storage**:
   - Google Sheets integration for storing expense data with timestamps

4. **Configuration**:
   - Uses `.env` file for configuration (API keys, credentials, etc.)

## Building and Running

### Prerequisites
1. Python 3.7+
2. Google Cloud project with Sheets API enabled
3. Telegram Bot Token
4. OpenRouter API Key

### Setup
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Set up Google Sheets API:
   - Go to [Google Cloud Console](https://console.cloud.google.com/)
   - Create a new project
   - Enable Google Sheets API
   - Create service account credentials
   - Download the credentials JSON file and save it as `credentials.json` in the project root
   - Share your Google Sheet with the service account email
3. Configure environment variables:
   - Copy `.env.example` to `.env`
   - Fill in your Telegram Bot Token, Google Sheet ID, and OpenRouter API Key
4. Create a `prompt.txt` file with the AI processing prompt (see existing file for format)

### Running the Bot
```bash
python bot.py
```

### Deployment
This project includes a `Procfile` for deployment on platforms like Heroku:
```
web: python bot.py
```

## Development Conventions

### Code Structure
- `bot.py`: Main entry point
- `util/`: Contains utility modules:
  - `config.py`: Configuration loading
  - `telegram.py`: Telegram bot implementation
  - `sheets.py`: Google Sheets integration
  - `openrouter.py`: AI processing with OpenRouter
  - `health.py`: Health check server and nudge pinger
- `prompt.txt`: AI prompt for expense processing
- `requirements.txt`: Python dependencies
- `.env`: Environment variables (not committed to version control)

### Testing
- Tests are located in the `tests/` directory
- Uses `pytest` for testing
- Test dependencies are in `requirements-test.txt`

### Configuration
- All configuration is managed through environment variables
- `.env.example` provides a template for required variables
- Never commit actual credentials to version control

### Error Handling
- Extensive error handling and logging throughout the application
- Graceful degradation when external services fail
- Clear error messages to users when processing fails

### Logging
- Uses Python's built-in `logging` module
- Configured in `util/config.py`
- Logs important events and errors for debugging