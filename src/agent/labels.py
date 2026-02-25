"""Deterministic UUID → human-readable label mapping.

Algorithm adapted from humanhash (public domain), ported from
CollabBoard's src/ai/labels.js.

Compresses 16 UUID bytes to 3 via XOR, maps each to a word from a
256-word list.  Result: 3 words, 3 tokens, deterministic.

Collision rate: ~1 in 16.7M (256^3) — negligible for patient-scoped
resource sets (typically 10–50 resources).

Usage in the agent loop:
  - fhir_read results → compute labels for every resource UUID
  - Inject label→UUID table into system prompt context section
  - Claude uses labels in DSL (src="tango golf potato")
  - _build_manifest resolves labels back to UUIDs before storing
"""

from __future__ import annotations

import re

WORDLIST = [
    "ack", "alabama", "alanine", "alaska", "alpha", "angel", "apart", "april",
    "arizona", "arkansas", "artist", "asparagus", "aspen", "august", "autumn",
    "avocado", "bacon", "bakerloo", "batman", "beer", "berlin", "beryllium",
    "black", "blossom", "blue", "bluebird", "bravo", "bulldog", "burger",
    "butter", "california", "carbon", "cardinal", "carolina", "carpet", "cat",
    "ceiling", "charlie", "chicken", "coffee", "cola", "cold", "colorado",
    "comet", "connecticut", "crazy", "cup", "dakota", "december", "delaware",
    "delta", "diet", "don", "double", "early", "earth", "east", "echo",
    "edward", "eight", "eighteen", "eleven", "emma", "enemy", "equal",
    "failed", "fanta", "fifteen", "fillet", "finch", "fish", "five", "fix",
    "floor", "florida", "football", "four", "fourteen", "foxtrot", "freddie",
    "friend", "fruit", "gee", "georgia", "glucose", "golf", "green", "grey",
    "hamper", "happy", "harry", "hawaii", "helium", "high", "hot", "hotel",
    "hydrogen", "idaho", "illinois", "india", "indigo", "ink", "iowa",
    "island", "item", "jersey", "jig", "johnny", "juliet", "july", "jupiter",
    "kansas", "kentucky", "kilo", "king", "kitten", "lactose", "lake", "lamp",
    "lemon", "leopard", "lima", "lion", "lithium", "london", "louisiana",
    "low", "magazine", "magnesium", "maine", "mango", "march", "mars",
    "maryland", "massachusetts", "may", "mexico", "michigan", "mike",
    "minnesota", "mirror", "mississippi", "missouri", "mobile", "mockingbird",
    "monkey", "montana", "moon", "mountain", "muppet", "music", "nebraska",
    "neptune", "network", "nevada", "nine", "nineteen", "nitrogen", "north",
    "november", "nuts", "october", "ohio", "oklahoma", "one", "orange",
    "oranges", "oregon", "oscar", "oven", "oxygen", "papa", "paris", "pasta",
    "pennsylvania", "pip", "pizza", "pluto", "potato", "princess", "purple",
    "quebec", "queen", "quiet", "red", "river", "robert", "robin", "romeo",
    "rugby", "sad", "salami", "saturn", "september", "seven", "seventeen",
    "shade", "sierra", "single", "sink", "six", "sixteen", "skylark", "snake",
    "social", "sodium", "solar", "south", "spaghetti", "speaker", "spring",
    "stairway", "steak", "stream", "summer", "sweet", "table", "tango", "ten",
    "tennessee", "tennis", "texas", "thirteen", "three", "timing", "triple",
    "twelve", "twenty", "two", "uncle", "undress", "uniform", "uranus", "utah",
    "vegan", "venus", "vermont", "victor", "video", "violet", "virginia",
    "washington", "west", "whiskey", "white", "william", "winner", "winter",
    "wisconsin", "wolfram", "wyoming", "xray", "yankee", "yellow", "zebra",
    "zulu",
]

_WORDS = 3
_SEPARATOR = " "
_MAX_LABEL_WORD_LEN = 8

_UUID_RE = re.compile(r"^[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}$", re.I)


def _compress(byte_values: list[int], target: int) -> list[int]:
    """XOR-compress a byte list down to *target* bytes."""
    seg_size = len(byte_values) // target
    result: list[int] = []
    for i in range(target):
        start = i * seg_size
        end = len(byte_values) if i == target - 1 else start + seg_size
        xor = 0
        for j in range(start, end):
            xor ^= byte_values[j]
        result.append(xor)
    return result


def uuid_to_label(uuid: str) -> str:
    """Convert a UUID string to a deterministic 3-word label.

    >>> uuid_to_label("bbb13f7a-966e-4c7c-aea5-4bac3ce98505")
    'tango golf potato'
    """
    hex_str = uuid.replace("-", "")
    byte_values = [int(hex_str[i : i + 2], 16) for i in range(0, len(hex_str), 2)]
    words = (_short_word(WORDLIST[b]) for b in _compress(byte_values, _WORDS))
    return _SEPARATOR.join(words)


