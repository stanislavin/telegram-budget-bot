import pytest
import requests
from unittest.mock import MagicMock, patch, call
import asyncio
from util.openrouter import process_with_openrouter, _call_chat_completion, _build_provider_chain
from util.config import OPENROUTER_LLM_VERSION, OPENROUTER_FALLBACK_MODELS, OPENROUTER_URL

@pytest.fixture(autouse=True)
def disable_local_llm():
    """Disable local LLM so tests only exercise the OpenRouter path."""
    with patch('util.openrouter.LOCAL_LLM_URL', ''), \
         patch('util.openrouter.LOCAL_LLM_MODEL', ''), \
         patch('util.openrouter.DATABASE_URL', None):
        yield

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
    assert data == (100.0, 'RSD', 'Food', None, 'Groceries')
    assert model == OPENROUTER_LLM_VERSION
    mock_openrouter_response.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_rub_currency(mock_openrouter_response_rub):
    """Test successful processing of a message with RUB currency."""
    message = "100 rub food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RUB', 'Food', None, 'Groceries')
    mock_openrouter_response_rub.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_eur_currency(mock_openrouter_response_eur):
    """Test successful processing of a message with EUR currency."""
    message = "100 eur food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'EUR', 'Food', None, 'Groceries')
    mock_openrouter_response_eur.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_rsd_currency(mock_openrouter_response_rsd):
    """Test successful processing of a message with RSD currency."""
    message = "100 rsd food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RSD', 'Food', None, 'Groceries')
    mock_openrouter_response_rsd.assert_called_once()

@pytest.mark.asyncio
async def test_process_with_openrouter_invalid_currency_defaults_to_rub(mock_openrouter_response_invalid_currency):
    """Test that invalid currency defaults to RUB."""
    message = "100 gbp food groceries"
    result, error = await process_with_openrouter(message)
    
    assert error is None
    data, model = result
    assert data == (100.0, 'RUB', 'Food', None, 'Groceries')
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
        assert data == (100.0, 'RSD', 'Food', None, 'Groceries')
        assert model == OPENROUTER_FALLBACK_MODELS[0] # First fallback
        assert mock_post.call_count == 2


# ---------- _build_provider_chain tests ----------

class TestBuildProviderChain:
    def test_chain_includes_local_when_configured(self):
        """Local LLM should be first in chain when URL and model are set."""
        with patch('util.openrouter.LOCAL_LLM_URL', 'http://myhost:1234/v1/chat/completions'), \
             patch('util.openrouter.LOCAL_LLM_MODEL', 'my-model'):
            chain = _build_provider_chain()
            # First entry is local
            url, headers, model, timeout = chain[0]
            assert url == 'http://myhost:1234/v1/chat/completions'
            assert model == 'my-model'
            assert 'Authorization' not in headers
            # Remaining entries are OpenRouter
            assert len(chain) == 1 + 1 + len(OPENROUTER_FALLBACK_MODELS)
            for _, hdr, _, _ in chain[1:]:
                assert 'Authorization' in hdr

    def test_chain_skips_local_when_url_empty(self):
        """When LOCAL_LLM_URL is empty, chain should only have OpenRouter models."""
        with patch('util.openrouter.LOCAL_LLM_URL', ''), \
             patch('util.openrouter.LOCAL_LLM_MODEL', 'my-model'):
            chain = _build_provider_chain()
            assert len(chain) == 1 + len(OPENROUTER_FALLBACK_MODELS)
            for url, _, _, _ in chain:
                assert url == OPENROUTER_URL

    def test_chain_skips_local_when_model_empty(self):
        """When LOCAL_LLM_MODEL is empty, chain should only have OpenRouter models."""
        with patch('util.openrouter.LOCAL_LLM_URL', 'http://myhost:1234/v1/chat/completions'), \
             patch('util.openrouter.LOCAL_LLM_MODEL', ''):
            chain = _build_provider_chain()
            for url, _, _, _ in chain:
                assert url == OPENROUTER_URL


# ---------- _call_chat_completion tests ----------

