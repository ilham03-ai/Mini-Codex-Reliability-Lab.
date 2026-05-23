import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.validator import is_valid_email, is_valid_phone

def test_valid_email_com():
    assert is_valid_email("user@example.com") is True

def test_valid_email_org():
    assert is_valid_email("admin@site.org") is True

def test_valid_email_long_tld():
    assert is_valid_email("user@example.info") is True

def test_invalid_email_no_at():
    assert is_valid_email("notanemail") is False

def test_valid_phone_dashes():
    assert is_valid_phone("123-456-7890") is True

def test_valid_phone_parens():
    assert is_valid_phone("(123) 456-7890") is True

def test_invalid_phone():
    assert is_valid_phone("12345") is False
