"""
Edit Fuzzy Matching for Limitcode.
9 replacer strategies with Levenshtein similarity scoring.

Strategies (in order of attempt):
1. SimpleReplacer - Exact string match
2. LineTrimmedReplacer - Match with per-line whitespace trimmed
3. BlockAnchorReplacer - First/last lines as anchors, Levenshtein for middle
4. WhitespaceNormalizedReplacer - Normalize all whitespace runs
5. IndentationFlexibleReplacer - Strip min indent before comparing
6. EscapeNormalizedReplacer - Unescape \n, \t, etc. before matching
7. TrimmedBoundaryReplacer - Trim leading/trailing whitespace from entire block
8. ContextAwareReplacer - First/last lines as context, 50% middle line match
"""

from typing import Generator, List, Tuple, Optional
import re


def levenshtein(a: str, b: str) -> int:
    """
    Compute the Levenshtein distance between two strings.
    Returns the minimum number of single-character edits
    (insertions, deletions, substitutions) to transform a into b.
    """
    if len(a) < len(b):
        return levenshtein(b, a)
    
    if len(b) == 0:
        return len(a)
    
    previous_row = list(range(len(b) + 1))
    
    for i, ca in enumerate(a):
        current_row = [i + 1]
        for j, cb in enumerate(b):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (ca != cb)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    
    return previous_row[-1]


