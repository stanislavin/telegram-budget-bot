import logging
import requests

from util.config import OPENROUTER_API_KEY, OPENROUTER_LLM_VERSION, OPENROUTER_URL, get_llm_prompt

logger = logging.getLogger(__name__)

# Define supported currencies
SUPPORTED_CURRENCIES = {'RSD', 'EUR', 'RUB'}

async def process_with_openrouter(message: str) -> tuple:
    """Process message using OpenRouter API and return formatted data."""
    try:
        headers = {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/stanislavin/telegram-budget-bot",
        }
        
        prompt = get_llm_prompt() + "\n\nDescription of expense is: " + message
        data = {
            "model": OPENROUTER_LLM_VERSION,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        }
        
        response = requests.post(OPENROUTER_URL, headers=headers, json=data)
        response.raise_for_status()
        
        # Extract the formatted response
        formatted_text = response.json()['choices'][0]['message']['content'].strip()
        
        # Parse the formatted response
        parts = formatted_text.split(',')
        if len(parts) != 4:
            return None, "Failed to parse OpenRouter response"
            
        amount = float(parts[0])
        currency = parts[1].upper()
        category = parts[2]
        description = parts[3]
        
        # Ensure currency defaults to RSD if not specified or invalid
        # Only default to RSD if the currency is truly unspecified or invalid
        # The prompt says to default to RSD if unspecified OR ambiguous
        if currency not in SUPPORTED_CURRENCIES:
            logger.warning(f"Invalid or ambiguous currency '{currency}' detected, defaulting to RSD")
            currency = 'RSD'
        
        return (amount, currency, category, description), None
        
    except Exception as e:
        return None, f"Error processing with OpenRouter: {str(e)}" 