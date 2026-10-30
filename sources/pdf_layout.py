"""Join wrapped full-width prose blocks only when PDF geometry supports continuity."""
import re
from src.quality import clean

def join_wrapped_blocks(blocks, page_width):
    result = []
    boundary = True
    for block in blocks:
        if len(block) < 5:
            boundary = True
            continue
        current = list(block[:5])
        raw = str(current[4])
        if not clean(raw):
            boundary = True
            continue
        previous = result[-1] if result else None
        first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
        last_line = next((line.strip() for line in reversed(str(previous[4]).splitlines()) if line.strip()), "") if previous else ""
        can_join = (
            previous is not None and not boundary
            and abs(current[0] - previous[0]) <= 3
            and -1 <= current[1] - previous[3] <= 12
            and previous[2] - previous[0] >= page_width * 0.55
            and len(last_line) >= 55
            and not re.search(r"[.!?:]\s*$", last_line)
            and not re.match(r"(?:\d+[.)]\s|Sub:|Pursuant\b|In continuation\b|With reference\b|Dear\b|Sir\s*/|Yours\b|Thanking\b)", first_line, re.I)
        )
        if can_join:
            previous[2] = max(previous[2], current[2])
            previous[3] = current[3]
            previous[4] = str(previous[4]).rstrip() + "\n" + raw.lstrip()
        else:
            result.append(current)
        boundary = False
    return result


def join_bullet_continuations(blocks, page_width):
    """Join a hanging-indent bullet within its column; ignore other-column charts."""
    rows = [list(b[:5]) for b in blocks if len(b) >= 5]
    used = set()
    result = []
    for index, row in enumerate(rows):
        if index in used:
            continue
        text = str(row[4]).lstrip()
        if not re.match(r'^[•▪\uf0b7]\s*', text) or row[2] - row[0] < page_width * 0.30:
            result.append(row)
            continue
        anchor_left, anchor_right = row[0], row[2]
        indent = None
        for _ in range(8):
            if re.search(r'[.!?:]\s*$', str(row[4])):
                break
            candidates = []
            for j, nxt in enumerate(rows):
                if j == index or j in used or not clean(nxt[4]):
                    continue
                gap = nxt[1] - row[3]
                expected_left = anchor_left + 14 if indent is None else indent
                if not (-0.5 <= gap <= 8 and abs(nxt[0] - expected_left) <= (8 if indent is None else 3)):
                    continue
                if nxt[2] > anchor_right + 3 or nxt[2] - nxt[0] < 40:
                    continue
                if re.match(r'^\s*(?:[•▪\uf0b7]|\d+[.)]\s)', str(nxt[4])):
                    continue
                # Any intervening block in this column, including an excluded
                # table, is a boundary. Blocks wholly in another column are not.
                if any(k not in used and k not in (index, j)
                       and b[0] < anchor_right and b[2] > anchor_left
                       and b[1] >= row[3] - 0.5 and b[3] <= nxt[1] + 0.5
                       for k, b in enumerate(rows)):
                    continue
                candidates.append((j, nxt))
            if len(candidates) != 1:
                break
            j, nxt = candidates[0]
            used.add(j)
            indent = nxt[0]
            row[3] = nxt[3]
            row[4] = str(row[4]).rstrip() + '\n' + str(nxt[4]).lstrip()
        result.append(row)
    return result
