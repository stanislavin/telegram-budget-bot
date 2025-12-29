import pytest
import requests
from unittest.mock import MagicMock, patch
import asyncio
from util.openrouter import process_with_openrouter
from util.config import OPENROUTER_LLM_VERSION

@pytest.fixture
def mock_openrouter_response():
    """Fixture to mock a successful OpenRouter API response."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,RSD,Food,Groceries'}}]
        }
        mock_post.return_value = mock_response
        yield mock_post

@pytest.fixture
def mock_openrouter_response_rub():
    """Fixture to mock a successful OpenRouter API response with RUB."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,RUB,Food,Groceries'}}]
        }
        mock_post.return_value = mock_response
        yield mock_post

@pytest.fixture
def mock_openrouter_response_eur():
    """Fixture to mock a successful OpenRouter API response with EUR."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,EUR,Food,Groceries'}}]
        }
        mock_post.return_value = mock_response
        yield mock_post

@pytest.fixture
def mock_openrouter_response_rsd():
    """Fixture to mock a successful OpenRouter API response with RSD."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,RSD,Food,Groceries'}}]
        }
        mock_post.return_value = mock_response
        yield mock_post

@pytest.fixture
def mock_openrouter_response_invalid_currency():
    """Fixture to mock a successful OpenRouter API response with invalid currency."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,GBP,Food,Groceries'}}]
        }
        mock_post.return_value = mock_response
        yield mock_post

@pytest.fixture
def mock_openrouter_error_response():
    """Fixture to mock an OpenRouter API error response."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError("500 Server Error")
        mock_post.return_value = mock_response
        yield mock_post

@pytest.mark.asyncio
async def test_process_with_openrouter_success(mock_openrouter_response):
    """Test successful processing of a message with OpenRouter."""
    message = "100 rsd food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RSD', 'Food', 'Groceries')
    assert model == OPENROUTER_LLM_VERSION
    mock_openrouter_response.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_rub_currency(mock_openrouter_response_rub):
    """Test successful processing of a message with RUB currency."""
    message = "100 rub food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RUB', 'Food', 'Groceries')
    mock_openrouter_response_rub.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_eur_currency(mock_openrouter_response_eur):
    """Test successful processing of a message with EUR currency."""
    message = "100 eur food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'EUR', 'Food', 'Groceries')
    mock_openrouter_response_eur.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_rsd_currency(mock_openrouter_response_rsd):
    """Test successful processing of a message with RSD currency."""
    message = "100 rsd food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RSD', 'Food', 'Groceries')
    mock_openrouter_response_rsd.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_invalid_currency_defaults_to_rub(mock_openrouter_response_invalid_currency):
    """Test that invalid currency defaults to RUB."""
    message = "100 gbp food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RUB', 'Food', 'Groceries')
    mock_openrouter_response_invalid_currency.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_api_error(mock_openrouter_error_response):
    """Test OpenRouter API error handling."""
    message = "some message"
    result, error = await process_with_openrouter(message)
    
    assert result is None
    assert "Error processing with OpenRouter" in error
    # It tries all 3 models (primary + 2 fallbacks) and then retries the whole process once due to @with_retry
    # Total attempts = (1 primary + 2 fallbacks) * (1 initial + 1 retry) = 6
    assert mock_openrouter_error_response.call_count == 6

@pytest.mark.asyncio
async def test_process_with_openrouter_parsing_error():
    """Test OpenRouter response parsing error."""
    with patch('requests.post') as mock_post:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'choices': [{'message': {'content': '100.00,RSD,Food'}}]
        } # Missing description
        mock_post.return_value = mock_response
        
        message = "100 rsd food"
        result, error = await process_with_openrouter(message)
        
        assert result is None
        assert "Failed to parse OpenRouter response" in error
        # Parsing error happens after successful HTTP call, so it tries all 3 models and then retries
        assert mock_post.call_count == 6

@pytest.mark.asyncio
async def test_process_with_openrouter_fallback_success():
    """Test that it falls back to the next model on 4xx error."""
    with patch('requests.post') as mock_post:
        # First call fails with 404
        mock_response_404 = MagicMock()
        mock_response_404.status_code = 404
        mock_response_404.text = "Model not found"
        
        # Second call succeeds
        mock_response_success = MagicMock()
        mock_response_success.status_code = 200
        mock_response_success.json.return_value = {
            'choices': [{'message': {'content': '100.00,RSD,Food,Groceries'}}]
        }
        
        mock_post.side_effect = [mock_response_404, mock_response_success]
        
        message = "100 rsd food groceries"
        result, error = await process_with_openrouter(message)
        
        assert error is None
        data, model = result
        assert data == (100.0, 'RSD', 'Food', 'Groceries')
        assert model == 'google/gemini-pro-1.5' # First fallback
        assert mock_post.call_count == 2