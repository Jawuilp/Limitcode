"""Evidence-based gate for unicode escape decoding in file tools (issue #9).

Some providers re-serialize tool-call arguments so that intended Unicode
characters arrive as literal escape sequences (e.g. ``M\\u00f3dulo`` instead of
``Módulo``). Limitcode historically repaired that by decoding every ``\\uXXXX``
sequence in edit_file/write_to_file arguments — which corrupts content where
the model deliberately wrote literal escapes (JSON i18n files, regexes, JS
strings): the issue reported as Jawuilp/Limitcode#9.

This gate keeps the repair but requires evidence before applying it:

* ``edit_file`` tries the raw arguments first. Only when the raw match fails
  and a decoded retry succeeds do we know the provider is corrupting; the
  tool then calls ``mark_corruption_proven()``.
* Once proven, ``write_to_file`` decodes its content through
  ``prepare_write_content()``. Until then writes are passed through verbatim.

The decision lives here (imported by the agent), not inside lib/agent.py or
the individual tools, so it can be unit-tested in isolation.
"""

from ..tools.base import decode_unicode_escapes


class EscapeGate:
    """Session-scoped switch for unicode-escape decoding.

    One instance per Agent: it carries the evidence learned from edit_file
    fallbacks across all tool calls of the session.
    """

    def __init__(self):
        self._corruption_proven = False

    @property
    def corruption_proven(self) -> bool:
        """True once a raw-vs-decoded mismatch proved provider corruption."""
        return self._corruption_proven

    def mark_corruption_proven(self) -> None:
        """Record that decoded arguments were required to match the file."""
        self._corruption_proven = True

    def prepare_write_content(self, content):
        """Return content for write_to_file: decoded only with proven corruption."""
        if self._corruption_proven and isinstance(content, str):
            return decode_unicode_escapes(content)
        return content
