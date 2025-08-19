import pytest
import requests
from unittest.mock import MagicMock, patch
import asyncio
from util.openrouter import process_with_openrouter

@pytest.fixture
def mock_openrouter_response_no_currency():
    """Fixture to mock a successful OpenRouter API response with no currency specified."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,,Food,Groceries'}}]  # Empty currency field
        }
        mock_post.return_value = mock_response
        yield mock_post

@pytest.mark.asyncio
async def test_process_with_openrouter_no_currency_defaults_to_rsd(mock_openrouter_response_no_currency):
    """Test that when no currency is specified, it defaults to RSD."""
    message = "100 food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    assert result == (100.0, 'RSD', 'Food', 'Groceries')
    mock_openrouter_response_no_currency.assert_called_once()