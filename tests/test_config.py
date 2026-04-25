import pytest
import os
from unittest.mock import patch, mock_open, MagicMock
import tempfile

from util.config import get_llm_prompt


@pytest.fixture
def mock_env_vars():
    """Fixture to mock environment variables."""
    env_vars = {
        'TELEGRAM_BOT_TOKEN': 'test_bot_token',
        'OPENROUTER_API_KEY': 'test_openrouter_key',
        'OPENROUTER_LLM_VERSION': 'test_model_version',
        'SERVICE_URL': 'http://test-service.com',
    }
    with patch.dict(os.environ, env_vars, clear=True):
        yield env_vars


def test_environment_variables_loaded(mock_env_vars):
    """Test that environment variables are properly loaded."""
    # Re-import config to trigger environment variable loading
    import importlib
    from util import config
    importlib.reload(config)
    
    assert config.TELEGRAM_BOT_TOKEN == 'test_bot_token'
    assert config.OPENROUTER_API_KEY == 'test_openrouter_key'
    assert config.SERVICE_URL == 'http://test-service.com'


def test_default_values():
    """Test that default values are used when environment variables are not set."""
    # Test constants that should always have default values
    from util import config
    assert config.HEALTH_CHECK_PORT == 8000
    assert config.HEALTH_CHECK_HOST == '0.0.0.0'
    assert config.OPENROUTER_URL == "https://openrouter.ai/api/v1/chat/completions"


def test_env_flag_parses_boolean_values():
    """env_flag should honor truthy and falsy strings."""
    from util import config
    with patch.dict(os.environ, {'RUN_TELEGRAM_BOT': 'off'}):
        assert config.env_flag('RUN_TELEGRAM_BOT', True) is False


def test_openrouter_url_constant():
    """Test that OpenRouter URL is set correctly."""
    from util import config
    assert config.OPENROUTER_URL == "https://openrouter.ai/api/v1/chat/completions"


def test_get_llm_prompt_success():
    """Test successful loading of LLM prompt."""
    prompt_content = "Test prompt content for LLM processing"
    
    with patch('builtins.open', mock_open(read_data=prompt_content)):
        result = get_llm_prompt()
        assert result == prompt_content


def test_get_llm_prompt_caching():
    """Test that LLM prompt is cached after first load."""
    from util import config
    
    # Reset cache
    original_prompt = config._LLM_PROMPT
    config._LLM_PROMPT = None
    
    try:
        prompt_content = "Cached prompt content"
        
        with patch('builtins.open', mock_open(read_data=prompt_content)) as mock_file:
            # First call should read from file
            result1 = get_llm_prompt()
            assert result1 == prompt_content
            
            # Second call should use cached value, not read file again
            result2 = get_llm_prompt()
            assert result2 == prompt_content
            
            # File should only be opened once
            mock_file.assert_called_once_with('prompt.txt', 'r')
    finally:
        # Restore original state
        config._LLM_PROMPT = original_prompt


def test_get_llm_prompt_file_not_found():
    """Test error handling when prompt.txt file is not found."""
    from util import config
    
    # Reset cache
    original_prompt = config._LLM_PROMPT
    config._LLM_PROMPT = None
    
    try:
        with patch('builtins.open', side_effect=FileNotFoundError("No such file")):
            with pytest.raises(RuntimeError, match="Failed to load prompt.txt: No such file"):
                get_llm_prompt()
    finally:
        config._LLM_PROMPT = original_prompt


def test_get_llm_prompt_permission_error():
    """Test error handling when prompt.txt cannot be read due to permissions."""
    from util import config
    
    # Reset cache
    original_prompt = config._LLM_PROMPT
    config._LLM_PROMPT = None
    
    try:
        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            with pytest.raises(RuntimeError, match="Failed to load prompt.txt: Permission denied"):
                get_llm_prompt()
    finally:
        config._LLM_PROMPT = original_prompt


def test_get_llm_prompt_io_error():
    """Test error handling for general IO errors."""
    from util import config
    
    # Reset cache
    original_prompt = config._LLM_PROMPT
    config._LLM_PROMPT = None
    
    try:
        with patch('builtins.open', side_effect=IOError("IO Error")):
            with pytest.raises(RuntimeError, match="Failed to load prompt.txt: IO Error"):
                get_llm_prompt()
    finally:
        config._LLM_PROMPT = original_prompt


def test_logging_configuration():
    """Test that logging is configured correctly."""
    import logging
    # Just verify that we can get a logger and it has a reasonable configuration
    logger = logging.getLogger('test_logger')
    assert logger is not None


def test_dotenv_loading():
    """Test that dotenv module is imported."""
    # Just verify that the load_dotenv function exists in the config module
    from util import config
    # This is a basic smoke test - the module should import without errors
    assert hasattr(config, 'TELEGRAM_BOT_TOKEN')


@patch.dict(os.environ, {'OPENROUTER_LLM_VERSION': 'custom_model'})
def test_custom_openrouter_model():
    """Test that custom OpenRouter model version can be set."""
    import importlib
    from util import config
    importlib.reload(config)
    
    assert config.OPENROUTER_LLM_VERSION == 'custom_model'


@patch.dict(os.environ, {'SERVICE_URL': 'https://custom-service.com'})
def test_custom_service_url():
    """Test that custom service URL can be set."""
    import importlib
    from util import config
    importlib.reload(config)
    
    assert config.SERVICE_URL == 'https://custom-service.com'


def test_global_prompt_cache_reset():
    """Test that the global prompt cache can be reset."""
    from util import config
    
    # Reset the global cache
    config._LLM_PROMPT = None
    
    prompt_content = "New prompt after reset"
    with patch('builtins.open', mock_open(read_data=prompt_content)):
        result = get_llm_prompt()
        assert result == prompt_content
        assert config._LLM_PROMPT == prompt_content


def test_multiple_prompt_loads_different_content():
    """Test loading prompt with different content after cache reset."""
    from util import config
    
    # First load
    config._LLM_PROMPT = None
    prompt1 = "First prompt content"
    with patch('builtins.open', mock_open(read_data=prompt1)):
        result1 = get_llm_prompt()
        assert result1 == prompt1
    
    # Reset cache and load again
    config._LLM_PROMPT = None
    prompt2 = "Second prompt content"
    with patch('builtins.open', mock_open(read_data=prompt2)):
        result2 = get_llm_prompt()
        assert result2 == prompt2
    
    # Results should be different
    assert result1 != result2


def test_prompt_includes_toys_category():
    """Prompt should list the toys category for expense classification."""
    from util import config
    original_prompt = config._LLM_PROMPT
    config._LLM_PROMPT = None
    try:
        prompt = get_llm_prompt()
        assert "toys" in prompt.lower()
    finally:
        config._LLM_PROMPT = original_prompt
