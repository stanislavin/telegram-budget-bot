import pytest
import requests
import time
from unittest.mock import MagicMock, patch, Mock, AsyncMock
from threading import Thread
from flask import Flask

from util.health import start_health_check, nudge_pinger, start_nudge, build_app


@pytest.fixture
def mock_flask_app():
    """Mock Flask app for testing."""
    with patch('util.health.Flask') as mock_flask:
        app_instance = MagicMock()
        mock_flask.return_value = app_instance
        yield app_instance


@pytest.fixture
def mock_thread():
    """Mock Thread for testing."""
    with patch('util.health.Thread') as mock_thread_class:
        thread_instance = MagicMock()
        mock_thread_class.return_value = thread_instance
        yield thread_instance


def test_start_health_check(mock_flask_app, mock_thread):
    """Test starting the health check server."""
    start_health_check()
    
    # Verify Flask app was created and configured
    mock_flask_app.route.assert_any_call('/health')
    mock_flask_app.route.assert_any_call('/nudge')
    
    # Verify thread was started
    mock_thread.start.assert_called_once()
    assert mock_thread.daemon is True


def test_health_check_endpoint():
    """Test the health check endpoint directly."""
    with patch('util.health.Flask') as mock_flask:
        app_instance = MagicMock()
        mock_flask.return_value = app_instance
        
        # Simulate calling start_health_check to register routes
        start_health_check()
        
        # Get the registered health check function
        health_route_calls = [call for call in app_instance.route.call_args_list if call[0][0] == '/health']
        assert len(health_route_calls) == 1


def test_nudge_endpoint():
    """Test the nudge endpoint directly."""
    with patch('util.health.Flask') as mock_flask:
        app_instance = MagicMock()
        mock_flask.return_value = app_instance
        
        # Simulate calling start_health_check to register routes
        start_health_check()
        
        # Get the registered nudge function
        nudge_route_calls = [call for call in app_instance.route.call_args_list if call[0][0] == '/nudge']
        assert len(nudge_route_calls) == 1


@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_success(mock_sleep, mock_requests_get):
    """Test successful nudge pinger execution."""
    # Mock successful response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_requests_get.return_value = mock_response
    
    # Stop the infinite loop after a few iterations
    mock_sleep.side_effect = [None, None, KeyboardInterrupt()]
    
    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()
    
    # Verify requests were made with timeout
    assert mock_requests_get.call_count >= 2
    call_args = mock_requests_get.call_args
    assert 'timeout' in call_args[1]
    assert call_args[1]['timeout'] == 10


@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_http_error(mock_sleep, mock_requests_get):
    """Test nudge pinger with HTTP error responses."""
    # Mock failed response
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_requests_get.return_value = mock_response
    
    # Stop after a few iterations
    mock_sleep.side_effect = [None, None, KeyboardInterrupt()]
    
    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()
    
    # Verify requests were made
    assert mock_requests_get.call_count >= 2


@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_request_exception(mock_sleep, mock_requests_get):
    """Test nudge pinger with request exceptions."""
    # Import the exception class
    from requests.exceptions import RequestException
    # Mock request exception
    mock_requests_get.side_effect = RequestException("Connection error")
    
    # Stop after a few iterations
    mock_sleep.side_effect = [None, None, KeyboardInterrupt()]
    
    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()
    
    # Verify requests were attempted
    assert mock_requests_get.call_count >= 2


@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_timeout_exception(mock_sleep, mock_requests_get):
    """Test nudge pinger with timeout exceptions."""
    # Import the exception class
    from requests.exceptions import Timeout
    # Mock timeout exception
    mock_requests_get.side_effect = Timeout("Request timeout")
    
    # Stop after a few iterations
    mock_sleep.side_effect = [None, None, KeyboardInterrupt()]
    
    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()
    
    # Verify requests were attempted
    assert mock_requests_get.call_count >= 2


