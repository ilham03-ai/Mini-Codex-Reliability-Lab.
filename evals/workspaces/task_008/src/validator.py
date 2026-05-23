import re


def is_valid_email(email: str) -> bool:
    """Return True if email matches a basic email pattern."""
    # BUG: pattern uses [a-z] for domain TLD — should allow longer TLDs like .com, .org
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-z]{2}$"
    return bool(re.match(pattern, email))


def is_valid_phone(phone: str) -> bool:
    """Return True if phone is a US phone number like (123) 456-7890 or 123-456-7890."""
    pattern = r"^(\(\d{3}\)\s?|\d{3}-)\d{3}-\d{4}$"
    return bool(re.match(pattern, phone))
