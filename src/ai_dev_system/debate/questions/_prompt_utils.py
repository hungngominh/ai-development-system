"""Shared helpers for SYSTEM/USER prompt templates.

All 4 stage modules use the same prompt file convention:

    SYSTEM
    <system content>

    USER
    <user template with {placeholders}>

Placeholder substitution uses `str.replace` rather than `str.format`
because the substituted values are JSON dumps containing `{`/`}`
characters that would crash `.format`.
"""


def split_prompt(template: str) -> tuple[str, str]:
    """Split a prompt template into (system, user_template).

    Raises:
        ValueError: when the template lacks a USER section.
    """
    if "\nUSER\n" not in template:
        raise ValueError("Prompt template missing USER section")
    system_block, user_template = template.split("\nUSER\n", 1)
    if system_block.startswith("SYSTEM\n"):
        system_block = system_block[len("SYSTEM\n"):]
    return system_block.strip(), user_template