class TestCallChatCompletion:
    def test_success(self):
        """Should return content and model on 200."""
        with patch('util.openrouter.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                'choices': [{'message': {'content': '  hello  '}}]
            }
            mock_post.return_value = mock_resp

            content, model = _call_chat_completion(
                'http://test/v1/chat/completions',
                {'Content-Type': 'application/json'},
                'test-model',
                [{'role': 'user', 'content': 'hi'}],
            )
            assert content == 'hello'
            assert model == 'test-model'

    def test_4xx_raises_value_error(self):
        """Should raise ValueError on 4xx status."""
        with patch('util.openrouter.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 404
            mock_resp.text = 'Not Found'
            mock_post.return_value = mock_resp

            with pytest.raises(ValueError, match='HTTP 404'):
                _call_chat_completion(
                    'http://test/v1/chat/completions', {}, 'model',
                    [{'role': 'user', 'content': 'hi'}],
                )

    def test_5xx_raises_http_error(self):
        """Should raise on 5xx via raise_for_status."""
        with patch('util.openrouter.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError('500')
            mock_post.return_value = mock_resp

            with pytest.raises(requests.exceptions.HTTPError):
                _call_chat_completion(
                    'http://test/v1/chat/completions', {}, 'model',
                    [{'role': 'user', 'content': 'hi'}],
                )

    def test_timeout_passed(self):
        """Should pass timeout to requests.post."""
        with patch('util.openrouter.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                'choices': [{'message': {'content': 'ok'}}]
            }
            mock_post.return_value = mock_resp

            _call_chat_completion(
                'http://test/v1/chat/completions', {}, 'model',
                [{'role': 'user', 'content': 'hi'}], timeout=42,
            )
            _, kwargs = mock_post.call_args
            assert kwargs['timeout'] == 42


# ---------- Local LLM preferred with fallback tests ----------

class TestLocalLLMFallback:
    @pytest.mark.asyncio
    async def test_local_llm_success_skips_openrouter(self):
        """When local LLM succeeds, OpenRouter should not be called."""
        with patch('util.openrouter.LOCAL_LLM_URL', 'http://local:1234/v1/chat/completions'), \
             patch('util.openrouter.LOCAL_LLM_MODEL', 'local-model'), \
             patch('util.openrouter.requests.post') as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                'choices': [{'message': {'content': '50.00,EUR,Food,need,Pizza'}}]
            }
            mock_post.return_value = mock_resp

            result, error = await process_with_openrouter("50 eur pizza")
            assert error is None
            data, model = result
            assert model == 'local-model'
            assert data == (50.0, 'EUR', 'Food', 'need', 'Pizza')
            # Only one call — to the local LLM
            mock_post.assert_called_once()
            call_url = mock_post.call_args[0][0]
            assert call_url == 'http://local:1234/v1/chat/completions'

    @pytest.mark.asyncio
    async def test_local_llm_timeout_falls_back_to_openrouter(self):
        """When local LLM times out, should fall back to OpenRouter."""
        with patch('util.openrouter.LOCAL_LLM_URL', 'http://local:1234/v1/chat/completions'), \
             patch('util.openrouter.LOCAL_LLM_MODEL', 'local-model'), \
             patch('util.openrouter.requests.post') as mock_post:
            # First call (local) times out, second call (OpenRouter) succeeds
            mock_success = MagicMock()
            mock_success.status_code = 200
            mock_success.json.return_value = {
                'choices': [{'message': {'content': '50.00,EUR,Food,need,Pizza'}}]
            }
            mock_post.side_effect = [
                requests.exceptions.ConnectionError("Connection refused"),
                mock_success,
            ]

            result, error = await process_with_openrouter("50 eur pizza")
            assert error is None
            data, model = result
            assert model == OPENROUTER_LLM_VERSION
            assert mock_post.call_count == 2

    @pytest.mark.asyncio
    async def test_local_llm_error_falls_back_to_openrouter(self):
        """When local LLM returns 500, should fall back to OpenRouter."""
        with patch('util.openrouter.LOCAL_LLM_URL', 'http://local:1234/v1/chat/completions'), \
             patch('util.openrouter.LOCAL_LLM_MODEL', 'local-model'), \
             patch('util.openrouter.requests.post') as mock_post:
            mock_local_err = MagicMock()
            mock_local_err.status_code = 500
            mock_local_err.raise_for_status.side_effect = requests.exceptions.HTTPError("500")

            mock_openrouter_ok = MagicMock()
            mock_openrouter_ok.status_code = 200
            mock_openrouter_ok.json.return_value = {
                'choices': [{'message': {'content': '50.00,EUR,Food,need,Pizza'}}]
            }
            mock_post.side_effect = [mock_local_err, mock_openrouter_ok]

            result, error = await process_with_openrouter("50 eur pizza")
            assert error is None
            data, model = result
            assert model == OPENROUTER_LLM_VERSION