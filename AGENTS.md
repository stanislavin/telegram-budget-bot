# Agent Guidelines for Telegram Budget Bot

This document outlines conventions and commands for agentic coding in this repository.

## 1. Build/Lint/Test Commands

- **Install Dependencies**: `pip install -r requirements.txt`
- **Run Bot**: `python bot.py`
- **Linting**:
  - Install: `pip install flake8 black` (if not already installed)
  - Run Flake8: `flake8 .`
  - Run Black (formatter): `black .`
- **Testing**:
  - Install: `pip install pytest` (if not already installed)
  - Run all tests: `pytest`
  - Run a single test: `pytest <path_to_test_file>::<test_function_name>`

## 2. Code Style Guidelines

- **Imports**: Group imports as follows: standard library, third-party, local application.
- **Formatting**: Adhere to PEP 8 guidelines. Use `black` for automatic formatting.
- **Types**: Use Python type hints for function arguments and return values.
- **Naming Conventions**: Follow PEP 8: `snake_case` for functions, variables, and modules; `PascalCase` for classes.
- **Error Handling**: Use `try-except` blocks for robust error handling. Log exceptions where appropriate.