def similarity(a: str, b: str) -> float:
    """
    Compute similarity ratio between two strings (0.0 to 1.0).
    1.0 means identical, 0.0 means completely different.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    
    dist = levenshtein(a, b)
    max_len = max(len(a), len(b))
    return 1.0 - (dist / max_len)


def normalize_line_endings(text: str) -> str:
    """Convert all line endings to \\n."""
    return text.replace("\r\n", "\n")


def detect_line_ending(text: str) -> str:
    """Detect the line ending used in text."""
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def convert_line_endings(text: str, target: str) -> str:
    """Convert text to use the target line ending."""
    normalized = text.replace("\r\n", "\n")
    if target == "\r\n":
        return normalized.replace("\n", "\r\n")
    return normalized


class MatchResult:
    """A single match found by a replacer strategy."""
    
    def __init__(self, start: int, end: int, strategy: str):
        self.start = start
        self.end = end
        self.strategy = strategy
    
    def __repr__(self):
        return f"MatchResult({self.start}-{self.end}, {self.strategy})"


def simple_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """Strategy 1: Exact string match."""
    if not find:
        return

    start = 0
    while True:
        idx = content.find(find, start)
        if idx < 0:
            break
        yield MatchResult(idx, idx + len(find), "simple")
        start = idx + len(find)


def line_trimmed_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """Strategy 2: Match line-by-line with both sides trimmed."""
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    find_trimmed = [l.strip() for l in find_lines]
    
    # Skip empty find
    if not any(find_trimmed):
        return
    
    for i in range(len(content_lines) - len(find_lines) + 1):
        candidate_lines = content_lines[i:i + len(find_lines)]
        candidate_trimmed = [l.strip() for l in candidate_lines]
        
        if candidate_trimmed == find_trimmed:
            # Calculate the actual character positions
            start = len("\n".join(content_lines[:i]))
            if i > 0:
                start += 1  # Account for the newline
            end = len("\n".join(content_lines[:i + len(find_lines)]))
            yield MatchResult(start, end, "line_trimmed")


def block_anchor_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """
    Strategy 3: First and last lines as anchors, Levenshtein for middle.
    Uses similarity thresholds to accept fuzzy matches.
    """
    SINGLE_THRESHOLD = 0.0
    MULTI_THRESHOLD = 0.3
    
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    
    if len(find_lines) <= 2:
        # Too short for anchor strategy, fall through
        return
    
    first_line = find_lines[0]
    last_line = find_lines[-1]
    middle_lines = find_lines[1:-1]
    
    for i in range(len(content_lines) - len(find_lines) + 1):
        candidate_lines = content_lines[i:i + len(find_lines)]
        
        # Check anchors
        if candidate_lines[0].strip() != first_line.strip():
            continue
        if candidate_lines[-1].strip() != last_line.strip():
            continue
        
        # Check middle lines with Levenshtein
        candidate_middle = candidate_lines[1:-1]
        
        if not middle_lines:
            # No middle lines, anchors matched
            start = len("\n".join(content_lines[:i]))
            if i > 0:
                start += 1
            end = len("\n".join(content_lines[:i + len(find_lines)]))
            yield MatchResult(start, end, "block_anchor")
            continue
        
        # Score middle lines
        similarities = []
        for cm, fm in zip(candidate_middle, middle_lines):
            sim = similarity(cm.strip(), fm.strip())
            similarities.append(sim)
        
        avg_similarity = sum(similarities) / len(similarities)
        
        # Determine threshold
        threshold = SINGLE_THRESHOLD if len(similarities) == 1 else MULTI_THRESHOLD
        
        if avg_similarity >= threshold:
            start = len("\n".join(content_lines[:i]))
            if i > 0:
                start += 1
            end = len("\n".join(content_lines[:i + len(find_lines)]))
            yield MatchResult(start, end, "block_anchor")


def whitespace_normalized_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """Strategy 4: Normalize all whitespace runs to single spaces."""
    content_normalized = re.sub(r'\s+', ' ', content).strip()
    find_normalized = re.sub(r'\s+', ' ', find).strip()
    
    idx = content_normalized.find(find_normalized)
    if idx >= 0:
        # Map back to original positions
        # This is approximate - find the best matching region in original
        original_start = _map_normalized_to_original(content, find_normalized, idx)
        if original_start is not None:
            # Find the end by scanning forward
            original_end = original_start
            words = find_normalized.split()
            pos = original_start
            for word in words:
                word_pos = content.find(word, pos)
                if word_pos >= 0:
                    original_end = word_pos + len(word)
                    pos = original_end
            yield MatchResult(original_start, original_end, "whitespace_normalized")


def _map_normalized_to_original(content: str, normalized_find: str, normalized_idx: int) -> Optional[int]:
    """Map a position in normalized text back to original text."""
    content_normalized = re.sub(r'\s+', ' ', content)
    
    # Count non-whitespace chars before the match in normalized
    chars_before = len(content_normalized[:normalized_idx].replace(' ', ''))
    
    # Find the same position in original
    count = 0
    for i, ch in enumerate(content):
        if not ch.isspace():
            count += 1
        if count == chars_before:
            return i
    return None


def indentation_flexible_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """Strategy 5: Strip minimum indentation before comparing."""
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    
    # Calculate min indent of find
    find_indents = []
    for line in find_lines:
        stripped = line.lstrip()
        if stripped:
            find_indents.append(len(line) - len(stripped))
    
    if not find_indents:
        return
    
    min_find_indent = min(find_indents)
    find_stripped = []
    for line in find_lines:
        stripped = line.lstrip()
        if stripped:
            find_stripped.append(line[min_find_indent:])
        else:
            find_stripped.append(line)
    
    # Try to match in content
    for i in range(len(content_lines) - len(find_lines) + 1):
        candidate_lines = content_lines[i:i + len(find_lines)]
        
        # Calculate min indent of candidate
        cand_indents = []
        for line in candidate_lines:
            stripped = line.lstrip()
            if stripped:
                cand_indents.append(len(line) - len(stripped))
        
        if not cand_indents:
            continue
        
        min_cand_indent = min(cand_indents)
        cand_stripped = []
        for line in candidate_lines:
            stripped = line.lstrip()
            if stripped:
                cand_stripped.append(line[min_cand_indent:])
            else:
                cand_stripped.append(line)
        
        if cand_stripped == find_stripped:
            start = len("\n".join(content_lines[:i]))
            if i > 0:
                start += 1
            end = len("\n".join(content_lines[:i + len(find_lines)]))
            yield MatchResult(start, end, "indentation_flexible")


def escape_normalized_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """Strategy 6: Unescape \\n, \\t, \\\", etc. before matching."""
    def unescape(s: str) -> str:
        s = s.replace('\\n', '\n')
        s = s.replace('\\t', '\t')
        s = s.replace('\\"', '"')
        s = s.replace("\\'", "'")
        s = s.replace('\\\\', '\\')
        return s
    
    find_unescaped = unescape(find)
    
    idx = content.find(find_unescaped)
    if idx >= 0:
        yield MatchResult(idx, idx + len(find_unescaped), "escape_normalized")


