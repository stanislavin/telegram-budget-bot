"""
Global test configuration and shared fixtures for the telegram-budget-bot test suite.
"""

import pytest
import os
import tempfile
from unittest.mock import MagicMock, patch
import asyncio


# Event loop fixture removed to avoid deprecation warning - using pytest-asyncio default


@pytest.fixture(autouse=True)
def clean_pending_expenses():
    """Automatically clean up pending expenses and caches after each test."""
    yield
    # Clean up any pending expenses that might have been left by tests
    from util.telegram import pending_expenses
    pending_expenses.clear()
    # Reset daily stats cache
    import util.sheets
    util.sheets._daily_stats_cache = {}
    util.sheets._daily_stats_cache_time = 0


@pytest.fixture
def mock_environment():
    """Fixture to provide a clean test environment with mocked config values."""
    env_vars = {
        'TELEGRAM_BOT_TOKEN': 'test_bot_token_123',
        'GOOGLE_SHEET_ID': 'test_sheet_id_456',
        'OPENROUTER_API_KEY': 'test_openrouter_key_789',
        'OPENROUTER_LLM_VERSION': 'test_model_v1',
        'SERVICE_URL': 'http://test-service.localhost:8000',
        'GOOGLE_CREDENTIALS_PATH': 'test_credentials.json'
    }
    
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def temp_prompt_file():
    """Create a temporary prompt file for testing."""
    prompt_content = """You are a budget tracking assistant. Parse the following expense:
- Food (meals, groceries, restaurants)
- Transport (taxi, bus, train, gas)
- Utilities (electricity, water, internet)
- Rent (housing costs)
- Salary (income)

Format your response as: amount,currency,category,description"""
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(prompt_content)
        temp_file_path = f.name
    
    # Patch the prompt file path
    with patch('util.config.get_llm_prompt') as mock_get_prompt:
        mock_get_prompt.return_value = prompt_content
        yield temp_file_path, prompt_content
    
    # Clean up
    os.unlink(temp_file_path)


@pytest.fixture
def mock_telegram_update():
    """Create a comprehensive mock Telegram Update object."""
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = "50.00 USD food lunch"
    update.message.chat_id = 12345
    update.message.message_id = 67890
    update.message.reply_text = MagicMock()
    update.message.edit_text = MagicMock()
    
    update.callback_query = MagicMock()
    update.callback_query.data = "action:confirm|id:12345-67890"
    update.callback_query.answer = MagicMock()
    update.callback_query.edit_message_text = MagicMock()
    
    return update


@pytest.fixture
def mock_telegram_context():
    """Create a mock Telegram context object."""
    context = MagicMock()
    context.bot = MagicMock()
    return context


@pytest.fixture
def sample_expense_data():
    """Provide sample expense data for testing."""
    return {
        'amount': 25.50,
        'currency': 'USD',
        'category': 'food',
        'description': 'lunch at restaurant'
    }


@pytest.fixture
def mock_google_sheets_service():
    """Mock Google Sheets service for testing."""
    import util.sheets
    util.sheets._sheets_service = None  # Reset cache before test
    with patch('util.sheets.service_account.Credentials.from_service_account_file') as mock_creds:
        mock_creds.return_value = MagicMock()
        with patch('util.sheets.build') as mock_build:
            mock_service = MagicMock()
            mock_build.return_value = mock_service

            # Configure default successful responses
            mock_service.spreadsheets().values().append().execute.return_value = {
                'updates': {'updatedCells': 6}
            }
            mock_service.spreadsheets().values().get().execute.return_value = {
                'values': []
            }

            yield mock_service
    util.sheets._sheets_service = None  # Reset cache after test


@pytest.fixture
def mock_openrouter_success():
    """Mock successful OpenRouter API responses."""
    with patch('util.openrouter.requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '25.50,USD,food,lunch'}}]
        }
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response
        yield mock_post


@pytest.fixture
def disable_logging():
    """Disable logging during tests to reduce noise."""
    import logging
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


# Pytest configuration
def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )


def pytest_collection_modifyitems(config, items):
    """Automatically mark async tests."""
    for item in items:
        if asyncio.iscoroutinefunction(item.function):
            item.add_marker(pytest.mark.asyncio)