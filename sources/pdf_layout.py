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
            and not re.match(r"(?:\d+[.)]\s|Sub:|Dear\b|Sir\s*/|Yours\b|Thanking\b)", first_line, re.I)
        )
        if can_join:
            previous[2] = max(previous[2], current[2])
            previous[3] = current[3]
            previous[4] = str(previous[4]).rstrip() + "\n" + raw.lstrip()
        else:
            result.append(current)
        boundary = False
    return result
