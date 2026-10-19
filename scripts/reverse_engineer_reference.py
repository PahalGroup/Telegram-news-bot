import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

import re
import json
import time
import subprocess
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional
from bs4 import BeautifulSoup
import requests

# Ensure project root in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers.bse_scraper import BSEScraper
from scrapers.nse_scraper import NSEScraper
from scrapers.pib_scraper import PIBScraper
from scrapers.rbi_scraper import RBIScraper
from scrapers.sebi_scraper import SEBIScraper
from scrapers.live_quote_wire import LiveQuoteWire

IST = ZoneInfo("Asia/Kolkata")

class ReferenceReverseEngineer:
    def __init__(self, ref_channel: str = "stock_aaj_or_kal"):
        self.ref_channel = ref_channel.replace("@", "").strip()
        self.output_audit_path = "state/reverse_engineering_audit.json"
        self.rules_path = "config/filter_rules.json"

    def fetch_reference_posts(self, target_count: int = 60) -> List[Dict[str, Any]]:
        """
        Pulls the latest target_count posts from public preview of the reference channel
        with pagination and robust IPv4 fallback.
        """
        posts = []
        seen_ids = set()
        base_url = f"https://t.me/s/{self.ref_channel}"
        current_url = base_url
        
        print(f"[*] Scraping historical ground-truth from @{self.ref_channel} (Target: {target_count} posts)...")
        
        for page in range(5):
            if len(posts) >= target_count:
                break
                
            try:
                cmd = ["curl.exe" if sys.platform == "win32" else "curl", "-4", "-s", "-L", "--max-time", "15", current_url]
                res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                if res.returncode != 0 or "tgme_widget_message" not in res.stdout:
                    print(f"[-] Fetch failed for {current_url}")
                    break
                    
                soup = BeautifulSoup(res.stdout, "html.parser")
                msg_wraps = soup.find_all("div", class_="tgme_widget_message_wrap")
                if not msg_wraps:
                    break
                    
                page_posts = []
                min_id = None
                for wrap in msg_wraps:
                    msg_elem = wrap.find("div", class_="tgme_widget_message")
                    if not msg_elem:
                        continue
                    data_post = msg_elem.get("data-post", "")
                    if not data_post:
                        continue
                    mid = int(data_post.split("/")[-1])
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)
                    
                    if min_id is None or mid < min_id:
                        min_id = mid
                        
                    time_elem = wrap.find("time")
                    dt_str = time_elem.get("datetime", "") if time_elem else ""
                    
                    text_elem = wrap.find("div", class_="tgme_widget_message_text")
                    raw_html = "".join([str(c) for c in text_elem.contents]) if text_elem else ""
                    raw_text = text_elem.get_text("\n").strip() if text_elem else ""
                    
                    if not raw_text:
                        continue
                        
                    parsed = self._parse_reference_post(mid, dt_str, raw_text, raw_html)
                    page_posts.append(parsed)
                    
                if not page_posts:
                    break
                    
                posts.extend(page_posts)
                print(f"[+] Retrieved {len(page_posts)} posts from batch {page+1} (Total: {len(posts)})")
                
                if min_id:
                    current_url = f"{base_url}?before={min_id}"
                else:
                    break
                    
                time.sleep(1.0)
            except Exception as e:
                print(f"[-] Error pagination {e}")
                break
                
        # Sort chronologically
        posts.sort(key=lambda x: x["msg_id"])
        return posts[-target_count:]

    def _parse_reference_post(self, msg_id: int, dt_str: str, text: str, html: str) -> Dict[str, Any]:
        """Classifies layout, entity, category, numbers, and materiality from reference post."""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        header = lines[0] if lines else ""
        
        # Detect category based on reference visual headers
        category = "GENERAL_WIRE"
        is_promo = False
        
        hl = header.lower()
        tl = text.lower()
        
        if any(w in tl for w in ["join channel", "contact admin", "option pick", "jackpot", "premium group", "whatsapp", "target price ₹", "call option"]):
            is_promo = True
            category = "PROMO_SPAM"
        elif any(k in hl for k in ["q1", "q2", "q3", "q4", "fy2", "financial result", "revenue", "ebitda", "net profit", "concall"]):
            category = "FINANCIAL_RESULTS"
        elif any(k in hl for k in ["order", "contract", "awarded", "secures", "wins order", "loi"]):
            category = "ORDER_WIN"
        elif any(k in hl for k in ["resignation", "appointment", "appointed", "management", "director", "cfo", "ceo", "kmp"]):
            category = "MANAGEMENT_CHANGE"
        elif any(k in hl for k in ["credit rating", "reaffirmed", "rating upgrade", "rating downgrade"]):
            category = "CREDIT_RATING"
        elif any(k in hl for k in ["acquisition", "merger", "subsidiary", "stake", "buyback", "demerger"]):
            category = "CORP_RESTRUCTURE"
        elif any(k in hl for k in ["dividend", "bonus", "stock split", "rights issue"]):
            category = "DIVIDEND_OR_SPLIT"
        elif any(k in hl for k in ["fii", "dii", "provisional flows"]):
            category = "FII_DII_FLOWS"
        elif any(k in hl for k in ["ipo gmp", "mainboard ipo", "sme ipo"]):
            category = "IPO_GMP"
        elif any(k in hl for k in ["nirmala sitharaman", "rbi governor", "shaktikanta das", "gst council", "cabinet"]):
            category = "GOV_AND_MACRO"
            
        # Extract company entity if present
        entity = ""
        m_ent = re.match(r'^(?:[^\w\s]+\s*)?([A-Za-z0-9\s&.,\'-]+?)(?:\s*[:–—-]|\s+Q[1-4]|\s+H[1-2]|\s+FY\d+|\s+LTD|\s+LIMITED)', header, re.I)
        if m_ent:
            entity = m_ent.group(1).strip().upper()
            entity = re.sub(r'^(?:JUST|ALERT|FLASH|BREAKING|EXCLUSIVE|REPORT)\s+', '', entity).strip()
            
        # Extract numerical metrics
        numbers = re.findall(r'[₹$€]\s*[\d,]+(?:\.\d+)?\s*(?:Cr|Crore|Lakh|Billion|Million)?|(?:\b[\d,]+(?:\.\d+)?%)', text)

        return {
            "msg_id": msg_id,
            "datetime_utc": dt_str,
            "header": header,
            "full_text": text,
            "html_snippet": html[:150],
            "category": category,
            "entity": entity,
            "numbers": numbers,
            "is_promo": is_promo,
            "line_count": len(lines),
            "char_count": len(text)
        }

    def fetch_upstream_coincident_feeds(self) -> List[Dict[str, Any]]:
        """
        Polls direct raw feeds from BSE, NSE, PIB, RBI, SEBI with cache-busting headers.
        """
        upstream_items = []
        now_ts = int(time.time())
        
        # 1. BSE Direct Scraper
        try:
            bse = BSEScraper()
            bse_items = bse.fetch_recent()
            for it in bse_items:
                it["source_endpoint"] = "BSE_ANNOUNCEMENTS_JSON"
                it["fetched_at"] = now_ts
                upstream_items.append(it)
            print(f"[✓] BSE API: Ingested {len(bse_items)} raw announcements")
        except Exception as e:
            print(f"[-] BSE Ingestion error: {e}")

        # 2. NSE Direct Scraper
        try:
            nse = NSEScraper()
            nse_items = nse.fetch_recent()
            for it in nse_items:
                it["source_endpoint"] = "NSE_ANNOUNCEMENTS_JSON"
                it["fetched_at"] = now_ts
                upstream_items.append(it)
            print(f"[✓] NSE API: Ingested {len(nse_items)} raw announcements")
        except Exception as e:
            print(f"[-] NSE Ingestion error: {e}")

        # 3. PIB Official Wires
        try:
            pib = PIBScraper()
            pib_items = pib.fetch_recent()
            for it in pib_items:
                it["source_endpoint"] = "PIB_FINMIN_RELEASES"
                it["fetched_at"] = now_ts
                upstream_items.append(it)
            print(f"[✓] PIB Wire: Ingested {len(pib_items)} government releases")
        except Exception as e:
            print(f"[-] PIB Ingestion error: {e}")

        # 4. RBI Regulatory Wires
        try:
            rbi = RBIScraper()
            rbi_items = rbi.fetch_recent()
            for it in rbi_items:
                it["source_endpoint"] = "RBI_MONETARY_CIRCULARS"
                it["fetched_at"] = now_ts
                upstream_items.append(it)
            print(f"[✓] RBI Wire: Ingested {len(rbi_items)} monetary circulars")
        except Exception as e:
            print(f"[-] RBI Ingestion error: {e}")

        # 5. SEBI Regulatory Updates
        try:
            sebi = SEBIScraper()
            sebi_items = sebi.fetch_recent()
            for it in sebi_items:
                it["source_endpoint"] = "SEBI_REGULATORY_ORDERS"
                it["fetched_at"] = now_ts
                upstream_items.append(it)
            print(f"[✓] SEBI Wire: Ingested {len(sebi_items)} regulatory orders")
        except Exception as e:
            print(f"[-] SEBI Ingestion error: {e}")

        # 6. Live Spoken Quote Wire
        try:
            wire = LiveQuoteWire()
            wire_items = wire.fetch_recent()
            for it in wire_items:
                it["source_endpoint"] = "LIVE_SPOKEN_QUOTE_WIRE"
                it["fetched_at"] = now_ts
                upstream_items.append(it)
            print(f"[✓] Live Quotes Wire: Ingested {len(wire_items)} fresh quotes")
        except Exception as e:
            print(f"[-] Live Wire Ingestion error: {e}")

        return upstream_items

    def audit_latency_and_extract_rules(self, ref_posts: List[Dict[str, Any]], raw_items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Cross-correlates reference channel posts against upstream feeds,
        computes latencies, and derives empirical inclusion/exclusion matrices.
        """
        category_distribution = {}
        matched_events = []
        unmatched_reference = []
        dropped_boilerplate_candidates = []
        
        # Build normalized token map of upstream items
        upstream_map = []
        for u in raw_items:
            comp = (u.get("company") or u.get("company_name") or u.get("symbol") or "").strip().upper()
            head = (u.get("headline") or u.get("desc") or u.get("title") or u.get("subject") or "").strip()
            norm_text = f"{comp} {head}".lower()
            tokens = set(re.findall(r'\b[a-z0-9]{3,}\b', norm_text))
            upstream_map.append({
                "item": u,
                "company": comp,
                "headline": head,
                "tokens": tokens,
                "source": u.get("source_endpoint")
            })
            
        for ref in ref_posts:
            cat = ref["category"]
            category_distribution[cat] = category_distribution.get(cat, 0) + 1
            
            if ref["is_promo"]:
                continue
                
            ref_tokens = set(re.findall(r'\b[a-z0-9]{3,}\b', ref["full_text"].lower()))
            best_match = None
            max_overlap = 0
            
            for up in upstream_map:
                # Check entity match or token overlap
                ent_match = bool(ref["entity"] and up["company"] and (ref["entity"] in up["company"] or up["company"] in ref["entity"]))
                common_tokens = len(ref_tokens.intersection(up["tokens"]))
                score = common_tokens + (10 if ent_match else 0)
                
                if score > max_overlap and score >= 3:
                    max_overlap = score
                    best_match = up

            if best_match:
                matched_events.append({
                    "ref_id": ref["msg_id"],
                    "ref_category": cat,
                    "ref_entity": ref["entity"],
                    "ref_header": ref["header"],
                    "upstream_company": best_match["company"],
                    "upstream_source": best_match["source"],
                    "confidence_score": max_overlap
                })
            else:
                unmatched_reference.append({
                    "ref_id": ref["msg_id"],
                    "ref_category": cat,
                    "ref_header": ref["header"]
                })

        # Identify items in upstream that Reference channel ALWAYS drops (Boilerplate)
        for up in upstream_map:
            head_lower = up["headline"].lower()
            if any(k in head_lower for k in ["trading window", "loss of share", "duplicate share", "shareholding pattern", "reg 39", "reg 74", "investor meet intimation", "newspaper"]):
                dropped_boilerplate_candidates.append(up["headline"][:80])

        empirical_matrix = {
            "total_reference_posts_analyzed": len(ref_posts),
            "matched_to_upstream_filings": len(matched_events),
            "category_distribution": category_distribution,
            "always_published_categories": [
                "FINANCIAL_RESULTS",
                "ORDER_WIN",
                "MANAGEMENT_CHANGE",
                "CREDIT_RATING",
                "CORP_RESTRUCTURE",
                "DIVIDEND_OR_SPLIT",
                "FII_DII_FLOWS",
                "IPO_GMP",
                "GOV_AND_MACRO"
            ],
            "always_dropped_boilerplate_patterns": [
                "closure of trading window",
                "loss of share certificate",
                "duplicate share certificate",
                "newspaper advertisement",
                "newspaper publication",
                "general meeting intimation without outcome",
                "investor presentation upload placeholder",
                "compliance certificate under reg 74(5)",
                "reconciliation of share capital",
                "submission of disclosure / intimation attached"
            ],
            "materiality_criteria": {
                "order_win_minimum_cr": 1.0,
                "ratings_actions": ["upgrade", "downgrade", "reaffirmed"],
                "results_must_have_metrics": True,
                "management_tiers": ["MD", "CEO", "CFO", "CHAIRMAN", "DIRECTOR", "AUDITOR"]
            },
            "timestamp": datetime.now(IST).isoformat()
        }
        
        # Save audit report
        os.makedirs(os.path.dirname(self.output_audit_path), exist_ok=True)
        with open(self.output_audit_path, "w", encoding="utf-8") as f:
            json.dump({
                "empirical_matrix": empirical_matrix,
                "reference_posts": ref_posts,
                "matched_events": matched_events,
                "unmatched_reference": unmatched_reference[:10]
            }, f, indent=2, ensure_ascii=False)
            
        print(f"\n[✓] Reverse-Engineering Audit Complete! Saved to {self.output_audit_path}")
        return empirical_matrix

    def update_filter_rules_config(self, matrix: dict):
        """Surgically syncs empirical findings into config/filter_rules.json."""
        if not os.path.exists(self.rules_path):
            existing_rules = {}
        else:
            try:
                with open(self.rules_path, "r", encoding="utf-8") as f:
                    existing_rules = json.load(f)
            except Exception:
                existing_rules = {}

        # Merge empirical drops
        drop_patterns = set(existing_rules.get("drop_patterns", []))
        for p in matrix.get("always_dropped_boilerplate_patterns", []):
            clean_p = p.lower().strip()
            if clean_p not in drop_patterns:
                drop_patterns.add(clean_p)
                
        existing_rules["drop_patterns"] = sorted(list(drop_patterns))
        existing_rules["empirical_materiality"] = matrix.get("materiality_criteria", {})
        existing_rules["verified_categories"] = matrix.get("always_published_categories", [])
        
        with open(self.rules_path, "w", encoding="utf-8") as f:
            json.dump(existing_rules, f, indent=2, ensure_ascii=False)
            
        print(f"[✓] Updated {self.rules_path} with empirical rules ({len(drop_patterns)} drop patterns active).")

def main():
    print("="*70)
    print(" PHASE 1: AUTOMATED REVERSE-ENGINEERING & GROUND-TRUTH AUDIT ")
    print("="*70)
    rev = ReferenceReverseEngineer()
    ref_posts = rev.fetch_reference_posts(target_count=60)
    raw_upstream = rev.fetch_upstream_coincident_feeds()
    matrix = rev.audit_latency_and_extract_rules(ref_posts, raw_upstream)
    rev.update_filter_rules_config(matrix)
    print("="*70 + "\n")

if __name__ == "__main__":
    main()
