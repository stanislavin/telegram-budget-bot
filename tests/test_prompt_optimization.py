import pytest
import re
from unittest.mock import patch, mock_open

# Test to compare the effectiveness of original vs optimized prompt
class TestPromptOptimization:
    
    def test_original_prompt_category_extraction(self):
        """Test that the original prompt correctly extracts categories."""
        # Load the original prompt
        with open('prompt.txt', 'r') as f:
            original_prompt = f.read()
        
        # Extract categories using the same regex as in telegram.py
        category_pattern = r'- ([a-zA-Z]+) \(.*?\)'
        original_categories = re.findall(category_pattern, original_prompt)
        
        # Check that we have the expected number of categories
        assert len(original_categories) == 16  # Based on the original prompt
        
        # Check that all expected categories are present
        expected_categories = [
            'bills', 'car', 'deliveries', 'entertainment',
            'essentials', 'health', 'home', 'presents', 'services', 'sport',
            'taxi', 'whatever', 'work', 'education', 'bonus', 'extra'
        ]
        
        for category in expected_categories:
            assert category in original_categories
    
    def test_optimized_prompt_category_extraction(self):
        """Test that the optimized prompt correctly extracts categories."""
        # Load the optimized prompt
        with open('prompt_optimized.txt', 'r') as f:
            optimized_prompt = f.read()
        
        # Extract categories using the same regex as in telegram.py
        category_pattern = r'- ([a-zA-Z]+) \(.*?\)'
        optimized_categories = re.findall(category_pattern, optimized_prompt)
        
        # Check that we have the expected number of categories
        assert len(optimized_categories) == 16  # Should be the same number
        
        # Check that all expected categories are present
        expected_categories = [
            'bills', 'car', 'deliveries', 'entertainment',
            'essentials', 'health', 'home', 'presents', 'services', 'sport',
            'taxi', 'whatever', 'work', 'education', 'bonus', 'extra'
        ]
        
        for category in expected_categories:
            assert category in optimized_categories
    
    def test_prompt_clarity_improvements(self):
        """Test specific improvements in the optimized prompt."""
        with open('prompt.txt', 'r') as f:
            original_prompt = f.read()
        
        with open('prompt_optimized.txt', 'r') as f:
            optimized_prompt = f.read()
        
        # Check that the optimized prompt has clearer category descriptions
        # Each category should have a more descriptive explanation
        assert 'bills (includes utilities, health insurance, home rent payments)' in optimized_prompt
        assert 'car (includes fuel, repairs, car-related expenses)' in optimized_prompt
        assert 'entertainment (e.g., movies, concerts, games)' in optimized_prompt
        
        # Check that the optimized prompt has better formatting
        # The task list should be clearly numbered
        assert 'Your task is to:' in optimized_prompt
        assert '1. Extract the numeric amount spent from the description.' in optimized_prompt
        assert '2. Determine the currency (RSD, EUR, RUB), defaulting to RSD if unspecified or ambiguous.' in optimized_prompt
        assert '3. Take the category from the description if it is provided.' in optimized_prompt
        assert '4. If no category is provided, categorize the expense into one of the following categories:' in optimized_prompt
    
    def test_prompt_consistency(self):
        """Test that both prompts produce the same categories with the regex."""
        with open('prompt.txt', 'r') as f:
            original_prompt = f.read()
        
        with open('prompt_optimized.txt', 'r') as f:
            optimized_prompt = f.read()
        
        # Extract categories using the same regex as in telegram.py
        category_pattern = r'- ([a-zA-Z]+) \(.*?\)'
        original_categories = set(re.findall(category_pattern, original_prompt))
        optimized_categories = set(re.findall(category_pattern, optimized_prompt))
        
        # Both should produce the same set of categories
        assert original_categories == optimized_categories
    
    def test_examples_improved(self):
        """Test that the optimized prompt has better examples."""
        with open('prompt_optimized.txt', 'r') as f:
            optimized_prompt = f.read()
        
        # Check that we have more comprehensive examples
        assert '1200 EUR за аренду квартиры' in optimized_prompt
        assert '300 на подарок другу' in optimized_prompt
        assert '1500 на курс программирования' in optimized_prompt
    
    def test_output_format_clarification(self):
        """Test that the optimized prompt has clearer output format instructions."""
        with open('prompt_optimized.txt', 'r') as f:
            optimized_prompt = f.read()
        
        # Check for clearer rules about output format
        assert 'Rules:' in optimized_prompt
        assert 'Use only commas as separators' in optimized_prompt
        assert 'Strictly follow the field order: expense, currency, category, description' in optimized_prompt
        assert 'Exclude the expense amount, currency and category from the "original description" field' in optimized_prompt
        assert 'If the category is unclear, use "extra" and ensure the output still follows the format' in optimized_prompt
        assert 'Do not include any additional information in the output' in optimized_prompt