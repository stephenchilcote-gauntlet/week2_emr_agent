"""Bijective UUID ↔ word-encoded identifier mapping.

Encodes 128-bit UUIDs as 10 words from a 2^14 (16384) wordlist.
Every word is a single token in cl100k_base (the tokenizer used by
Claude and GPT-4), so a word-ID costs exactly 10 tokens — roughly
half the ~23 tokens a dashed UUID costs.

The encoding is fully bijective: no registry, no collisions, no
session state.  ``uuid_to_words(words_to_uuid(x)) == x`` for all
valid inputs.

Bit budget: 10 words × 14 bits/word = 140 bits capacity for 128 bits.
The top 12 bits of word 0 are always zero (UUID fits in 128 bits).
"""

from __future__ import annotations

import re
from pathlib import Path

_WORDLIST_PATH = Path(__file__).parent / "wordlist.txt"
_BITS_PER_WORD = 14
_N_WORDS = 10
_WORDLIST_SIZE = 1 << _BITS_PER_WORD  # 16384
_MASK = _WORDLIST_SIZE - 1  # 0x3FFF

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.I,
)
_UUID_STRICT_RE = re.compile(
    r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$",
    re.I,
)

# ---------------------------------------------------------------------------
# Wordlist loading
# ---------------------------------------------------------------------------

def _load_wordlist() -> tuple[list[str], dict[str, int]]:
    words = _WORDLIST_PATH.read_text().splitlines()
    if len(words) != _WORDLIST_SIZE:
        raise RuntimeError(
            f"Wordlist has {len(words)} entries, expected {_WORDLIST_SIZE}"
        )
    index = {w: i for i, w in enumerate(words)}
    return words, index


WORDLIST, _WORD_INDEX = _load_wordlist()


# ---------------------------------------------------------------------------
# Core encode / decode
# ---------------------------------------------------------------------------

def uuid_to_words(uuid_str: str) -> str:
    """Encode a UUID as 10 space-separated words.  Bijective.

    >>> uuid_to_words("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")
    '...'
    >>> words_to_uuid(uuid_to_words("bbb13f7a-966e-4c7c-aea5-4bac3ce98505"))
    'bbb13f7a-966e-4c7c-aea5-4bac3ce98505'
    """
    n = int(uuid_str.replace("-", ""), 16)
    parts: list[str] = []
    for _ in range(_N_WORDS):
        parts.append(WORDLIST[n & _MASK])
        n >>= _BITS_PER_WORD
    parts.reverse()
    return " ".join(parts)


def words_to_uuid(word_str: str) -> str:
    """Decode 10 space-separated words back to a dashed UUID.  Bijective."""
    parts = word_str.strip().split()
    if len(parts) != _N_WORDS:
        raise ValueError(
            f"Expected {_N_WORDS} words, got {len(parts)}: {word_str!r}"
        )
    n = 0
    for word in parts:
        idx = _WORD_INDEX.get(word)
        if idx is None:
            raise ValueError(f"Word {word!r} not in wordlist")
        n = (n << _BITS_PER_WORD) | idx
    n &= (1 << 128) - 1
    h = f"{n:032x}"
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def is_word_id(value: str) -> bool:
    """Return True if *value* looks like a 10-word encoded UUID."""
    parts = value.strip().split()
    if len(parts) != _N_WORDS:
        return False
    return all(p in _WORD_INDEX for p in parts)


def is_uuid(value: str) -> bool:
    """Return True if *value* looks like a UUID (with or without dashes)."""
    return bool(_UUID_STRICT_RE.match(value.strip()))


# ---------------------------------------------------------------------------
# Bulk replacement in JSON / text
# ---------------------------------------------------------------------------

def replace_uuids_with_words(text: str) -> str:
    """Replace all dashed UUIDs in a string with their word encodings."""
    return _UUID_RE.sub(lambda m: uuid_to_words(m.group()), text)


def replace_words_with_uuids(text: str) -> str:
    """Replace all 10-word IDs in a string with dashed UUIDs.

    Scans for sequences of 10 consecutive wordlist words separated by
    spaces.  Only replaces sequences where all 10 words are in the
    wordlist.
    """
    # Build a regex that matches 10 wordlist words in a row.
    # This is cheaper than scanning every 10-word window.
    words_set = set(WORDLIST)
    tokens = text.split()
    result: list[str] = []
    i = 0
    while i < len(tokens):
        if (
            i + _N_WORDS <= len(tokens)
            and all(tokens[i + j] in words_set for j in range(_N_WORDS))
        ):
            word_id = " ".join(tokens[i : i + _N_WORDS])
            try:
                result.append(words_to_uuid(word_id))
            except ValueError:
                result.append(tokens[i])
                i += 1
                continue
            i += _N_WORDS
        else:
            result.append(tokens[i])
            i += 1
    return " ".join(result)


def resolve_identifier(value: str) -> str:
    """Resolve a word-ID or UUID string to a UUID.

    Accepts raw UUIDs (returned as-is), 10-word encoded UUIDs
    (decoded back to UUID), or other identifiers (returned as-is).
    """
    value = value.strip()
    if is_uuid(value):
        return value
    if is_word_id(value):
        return words_to_uuid(value)
    return value


def resolve_reference(ref: str) -> str:
    """Resolve a FHIR-style reference that may use word-IDs.

    Handles "ResourceType/uuid" and "ResourceType/word1 word2 ...".
    """
    ref = ref.strip()
    slash_idx = ref.find("/")
    if slash_idx == -1:
        return resolve_identifier(ref)

    resource_type = ref[:slash_idx]
    identifier = ref[slash_idx + 1:]

    resolved = resolve_identifier(identifier)
    return f"{resource_type}/{resolved}"
