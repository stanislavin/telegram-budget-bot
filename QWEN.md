# Qwen Code

This project was developed with the assistance of Qwen Code, an AI coding assistant by Tongyi Lab.

For more information, visit [Qwen Code](https://www.qwenlm.ai/).

## Project Overview

This Telegram Budget Bot is a comprehensive expense tracking solution that integrates with Google Sheets for data storage. The bot processes natural language expense descriptions using AI (OpenRouter API) to categorize and format expenses before saving them to a spreadsheet.

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