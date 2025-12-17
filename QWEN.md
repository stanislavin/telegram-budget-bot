# Qwen Code

This project was developed with the assistance of Qwen Code, an AI coding assistant by Tongyi Lab.

For more information, visit [Qwen Code](https://www.qwenlm.ai/).

**Last Updated:** Wednesday, December 17, 2025

## Project Overview

This Telegram Budget Bot is a comprehensive expense tracking solution that integrates with Google Sheets for data storage. The bot processes natural language expense descriptions using AI (OpenRouter API) to categorize and format expenses before saving them to a spreadsheet.

## Recent Maintenance Updates

- Removed unused documentation files (AGENTS.md, CLAUDE.md, GEMINI.md, PROMPT_OPTIMIZATION.md)
- Removed prompt optimization tests that were dependent on removed files
- Updated .gitignore to exclude sensitive and temporary files
- Added test coverage reporting functionality
- Fixed currency defaulting issue: now properly defaults to RSD when no currency is specified
- Fixed daily expense summary scheduler to support multiple users
- All tests are now passing (80 tests with 93% coverage)

## How to Run Tests

### Prerequisites
1. Activate the virtual environment:
   ```bash
   source .venv/bin/activate
   ```

2. Install test dependencies (if not already installed):
   ```bash
   pip install -r requirements-test.txt
   ```

### Running Tests

#### Basic Test Execution
```bash
# Run all tests
python -m pytest tests/

# Run tests with verbose output
python -m pytest tests/ -v
```

#### Test with Coverage Reporting
```bash
# Run tests with coverage reporting
python -m pytest tests/ --cov=. --cov-report=term-missing

# Generate all coverage report formats
python -m pytest tests/ --cov=. --cov-report=term-missing --cov-report=html --cov-report=xml
```

#### Using Makefile (Recommended)
```bash
# Run tests
make test

# Run tests with coverage
make coverage

# Clean coverage reports
make clean
```

#### Using the Dedicated Script
```bash
# Run tests with coverage using the script
./scripts/run_tests_with_coverage.sh
```

### Coverage Reports
- **Terminal output**: Shows immediate coverage summary with missing lines
- **HTML report**: Available at `htmlcov/index.html` for detailed browsing
- **XML report**: Available at `coverage.xml` for CI/CD integration

## Key Features Developed with Qwen Code Assistance

### 1. Telegram Bot Integration
- Implementation of message handling using python-telegram-bot library
- Interactive UI with custom keyboards and inline buttons for expense confirmation
- Command handling for /start, /help, and /summary commands

### 2. AI-Powered Expense Processing
- Integration with OpenRouter API for natural language processing
- Custom prompt engineering for Russian expense descriptions
- Automated categorization into 18 predefined categories

### 3. Google Sheets Integration
- Secure authentication using service account credentials
- Data storage with timestamped entries
- Daily spending summaries with category breakdowns
- Recent expense retrieval functionality

### 4. Health Monitoring System
- Flask server for health checks and service monitoring
- Nudge pinger to prevent service timeouts on hosting platforms
- Background threading for non-blocking operations

### 5. Data Visualization
- Dynamic pie chart generation for daily spending summaries
- Matplotlib integration for visual expense tracking

### 6. Scheduler System
- Automated daily summary delivery at 17:00 UTC
- Timezone-aware scheduling system
- Asynchronous task management

### 7. User Experience Features
- Expense confirmation flow with auto-confirmation timeout
- Category selection interface for expense re-categorization
- Interactive command keyboard for easy navigation
- Multi-language support (Russian expense descriptions)

## Technical Architecture

The bot follows a modular architecture with separate utility modules for:
- Configuration management
- Telegram bot operations
- Google Sheets integration
- OpenRouter API communication
- Health monitoring
- Scheduler system

This separation of concerns makes the codebase maintainable and allows for easy feature additions and modifications with continued Qwen Code assistance.