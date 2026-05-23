def word_frequency(text: str) -> dict:
    """Return a dict mapping each word to its frequency in text (case-insensitive)."""
    words = text.lower().split()
    freq: dict = {}
    for word in words:
        clean = word.strip(".,!?;:'\"")
        if clean:
            freq[clean] = freq.get(clean, 0) + 1
    return freq


def longest_common_prefix(strings: list) -> str:
    """Return the longest common prefix of a list of strings."""
    if not strings:
        return ""
    prefix = strings[0]
    for s in strings[1:]:
        while not s.startswith(prefix):
            prefix = prefix[:-1]
            if not prefix:
                return ""
    return prefix


def reverse_words(sentence: str) -> str:
    """Reverse the order of words in a sentence, preserving spacing."""
    words = sentence.split(" ")
    return " ".join(words)   # BUG: missing reversed() call
