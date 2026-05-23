import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.formatter import format_greeting

def test_basic_greeting():
    user = {"username": "alice", "email": "alice@example.com"}
    assert format_greeting(user) == "Hello, alice! Your email is alice@example.com."

def test_different_user():
    user = {"username": "bob", "email": "bob@example.com"}
    assert format_greeting(user) == "Hello, bob! Your email is bob@example.com."