def _short_word(word: str) -> str:
    """Bound label length so labels stay shorter than UUID strings."""
    if len(word) <= _MAX_LABEL_WORD_LEN:
        return word
    return word[:_MAX_LABEL_WORD_LEN]


def is_label(value: str) -> bool:
    """Return True if *value* looks like a 3-word label rather than a UUID."""
    parts = value.strip().split()
    return len(parts) == _WORDS and all(p.isalpha() for p in parts)


def is_uuid(value: str) -> bool:
    """Return True if *value* looks like a UUID (with or without dashes)."""
    return bool(_UUID_RE.match(value.strip()))


class LabelRegistry:
    """Bidirectional label ↔ UUID registry for a session.

    Built from fhir_read results.  Injected into the system prompt so
    Claude can use short labels.  Resolves labels back to UUIDs in
    _build_manifest.

    Collision handling: if two UUIDs map to the same label, both are
    stored.  resolve() returns an error with the colliding UUIDs so the
    agent can fall back to raw UUIDs for those resources.
    """

    def __init__(self) -> None:
        self._uuid_to_label: dict[str, str] = {}
        self._label_to_uuids: dict[str, list[str]] = {}

    def register(self, uuid: str, resource_type: str | None = None) -> str:
        """Register a UUID and return its label.

        If the UUID is already registered, returns the existing label.
        """
        uuid = uuid.strip()
        if uuid in self._uuid_to_label:
            return self._uuid_to_label[uuid]

        label = uuid_to_label(uuid)
        self._uuid_to_label[uuid] = label
        self._label_to_uuids.setdefault(label, []).append(uuid)
        return label

    def register_bundle(self, bundle: dict) -> None:
        """Register all resource UUIDs from a FHIR Bundle response."""
        for entry in bundle.get("entry", []):
            resource = entry.get("resource", {})
            resource_id = resource.get("id")
            resource_type = resource.get("resourceType")
            if resource_id and is_uuid(resource_id):
                self.register(resource_id, resource_type)

    def resolve(self, value: str) -> dict:
        """Resolve a label or UUID to a concrete UUID.

        Returns:
            {"ok": True, "uuid": "..."} on success
            {"ok": False, "error": "...", "matches": [...]} on collision
            {"ok": False, "error": "..."} if not found

        Accepts:
            - Raw UUID (returned as-is if registered, or passed through)
            - 3-word label
            - "ResourceType/label" (e.g., "Encounter/tango golf potato")
        """
        value = value.strip()

        # Direct UUID match
        if is_uuid(value):
            return {"ok": True, "uuid": value}

        # Try as label
        label = value.lower()
        uuids = self._label_to_uuids.get(label, [])
        if len(uuids) == 1:
            return {"ok": True, "uuid": uuids[0]}
        if len(uuids) > 1:
            return {
                "ok": False,
                "error": f'Multiple resources match label "{value}".',
                "matches": uuids,
            }

        return {"ok": False, "error": f'Resource "{value}" not found.'}

    def resolve_reference(self, ref: str) -> dict:
        """Resolve a FHIR reference that may use a label.

        Handles both "ResourceType/uuid" and "ResourceType/label words".
        E.g., "Encounter/tango golf potato" → "Encounter/993da0c4-..."
        """
        ref = ref.strip()

        # Split on first / to get ResourceType
        slash_idx = ref.find("/")
        if slash_idx == -1:
            # No slash — treat as bare label or UUID
            result = self.resolve(ref)
            if result["ok"]:
                return {"ok": True, "reference": result["uuid"]}
            return result

        resource_type = ref[:slash_idx]
        identifier = ref[slash_idx + 1 :]

        result = self.resolve(identifier)
        if result["ok"]:
            return {"ok": True, "reference": f"{resource_type}/{result['uuid']}"}
        return result

    def has_collisions(self, label: str) -> bool:
        """Return True if a label maps to more than one UUID."""
        return len(self._label_to_uuids.get(label.lower(), [])) > 1

    def get_label(self, uuid: str) -> str | None:
        """Return the label for a UUID, or None if not registered."""
        return self._uuid_to_label.get(uuid.strip())

    def format_context_table(self) -> str:
        """Format the registry as a compact table for the system prompt.

        Lists each resource with its label for Claude to use in DSL
        references.  Colliding labels are marked so Claude knows to use
        the full UUID instead.
        """
        if not self._uuid_to_label:
            return ""

        lines = ["## Resource Labels (use these instead of UUIDs)"]
        # Group by label to detect collisions
        for label, uuids in sorted(self._label_to_uuids.items()):
            if len(uuids) == 1:
                lines.append(f"- {label} → {uuids[0]}")
            else:
                # Collision — tell Claude to use full UUIDs
                lines.append(f"- {label} → COLLISION, use full UUID:")
                for uuid in uuids:
                    lines.append(f"  - {uuid}")

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._uuid_to_label)