def trimmed_boundary_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """Strategy 7: Trim leading/trailing whitespace from entire block."""
    find_trimmed = find.strip()
    
    if not find_trimmed:
        return
    
    idx = content.find(find_trimmed)
    if idx >= 0:
        yield MatchResult(idx, idx + len(find_trimmed), "trimmed_boundary")


def context_aware_replacer(content: str, find: str) -> Generator[MatchResult, None, None]:
    """
    Strategy 8: First/last lines as context anchors,
    requires at least 50% of middle lines to match when trimmed.
    """
    content_lines = content.split("\n")
    find_lines = find.split("\n")
    
    if len(find_lines) < 3:
        return
    
    first_line = find_lines[0].strip()
    last_line = find_lines[-1].strip()
    middle_lines = [l.strip() for l in find_lines[1:-1]]
    
    for i in range(len(content_lines) - len(find_lines) + 1):
        candidate_lines = content_lines[i:i + len(find_lines)]
        
        # Check context anchors
        if candidate_lines[0].strip() != first_line:
            continue
        if candidate_lines[-1].strip() != last_line:
            continue
        
        # Check middle lines - 50% must match
        candidate_middle = [l.strip() for l in candidate_lines[1:-1]]
        
        if not middle_lines:
            start = len("\n".join(content_lines[:i]))
            if i > 0:
                start += 1
            end = len("\n".join(content_lines[:i + len(find_lines)]))
            yield MatchResult(start, end, "context_aware")
            continue
        
        matches = sum(1 for cm, fm in zip(candidate_middle, middle_lines) if cm == fm)
        required = len(middle_lines) * 0.5
        
        if matches >= required:
            start = len("\n".join(content_lines[:i]))
            if i > 0:
                start += 1
            end = len("\n".join(content_lines[:i + len(find_lines)]))
            yield MatchResult(start, end, "context_aware")


def find_edit(content: str, find: str, replace_all: bool = False) -> List[MatchResult]:
    """
    Try all 8 replacer strategies in order.
    
    Returns a list of unique match results.
    If replace_all is False, returns at most 1 match.
    If replace_all is True, returns all unique matches.
    """
    results = []
    seen_positions = set()
    
    # All strategies in order
    strategies = [
        simple_replacer,
        line_trimmed_replacer,
        block_anchor_replacer,
        whitespace_normalized_replacer,
        indentation_flexible_replacer,
        escape_normalized_replacer,
        trimmed_boundary_replacer,
        context_aware_replacer,
    ]
    
    for strategy in strategies:
        for match in strategy(content, find):
            pos_key = (match.start, match.end)
            if pos_key not in seen_positions:
                seen_positions.add(pos_key)
                results.append(match)
                
                if not replace_all and len(results) > 0:
                    return results  # Return first match found
    
    return results


def apply_edit(content: str, find: str, replace: str, replace_all: bool = False) -> Tuple[str, str]:
    """
    Apply an edit to content using fuzzy matching.
    
    Args:
        content: The original file content
        find: The text to find (may be imprecise)
        replace: The replacement text
        replace_all: Whether to replace all occurrences
    
    Returns:
        Tuple of (new_content, strategy_used)
    
    Raises:
        ValueError if no match is found
    """
    # Normalize line endings for matching
    original_ending = detect_line_ending(content)
    content_normalized = normalize_line_endings(content)
    find_normalized = normalize_line_endings(find)
    replace_normalized = normalize_line_endings(replace)
    
    matches = find_edit(content_normalized, find_normalized, replace_all)
    
    if not matches:
        raise ValueError(
            f"Could not find the text to replace. "
            f"The edit tool tried 8 different matching strategies but none found a match. "
            f"Make sure the text you're trying to replace exists in the file."
        )
    
    # Apply replacements (in reverse order to preserve positions)
    new_content = content_normalized
    for match in reversed(matches):
        new_content = new_content[:match.start] + replace_normalized + new_content[match.end:]
    
    # Restore original line endings
    new_content = convert_line_endings(new_content, original_ending)
    
    return new_content, matches[0].strategy
