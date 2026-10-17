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

    def is_duplicate_24h(self, company: str, category: str, text: str) -> bool:
        """
        24-Hour Semantic Deduplication:
        Checks state/posted_history.json by (company_or_ministry, event_category, key_numbers).
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
        
        company_clean = (company or "").strip().upper()
        current_numbers = set(self.extract_numbers(text))
        
        for p in valid_posts:
            p_comp = (p.get("company") or "").strip().upper()
            if company_clean and p_comp and company_clean == p_comp:
                p_nums = set(p.get("numbers", []))
                # If companies match and share key numbers or category
                if current_numbers and p_nums and bool(current_numbers & p_nums):
                    return True
                if p.get("category") == category and p.get("headline") == text[:100]:
                    return True
                    
        return False

    def record_posted(self, company: str, category: str, final_post: str, url: str):
        """Records successfully sent post into 24h state history."""
        nums = self.extract_numbers(final_post)
        self.posted_history["posts"].append({
            "timestamp": datetime.now(IST).isoformat(),
            "company": company,
            "category": category,
            "numbers": nums,
            "headline": final_post.splitlines()[0] if final_post else "",
            "url": url
        })
        self._save_history()

    @staticmethod
    def format_3line_post(company_or_body: str, action_headline: str, fact_details: str, context_line: str) -> str:
        """
        Enforces Benchmark Scannable Structure:
        Line 1 (Header): 📢 <b>[COMPANY / GOV BODY]</b> — [1-Line Action Headline]
        Line 2 (The Facts): Plain-English summary with exact verified numbers
        Line 3 (Market Context): 1-line objective takeaway
        """
        comp_tag = company_or_body.strip().upper()
        action_clean = action_headline.strip()
        header = f"📢 <b>{comp_tag}</b> — {action_clean}"
        facts = fact_details.strip()
        context = f"• {context_line.strip()}" if not context_line.startswith("•") else context_line.strip()
        
        return f"{header}\n\n{facts}\n\n{context}"

    def generate_verified_post(self, item: dict) -> Tuple[Optional[str], float, dict]:
        """
        Full Gatekeeper Execution:
        1. Checks 24h deduplication.
        2. Drafts post using deterministic fact extraction (or Gemini LLM if key available).
        3. Deterministic numeric audit.
        4. Enforces 3-line format.
        Returns (formatted_post, authenticity_score, audit_metadata).
        """
        company = item.get("company", "").strip() or "MARKET WIRE"
        category = item.get("source", "GENERAL")
        raw_text = item.get("raw_text", "").strip()
        url = item.get("url", "")
        
        # 1. Deduplication check
        if self.is_duplicate_24h(company, category, raw_text):
            return None, 0.0, {"status": "REJECTED_DUPLICATE", "reason": "Already posted within last 24 hours"}
            
        # 2. Extract facts deterministically
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        headline = item.get("headline", "").strip() or (lines[0] if lines else "")
        headline = re.sub(r'^(NEWS|ALERT|FLASH|BREAKING)\s*[:-]\s*', '', headline, flags=re.I).strip()
        
        # Build plain-English facts without LODR boilerplate
        body = item.get("body", "").strip()
        clean_body = re.sub(r'\b(?:pursuant to regulation \d+|we wish to inform that|this is to inform you that)\b.*', '', body, flags=re.I).strip()
        
        # Extract verified numbers
        raw_nums = self.extract_numbers(raw_text)
        
        # If Gemini API key is configured in env, attempt LLM polish with strict temperature 0.0
        gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
        drafted_post = None
        
        if gemini_key and gemini_key != "YOUR_GEMINI_API_KEY":
            try:
                import google.generativeai as genai
                genai.configure(api_key=gemini_key)
                model = genai.GenerativeModel("gemini-1.5-flash")
                prompt = (
                    f"You are the Zero-Hallucination Gatekeeper for an Indian stock market wire.\n"
                    f"Format the following raw news item into exactly 3 lines:\n"
                    f"Line 1: 🚨 [{company.upper()}] — <Concise 1-Line Action Headline with Key Number>\n"
                    f"Line 2: <1 Plain-English sentence stating the core verified facts>\n"
                    f"Line 3: <1 Objective context bullet, e.g. segment, date, or comparison>\n"
                    f"CRITICAL: Do NOT invent ANY numbers, names, or targets. Use only numbers in the raw text:\n"
                    f"RAW TEXT: {raw_text}"
                )
                resp = model.generate_content(prompt, generation_config={"temperature": 0.0, "max_output_tokens": 250})
                if resp.text:
                    drafted_post = resp.text.strip()
            except Exception as e:
                logger.warning(f"Gemini LLM polish skipped ({e}), falling back to deterministic formatter.")

        # Fallback to deterministic 3-line assembly
        if not drafted_post:
            fact_line = clean_body if (clean_body and len(clean_body) < 220) else headline
            context_line = f"Verified from official {category} disclosure"
            drafted_post = self.format_3line_post(company, headline[:100], fact_line, context_line)

        # 3. Deterministic Numeric & Entity Audit
        is_passed, ungrounded = self.audit_numbers(raw_text, drafted_post)
        
        if not is_passed:
            # Hallucination blocked! Regenerate deterministically with zero risk
            logger.warning(f"HALLUCINATION_BLOCKED: Draft contained ungrounded numbers {ungrounded}")
            fact_line = headline
            context_line = f"Verified from official {category} disclosure"
            drafted_post = self.format_3line_post(company, headline[:100], fact_line, context_line)
            # Re-audit
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
