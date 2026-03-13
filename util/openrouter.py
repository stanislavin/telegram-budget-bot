import logging
import requests
import asyncio

from util.config import (
    OPENROUTER_API_KEY, 
    OPENROUTER_LLM_VERSION, 
    OPENROUTER_FALLBACK_MODELS,
    OPENROUTER_URL, 
    get_llm_prompt
)
from util.retry_handler import with_retry

logger = logging.getLogger(__name__)

# Define supported currencies
SUPPORTED_CURRENCIES = {'RSD', 'EUR', 'RUB'}

VALID_SPENDING_TYPES = {'need', 'want', 'invest'}

def _parse_openrouter_response(formatted_text: str):
    """Helper function to parse OpenRouter API response."""
    parts = formatted_text.split(',')
    if len(parts) == 5:
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        spending_type = parts[3].strip().lower()
        description = parts[4]
    elif len(parts) == 4:
        # Backwards compatibility: no spending_type field
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        spending_type = None
        description = parts[3]
    else:
        raise ValueError("Failed to parse OpenRouter response")

    # Ensure currency defaults to RUB if not specified or invalid
    if currency not in SUPPORTED_CURRENCIES:
        logger.warning(f"Invalid or ambiguous currency '{currency}' detected, defaulting to RUB")
        currency = 'RUB'

    # Validate spending_type
    if spending_type not in VALID_SPENDING_TYPES:
        logger.warning(f"Invalid spending_type '{spending_type}', defaulting to None")
        spending_type = None

    return amount, currency, category, spending_type, description


@with_retry(max_retries=1, error_message="Error processing with OpenRouter")
async def process_with_openrouter(message: str) -> tuple:
    """Process message using OpenRouter API and return formatted data and model used."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/stanislavin/telegram-budget-bot",
    }

    prompt = get_llm_prompt() + "\n\nDescription of expense is: " + message
    
    models_to_try = [OPENROUTER_LLM_VERSION] + OPENROUTER_FALLBACK_MODELS
    last_error = None

    for model in models_to_try:
        try:
            logger.info(f"Attempting to process with model: {model}")
            data = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            }

            response = await asyncio.to_thread(requests.post, OPENROUTER_URL, headers=headers, json=data)
            
            # If we get a 4xx error, we might want to try the next model
            if 400 <= response.status_code < 500:
                logger.warning(f"Model {model} failed with status {response.status_code}: {response.text}")
                last_error = f"HTTP {response.status_code}: {response.text}"
                continue
                
            response.raise_for_status()

            # Extract the formatted response
            formatted_text = response.json()['choices'][0]['message']['content'].strip()
            parsed_data = _parse_openrouter_response(formatted_text)
            
            return parsed_data, model
            
        except Exception as e:
            logger.error(f"Error with model {model}: {str(e)}")
            last_error = str(e)
            # For non-4xx errors, we might still want to try the next model if it's a connection issue
            continue

    raise RuntimeError(f"All models failed. Last error: {last_error}")