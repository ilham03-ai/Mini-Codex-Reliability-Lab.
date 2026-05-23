def format_greeting(user: dict) -> str:
    """Return a greeting string for a user dict with 'username' and 'email' keys."""
    return f"Hello, {user['name']}! Your email is {user['email']}."  # BUG: key should be 'username'
