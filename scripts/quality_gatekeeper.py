import os
import re
import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Tuple, Dict, Any

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)

class QualityGatekeeper:
    def __init__(self, state_file: str = "state/posted_history.json"):
        self.state_file = state_file
        self.posted_history = self._load_history()

    def _load_history(self) -> dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"last_updated": datetime.now(IST).isoformat(), "posts": []}

    def _save_history(self):
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(self.posted_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save posted history: {e}")

    @staticmethod
    def extract_numbers(text: str) -> list[str]:
        """Extracts all numeric tokens, percentages, and rupee quantities."""
        # Clean currency symbols and commas
        tokens = re.findall(r'(?:₹|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:cr(?:ore)?|lakh|%|bps|gw|mw)?', text, re.IGNORECASE)
        normalized = []
        for t in tokens:
            cleaned = t.strip().lower().replace(",", "").replace("₹", "").replace("rs.", "").strip()
            if cleaned and any(c.isdigit() for c in cleaned):
                normalized.append(cleaned)
        return normalized

    def audit_numbers(self, raw_source: str, drafted_post: str) -> Tuple[bool, list[str]]:
        """
        Deterministic Numeric Audit:
        Checks if every number in drafted_post exists verbatim in raw_source.
        Returns (is_passed, ungrounded_numbers).
        """
        raw_nums = set(self.extract_numbers(raw_source))
        draft_nums = self.extract_numbers(drafted_post)
        
        ungrounded = []
        for dn in draft_nums:
            # Extract just digits and decimal for base check
            base_num = re.findall(r'\d+(?:\.\d+)?', dn)
            if not base_num:
                continue
            bn = base_num[0]
            # Check if base number or full unit exists in raw source
            if not any(bn in rn for rn in raw_nums) and bn not in raw_source:
                ungrounded.append(dn)
                
        is_passed = (len(ungrounded) == 0)
        return is_passed, ungrounded

    @staticmethod
    def normalize_company(company: str) -> str:
        if not company:
            return ""
        c = company.strip().upper()
        # Strip trailing exchange suffixes like -$ or -RE or LTD or LIMITED
        c = re.sub(r'[-\$]+$', '', c).strip()
        c = re.sub(r'\b(?:LTD|LIMITED|PVT|PRIVATE|CORP|CORPORATION|INC)\b', '', c, flags=re.I).strip()
        c = re.sub(r'[^A-Z0-9]', '', c)
        return c

    @staticmethod
    def normalize_text_key(text: str) -> str:
        if not text:
            return ""
        # Strip html tags
        t = re.sub(r'<[^>]+>', ' ', text)
        # Strip emojis and punctuation
        t = re.sub(r'[^\w\s]', ' ', t.lower())
        # Collapse whitespace
        return re.sub(r'\s+', ' ', t).strip()

    def is_duplicate_24h(self, company: str, category: str, text: str, url: str = "") -> bool:
        """
        24-Hour Semantic Deduplication:
        Checks state/posted_history.json by URL, normalized company, numbers, and text content.
        """
        now = datetime.now(IST)
        cutoff = now - timedelta(hours=24)
        
        # Clean expired items from history
        valid_posts = []
        for p in self.posted_history.get("posts", []):
            try:
                p_dt = datetime.fromisoformat(p.get("timestamp"))
                if p_dt > cutoff:
                    valid_posts.append(p)
            except Exception:
                pass
        self.posted_history["posts"] = valid_posts
        
        # Check 1: URL match (immediate duplicate for same exchange filing PDF or wire link)
        if url:
            for p in valid_posts:
                if p.get("url") and p.get("url") == url:
                    return True

        company_clean = self.normalize_company(company)
        current_numbers = set(self.extract_numbers(text))
        norm_text = self.normalize_text_key(text)
        
        for p in valid_posts:
            # Check direct normalized headline / text match
            p_norm = p.get("norm_headline") or self.normalize_text_key(p.get("headline", ""))
            if norm_text and p_norm:
                if norm_text[:80] in p_norm or p_norm[:80] in norm_text:
                    return True

            p_comp = self.normalize_company(p.get("company", ""))
            if company_clean and p_comp and company_clean == p_comp:
                p_nums = set(p.get("numbers", []))
                # If companies match and share key numbers
                if current_numbers and p_nums and bool(current_numbers & p_nums):
                    return True
                # Cross-exchange event matching: if both share event root (e.g. director, resignation, results)
                event_roots = [
                    'director', 'resignation', 'appointment', 'order', 'contract', 'result',
                    'q1', 'q2', 'q3', 'q4', 'bonus', 'dividend', 'split', 'allotment', 'rating',
                    'merger', 'acquisition', 'tenure', 'kmp'
                ]
                for ev in event_roots:
                    if ev in norm_text and ev in p_norm:
                        return True
                # If companies match and category matches, compare text overlap
                if p.get("category") == category:
                    if norm_text and p_norm and (norm_text[:40] in p_norm or p_norm[:40] in norm_text):
                        return True
                    if not current_numbers and not p_nums:
                        if norm_text[:30] and norm_text[:30] in p_norm:
                            return True
                    
        return False

    def record_posted(self, company: str, category: str, final_post: str, url: str, raw_text: str = ""):
        """Records successfully sent post into 24h state history."""
        nums = self.extract_numbers(final_post)
        first_line = final_post.splitlines()[0] if final_post else ""
        norm_head = self.normalize_text_key(first_line)
        if raw_text:
            norm_raw = self.normalize_text_key(raw_text)
            norm_head = f"{norm_head} {norm_raw[:100]}".strip()

        self.posted_history["posts"].append({
            "timestamp": datetime.now(IST).isoformat(),
            "company": company,
            "category": category,
            "numbers": nums,
            "headline": first_line,
            "norm_headline": norm_head,
            "url": url
        })
        self._save_history()

    @staticmethod
    def format_3line_post(company_or_body: str, action_headline: str, fact_details: str = "", context_line: str = "") -> str:
        """
        Enforces Benchmark Scannable Structure:
        Line 1 (Header): <b>COMPANY : ACTION HEADLINE</b>
        Followed by clean bullet points:
        ▪️ Fact 1
        ▪️ Fact 2
        (ZERO disclosure footers, ZERO boilerplate, tight single-spaced layout)
        """
        comp_tag = company_or_body.strip().upper()
        action_clean = action_headline.strip()
        
        # Strip common regulatory / exchange boilerplate prefixes from action
        action_clean = re.sub(
            r'^(?:Announcement\s+under\s+Regulation\s*\d+(?:\s*\([^)]+\))?|Intimation\s+under\s+Regulation\s*\d+(?:\s*\([^)]+\))?|Disclosure\s+under\s+Regulation\s*\d+(?:\s*\([^)]+\))?|General\s+Updates|Outcome\s+of\s+Board\s+Meeting|Compliances|We\s+wish\s+to\s+inform\s+you\s+that|This\s+is\s+to\s+inform\s+you\s+that)\s*[-–:]\s*',
            '',
            action_clean,
            flags=re.IGNORECASE
        ).strip(' -:–')
        
        # If action starts with "Pursuant to Regulation 30...", strip the entire clause
        action_clean = re.sub(r'^Pursuant\s+to\s+Regulation\s+\d+.*?(?:hereby\s+inform\s+that|intimates\s+that|regarding)\s*', '', action_clean, flags=re.IGNORECASE).strip(' -:–')
        
        # Avoid repeating company name if action starts with it
        if action_clean.upper().startswith(comp_tag):
            action_clean = action_clean[len(comp_tag):].strip(' -:–')
        
        # Clean trailing sources or urls
        action_clean = re.sub(r'\s*-\s*(?:The\s+Economic\s+Times|CNBC|Moneycontrol|NDTV|Business\s+Today|Reuters|Bloomberg|Taxo\s+Online).*$', '', action_clean, flags=re.IGNORECASE).strip()
        
        # If action is completely empty or just "DISCLOSURE ATTACHED", reject
        if not action_clean or re.match(r'^(?:DISCLOSURE\s+ATTACHED|ATTACHMENT|COVERING\s+LETTER|ENCLOSED|ANNEXURE)\.?$', action_clean, re.I):
            return ""

        header = f"<b>{comp_tag} : {action_clean.upper()}</b>"
        
        bullets = []
        if fact_details:
            clean_facts = fact_details.strip()
            norm_h = re.sub(r'[^\w]', '', action_clean.lower())
            norm_f = re.sub(r'[^\w]', '', clean_facts.lower())
            # Only add facts if not redundant with header
            if norm_h != norm_f and norm_h not in norm_f and norm_f not in norm_h:
                for fl in clean_facts.splitlines():
                    fl_clean = fl.strip()
                    if fl_clean and not any(bp in fl_clean.lower() for bp in [
                        'verified from', 'official disclosure', 'dear sir', 'please find enclosed',
                        'pursuant to regulation', 'forwarded herewith', 'we submit herewith', 'disclosure attached'
                    ]):
                        bullet_txt = re.sub(r'^[-•*●▪️\s]+', '', fl_clean).strip()
                        if len(bullet_txt) > 12:
                            bullets.append(f"▪️ {bullet_txt}")
        
        if bullets:
            return f"{header}\n" + "\n".join(bullets[:3])
        return header

    def generate_verified_post(self, item: dict) -> Tuple[Optional[str], float, dict]:
        """
        Full Gatekeeper Execution:
        1. Checks zero-substance boilerplate rejection.
        2. Checks 24h deduplication.
        3. Drafts post using deterministic fact extraction.
        4. Deterministic numeric audit.
        5. Zero disclosure footers.
        Returns (formatted_post, authenticity_score, audit_metadata).
        """
        company = item.get("company", "").strip() or "MARKET WIRE"
        category = item.get("source", "GENERAL")
        raw_text = item.get("raw_text", "").strip()
        url = item.get("url", "")
        
        # 1. Zero-substance boilerplate rejection
        boilerplate_patterns = [
            r'please\s+find\s+(?:the\s+)?(?:attach|enclosed|letter|same)',
            r'self\s+explanatory',
            r'please\s+refer\s+(?:the\s+)?(?:detailed\s+)?(?:disclosure|enclosed|file|attachment)',
            r'refer\s+(?:the\s+)?(?:enclosed\s+file|detailed\s+disclosure)',
            r'covering\s+letter',
            r'forwarded\s+herewith',
            r'we\s+submit\s+herewith',
            r'copy\s+of\s+letter\s+submitted',
            r'^disclosure\s+attached\.?$',
            r'^attachment\s+attached\.?$',
            r'as\s+per\s+(?:the\s+)?attached',
            r'enclosed\s+herewith',
            r'submission\s+of\s+intimation',
            r'pursuant\s+to\s+regulation\s+30\s+of\s+securities\s+and\s+exchange',
            r'loss\s+of\s+share\s+certificate',
            r'duplicate\s+share\s+certificate',
            r'newspaper\s+publication\s+prior\s+to',
        ]
        is_boilerplate = any(re.search(pat, raw_text, re.I) for pat in boilerplate_patterns)
        has_concrete_action = bool(re.search(
            r'\b(?:order|contract|awarded|result|q[1-4]|profit|loss|revenue|ebitda|margin|dividend|bonus|split|buyback|acquisition|stake|resigned|appointed|ceo|cfo|md|usfda|approval|capacity|expansion|patent|incorporation|subsidiary|ncd|allotment)\b',
            raw_text, re.I
        ))

        if is_boilerplate and not has_concrete_action:
            return None, 0.0, {"status": "REJECTED_BOILERPLATE", "reason": "Cover letter placeholder without corporate action"}

        # 2. Deduplication check (URL + Semantic 24h)
        if self.is_duplicate_24h(company, category, raw_text, url=url):
            return None, 0.0, {"status": "REJECTED_DUPLICATE", "reason": "Already posted within last 24 hours"}
            
        # 2. Extract facts deterministically
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        headline = item.get("headline", "").strip() or (lines[0] if lines else "")
        headline = re.sub(r'^(NEWS|ALERT|FLASH|BREAKING)\s*[:-]\s*', '', headline, flags=re.I).strip()
        
        # Build plain-English facts without LODR boilerplate
        body = item.get("body", "").strip()
        clean_body = re.sub(r'\b(?:pursuant to regulation \d+|we wish to inform that|this is to inform you that)\b.*', '', body, flags=re.I).strip()
        
        drafted_post = None

        # Assemble deterministically without disclosure footers
        fact_line = clean_body if (clean_body and len(clean_body) < 220) else ""
        drafted_post = self.format_3line_post(company, headline[:120], fact_line)

        # 3. Deterministic Numeric & Entity Audit
        is_passed, ungrounded = self.audit_numbers(raw_text, drafted_post)
        
        if not is_passed:
            logger.warning(f"HALLUCINATION_BLOCKED: Draft contained ungrounded numbers {ungrounded}")
            drafted_post = self.format_3line_post(company, headline[:120], "")
            is_passed, ungrounded = self.audit_numbers(raw_text, drafted_post)

        authenticity_score = 1.0 if is_passed else 0.5
        audit_meta = {
            "status": "APPROVED" if is_passed else "HALLUCINATION_BLOCKED",
            "verified_numbers": self.extract_numbers(drafted_post),
            "ungrounded_numbers": ungrounded,
            "authenticity_score": authenticity_score,
            "timestamp": datetime.now(IST).isoformat()
        }
        
        return drafted_post, authenticity_score, audit_meta
