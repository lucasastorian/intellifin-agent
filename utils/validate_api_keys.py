"""API Key validation utilities"""
import os
import sys


def validate_api_keys(model: str) -> None:
    """
    Validates that required API keys are present in the environment.

    Args:
        model: The model name being used

    Raises:
        SystemExit: If any required API keys are missing
    """
    missing_keys = []

    # VOYAGE_API_KEY is ALWAYS required for embeddings
    if not os.getenv("VOYAGE_API_KEY"):
        missing_keys.append({
            "key": "VOYAGE_API_KEY",
            "reason": "Required for embeddings (used universally)",
            "docs": "Get your API key from https://www.voyageai.com/"
        })

    # Model-specific API keys
    model_key_map = {
        'gpt-5': ('OPENAI_API_KEY', 'Required for OpenAI GPT-5 model', 'Get your API key from https://platform.openai.com/'),
        'gpt-5-mini': ('OPENAI_API_KEY', 'Required for OpenAI GPT-5-mini model', 'Get your API key from https://platform.openai.com/'),
        'claude-haiku-4-5': ('ANTHROPIC_API_KEY', 'Required for Claude Haiku 4.5 model', 'Get your API key from https://console.anthropic.com/'),
        'claude-sonnet-4-5': ('ANTHROPIC_API_KEY', 'Required for Claude Sonnet 4.5 model', 'Get your API key from https://console.anthropic.com/'),
        'claude-opus-4-1': ('ANTHROPIC_API_KEY', 'Required for Claude Opus 4.1 model', 'Get your API key from https://console.anthropic.com/'),
        'gemini-2.5-flash': ('GEMINI_API_KEY', 'Required for Gemini 2.5 Flash model', 'Get your API key from https://aistudio.google.com/'),
        'gemini-2.5-pro': ('GEMINI_API_KEY', 'Required for Gemini 2.5 Pro model', 'Get your API key from https://aistudio.google.com/'),
        'grok-4': ('XAI_API_KEY', 'Required for xAI Grok-4 model', 'Get your API key from https://x.ai/'),
        'openai/gpt-oss-120b': ('GROQ_API_KEY', 'Required for Groq models', 'Get your API key from https://console.groq.com/'),
        'openai/gpt-oss-20b': ('GROQ_API_KEY', 'Required for Groq models', 'Get your API key from https://console.groq.com/'),
        'moonshotai/kimi-k2-instruct-0905': ('GROQ_API_KEY', 'Required for Groq models', 'Get your API key from https://console.groq.com/'),
    }

    if model in model_key_map:
        key_name, reason, docs = model_key_map[model]
        if not os.getenv(key_name):
            missing_keys.append({
                "key": key_name,
                "reason": reason,
                "docs": docs
            })

    # If any keys are missing, print a very obvious error message and exit
    if missing_keys:
        print("\n" + "=" * 80)
        print("ERROR: MISSING REQUIRED API KEYS")
        print("=" * 80)
        print("\nThe following API keys are missing from your environment:\n")

        for idx, key_info in enumerate(missing_keys, 1):
            print(f"{idx}. {key_info['key']}")
            print(f"   Reason: {key_info['reason']}")
            print(f"   Docs:   {key_info['docs']}")
            print()

        print("=" * 80)
        print("HOW TO FIX:")
        print("=" * 80)
        print("1. Create a .env file in the project root (if not already present)")
        print("2. Add the missing API key(s) to the .env file:")
        print()
        for key_info in missing_keys:
            print(f"   {key_info['key']}=your_api_key_here")
        print()
        print("3. Run the command again")
        print("=" * 80 + "\n")

        sys.exit(1)

    # If we get here, all required keys are present
    print("✓ All required API keys are present")