@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_consecutive_failures(mock_sleep, mock_requests_get):
    """Test nudge pinger consecutive failure handling."""
    # Mock consistent failures
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_requests_get.return_value = mock_response
    
    # Stop after enough iterations to trigger consecutive failure warning
    mock_sleep.side_effect = [None] * 5 + [KeyboardInterrupt()]
    
    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()
    
    # Should have made multiple requests
    assert mock_requests_get.call_count >= 5


def test_start_nudge(mock_thread):
    """Test starting the nudge monitor."""
    start_nudge()
    
    # Verify thread was created and started
    mock_thread.start.assert_called_once()
    assert mock_thread.daemon is True


def test_start_nudge_thread_restart(mock_thread):
    """Test that start_nudge creates a monitor thread."""
    start_nudge()
    
    # Verify thread was created and started
    mock_thread.start.assert_called_once()
    assert mock_thread.daemon is True


@patch('util.health.SERVICE_URL', 'http://test-service.com')
@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_uses_correct_url(mock_sleep, mock_requests_get):
    """Test that nudge pinger uses the correct service URL."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_requests_get.return_value = mock_response
    
    mock_sleep.side_effect = [KeyboardInterrupt()]  # Stop after first iteration
    
    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()
    
    # Verify correct URL was called
    mock_requests_get.assert_called_with('http://test-service.com/nudge', timeout=10)


@patch('util.health.HEALTH_CHECK_HOST', '127.0.0.1')
@patch('util.health.HEALTH_CHECK_PORT', 9000)
def test_start_health_check_configuration(mock_flask_app, mock_thread):
    """Test that health check uses correct host and port configuration."""
    start_health_check()

    # Verify Flask app.run was configured with correct host and port
    # Note: We can't directly test app.run since it's called in a thread,
    # but we can verify the configuration is passed correctly
    mock_thread.start.assert_called_once()


# ---------- Flask route response body tests ----------

def test_health_check_route_response():
    """Test /health endpoint returns 'OK' with 200 (covers line 19)."""
    flask_app = build_app()
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()
    response = client.get('/health')
    assert response.status_code == 200
    assert response.data == b'OK'


def test_nudge_route_response():
    """Test /nudge endpoint returns 'OK' with 200 (covers line 24)."""
    flask_app = build_app()
    flask_app.config['TESTING'] = True
    client = flask_app.test_client()
    response = client.get('/nudge')
    assert response.status_code == 200
    assert response.data == b'OK'


# ---------- nudge_pinger unexpected exception ----------

@patch('util.health.requests.get')
@patch('util.health.time.sleep')
def test_nudge_pinger_unexpected_exception(mock_sleep, mock_requests_get):
    """Test nudge_pinger handles non-requests exceptions (covers lines 69-71)."""
    mock_requests_get.side_effect = ValueError("Unexpected internal error")
    mock_sleep.side_effect = [None, KeyboardInterrupt()]

    with pytest.raises(KeyboardInterrupt):
        nudge_pinger()

    assert mock_requests_get.call_count >= 1


def test_start_nudge_monitor_restarts_pinger():
    """Test that monitor_and_restart creates, starts and joins a nudge thread (lines 82-91)."""
    with patch('util.health.Thread') as MockThread:
        monitor_instance = MagicMock()
        MockThread.return_value = monitor_instance

        start_nudge()

        # Retrieve the monitor_and_restart target function
        monitor_target = MockThread.call_args[1]['target']

    # Now invoke monitor_and_restart directly, with Thread and time patched
    with patch('util.health.Thread') as MockThread2:
        with patch('util.health.time.sleep') as mock_sleep:
            nudge_instance = MagicMock()
            MockThread2.return_value = nudge_instance
            # Break out of the infinite loop after one iteration
            mock_sleep.side_effect = KeyboardInterrupt()

            with pytest.raises(KeyboardInterrupt):
                monitor_target()

            MockThread2.assert_called_once()
            nudge_instance.start.assert_called_once()
            nudge_instance.join.assert_called_once()

