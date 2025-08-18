# Prompt Optimization for Telegram Budget Bot

This document explains the improvements made to the LLM prompt used by the Telegram Budget Bot for categorizing expenses.

## Original Prompt Issues

The original prompt had several areas for improvement:

1. **Unclear task structure** - The instructions were not clearly numbered or organized
2. **Inconsistent category descriptions** - Some categories had detailed descriptions while others were brief
3. **Lack of clear output format rules** - The rules for formatting the output were not explicitly stated
4. **Limited examples** - Only two examples were provided

## Optimized Prompt Improvements

The optimized prompt (`prompt_optimized.txt`) includes the following improvements:

### 1. Clearer Task Structure
- Numbered steps for better understanding
- Logical flow from extraction to categorization to output

### 2. Enhanced Clarity
- More consistent category descriptions with clear examples
- Better explanation of the expected output format
- Explicit rules for handling edge cases

### 3. Improved Examples
- Five comprehensive examples covering different scenarios
- Examples with various currencies and categories

### 4. Explicit Rules
- Clear formatting rules for the output
- Specific instructions for handling ambiguous cases
- Guidelines for excluding information from the description field

## Testing

The improvements have been validated with comprehensive tests in `tests/test_prompt_optimization.py` that verify:

1. Category extraction works correctly
2. Both prompts produce the same set of categories
3. The optimized prompt has clearer instructions
4. Examples are comprehensive and helpful
5. Output format rules are explicit

## How to Use

To use the optimized prompt in the bot:

1. Replace the content of `prompt.txt` with the content of `prompt_optimized.txt`
2. Run the bot as usual

The bot will automatically use the improved prompt for better expense categorization accuracy.