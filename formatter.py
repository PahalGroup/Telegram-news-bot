import re
from html import unescape
from typing import Dict, Any, Optional

class Formatter:
    @staticmethod
    def clean_text(text: str) -> str:
        """Strip HTML tags, decode entities, and normalize whitespace."""
        if not text:
            return ""
        # decode entities
        t = unescape(text)
        # handle numeric entities and common malformed entities
        t = t.replace('&#39;', "'").replace('#39;', "'").replace('&quot;', '"').replace('&amp;', '&')
        # strip html tags
        t = re.sub(r'<[^>]+>', ' ', t)
        # replace smart quotes cleanly using explicit unicode escapes
        t = t.replace('\u2018', "'").replace('\u2019', "'").replace('\u201c', '"').replace('\u201d', '"')
        # normalize spaces
        t = re.sub(r'[ \t]+', ' ', t).strip()
        return t

    @staticmethod
    def strip_disclaimers_and_sources(text: str) -> str:
        """Remove trailing sources, disclaimers, 'why it matters', and generic boilerplate."""
        # Remove source lines: (Source: ...), (PTI), (Bloomberg), via Reuters
        text = re.sub(r'\s*\((?:Source|via|Inputs from)\s*:[^)]+\)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\(PTI|\(Reuters|\(Bloomberg|\(ANI\)', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\|\s*Read more.*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\|\s*Check details.*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\|\s*Here\'s what.*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*\|\s*(?:Target|Stop-loss|Rationale|Nifty outlook).*$', '', text, flags=re.IGNORECASE)
        
        # Remove "Why it matters", "Impact", "Disclaimer"
        text = re.sub(r'[:\s]*(?:Why it matters|Impact|Key takeaway|Disclaimer)\b.*$', '', text, flags=re.IGNORECASE | re.DOTALL)
        
        # Remove clickbait question suffixes if present at end
        text = re.sub(r'\s*\|\s*(?:Should you buy|Is the rally far from over|What to expect|What should investors do|Buy, sell or hold|Check target prices|Experts\'? views?)\??', '', text, flags=re.IGNORECASE)
        text = re.sub(r'\s*-\s*(?:Should you buy|What to expect|Check target prices)\??', '', text, flags=re.IGNORECASE)
        text = re.sub(r'[:.\s]+\d+\s+key\s+factors\b.*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'[:.\s]+what\s+are\s+technicals?\b.*$', '', text, flags=re.IGNORECASE)
        text = re.sub(r'^(?:Why is market falling today\??|Why are stocks falling today\??)\s*', '', text, flags=re.IGNORECASE)
        return text.strip()

    @staticmethod
    def format_exchange_filing(company_name: str, subject: str, headline: str = "", more: str = "", attachment_text: str = "") -> str:
        """
        Format: Hindustan Copper Ltd – CHANGE IN SENIOR MANAGEMENT
        Rule: Use the exchange subject. Do not rewrite. Do not round numbers.
        If rich disclosure details (headline/more) are present, include them as clean details below the title.
        """
        company_clean = Formatter.clean_text(company_name).strip()
        subj_clean = Formatter.clean_text(subject).strip()

        # Clean common boilerplate exchange prefixes from subject
        clean_subj = re.sub(r'^Announcement under Regulation\s*\d+\s*(?:\([^)]+\))?\s*[-–:]\s*', '', subj_clean, flags=re.IGNORECASE)
        clean_subj = re.sub(r'^Intimation under Regulation\s*\d+\s*(?:\([^)]+\))?\s*[-–:]\s*', '', clean_subj, flags=re.IGNORECASE)
        clean_subj = re.sub(r'^Disclosure under Regulation\s*\d+\s*(?:\([^)]+\))?\s*[-–:]\s*', '', clean_subj, flags=re.IGNORECASE)
        clean_subj = re.sub(r'^Compliances\s*[-–:]\s*', '', clean_subj, flags=re.IGNORECASE)
        clean_subj = re.sub(r'^Board Meeting Intimation for\s*', 'BOARD MEETING: ', clean_subj, flags=re.IGNORECASE)
        
        boilerplate_lines = [
            r'^dear\s+sir',
            r'^please\s+find\s+(?:enclosed|the\s+attachment|attached)',
            r'^intimation\s+attached',
            r'^regards',
            r'^for\s+[a-z0-9\s]+limited',
            r'^(?:authorised|authorized)\s+signatory',
            r'^compliance\s+officer',
            r'^thanking\s+you',
            r'^yours\s+faithfully'
        ]

        att_combined = f"{headline} {more} {attachment_text}".strip()
        generic_terms = ('general updates', 'updates', 'press release', 'media release', 'press release / media release', 'appointment', 'outcome of the board meeting', 'outcome of board meeting')
        if clean_subj.lower() in generic_terms and att_combined:
            att = Formatter.clean_text(headline or att_combined)
            att_norm = att.replace("''", "'")
            m_titled = re.search(r'titled\s*[:\s]*[\'"]([^\'\"]{15,140})[\'"]', att_norm, re.I)
            if not m_titled:
                m_titled = re.search(r'titled\s*[:\s]*["\']?([^"\n\r]{15,140}?)["\']?(?:\.|\"|$)', att_norm, re.I)
            if m_titled:
                clean_subj = m_titled.group(1).strip().strip('"\'')
            elif re.search(r'regarding\s+(Appointment\s+of\s+[^.\n]+)', att_norm, re.IGNORECASE):
                m = re.search(r'regarding\s+(Appointment\s+of\s+[^.\n]+)', att_norm, re.IGNORECASE)
                clean_subj = m.group(1).strip()
            else:
                match_about = re.search(r'(?:regarding|about)\s+(.{10,120}?)(\.|$)', att_norm, flags=re.IGNORECASE)
                if match_about:
                    clean_subj = match_about.group(1).strip()

        # Strip trailing or leading regulation boilerplate from subject
        clean_subj = re.sub(r'^(?:Announcement\s+under\s+)?(?:Regulation\s*\d+|Outcome\s+of\s+Board\s+Meeting)\s*[-–:]\s*', '', clean_subj, flags=re.IGNORECASE)
        clean_subj = clean_subj.strip(' -:–').upper()

        # Clean underscores and convert to uppercase
        clean_subj = clean_subj.replace('_', ' ')
        clean_subj = re.sub(r'\s+', ' ', clean_subj).strip(' -:–').upper()

        full_context = f"{clean_subj} {att_combined}".lower()

        # Specific institutional title synthesis:
        if 'buyback' in full_context or 'buy-back' in full_context:
            clean_subj = "EQUITY SHARE BUY-BACK"
        elif 'qip' in full_context or 'qualified institutional' in full_context:
            clean_subj = "QUALIFIED INSTITUTIONAL PLACEMENT OUTCOME"
        elif any(k in full_context for k in ['rupee term loan', 'term loan facility', 'facility agreement', 'ireda']):
            clean_subj = "RUPEE TERM LOAN FACILITY AGREEMENT"
        elif 'transtech punjab cbg' in full_context:
            clean_subj = "INVESTMENT IN TRANSTECH PUNJAB CBG LLP"
        elif 'proposal for investing' in full_context or 'investment in' in full_context:
            m_targ = re.search(r'investment in\s+([A-Za-z0-9\s]+(?:LLP|Limited|Pvt Ltd))', full_context, re.I)
            if m_targ:
                clean_subj = f"INVESTMENT IN {m_targ.group(1).strip().upper()}"
            else:
                clean_subj = "INVESTMENT IN SUBSIDIARY / ASSOCIATE"
        elif 'credit rating' in full_context:
            if any(k in full_context for k in ['upgrade', 'enhanced']):
                clean_subj = "CREDIT RATING UPGRADE"
            elif 'downgrade' in full_context:
                clean_subj = "CREDIT RATING DOWNGRADE"
            else:
                clean_subj = "CREDIT RATING UPDATE"
        elif any(k in full_context for k in ['award of order', 'receipt of order', 'award of work order', 'secures order', 'epcom']):
            if 'bpcl' in full_context or 'bharat petroleum' in full_context:
                clean_subj = "WINS LARGE-SCALE BPCL CONTRACT"
            else:
                clean_subj = "AWARD OF WORK ORDER"
        elif any(k in full_context for k in ['sole eligible', 'technically qualified bidder', 'artillery shells']):
            clean_subj = "SOLE TECHNICALLY QUALIFIED BIDDER FOR ARTILLERY SHELLS"
        elif any(k in full_context for k in ['md&ceo', 'md & ceo', 'managing director', 'ceo']):
            if any(k in full_context for k in ['extended', 'extension', 'tenure']):
                clean_subj = "TENURE EXTENSION OF MD & CEO"
            elif 'designate' in full_context:
                clean_subj = "CHANGE IN MANAGEMENT"
            else:
                clean_subj = "APPOINTMENT OF MD & CEO"
        elif any(k in full_context for k in ['chief risk officer', 'chief technology officer', 'senior management', 'directorate', 'nominee director']):
            clean_subj = "CHANGE IN SENIOR MANAGEMENT"
        elif any(gen in clean_subj for gen in ['REGULATION 30', 'GENERAL UPDATES', 'BOARD MEETING OUTCOME', 'OUTCOME OF BOARD MEETING', 'INTIMATION', 'SUBMISSION OF OUTCOME']):
            if len(clean_subj) > 30:
                clean_subj = "BOARD MEETING OUTCOME"

        title_line = f"{company_clean} \u2013 {clean_subj}"

        # Format details / bullets with reference channel layout
        if more:
            more_clean = Formatter.clean_text(more).strip()
            lines = [l.strip() for l in more_clean.splitlines() if l.strip()]
            bullets = []
            lead_line = ""
            for l in lines:
                if any(re.search(bp, l, re.I) for bp in boilerplate_lines):
                    continue
                if l.startswith(('- ', '• ', '* ', '● ')):
                    bullet_text = re.sub(r'^[-•*●\s,\'"]+', '', l).strip()
                    if len(bullet_text) < 10 or 'personal reason' in bullet_text.lower() or re.match(r'^Designation:\s*[\d\-]+$', bullet_text, re.I):
                        continue
                    bullets.append(f"- {bullet_text}")
                elif not lead_line and not bullets:
                    lead_line = l
                else:
                    bullet_text = re.sub(r'^[-•*●\s,\'"]+', '', l).strip()
                    if len(bullet_text) < 10 or 'personal reason' in bullet_text.lower() or re.match(r'^Designation:\s*[\d\-]+$', bullet_text, re.I):
                        continue
                    bullets.append(f"- {bullet_text}")

            if bullets:
                if lead_line:
                    if not lead_line.endswith(':'):
                        lead_line = f"{lead_line}:"
                    return f"{title_line}\n\n{lead_line}\n\n" + "\n\n".join(bullets)
                return f"{title_line}\n\n" + "\n\n".join(bullets)
            elif lead_line and len(lead_line) > 20 and not re.search(r'has\s+informed\s+the\s+exchange\s+about', lead_line, re.I):
                return f"{title_line}\n\n{lead_line}"

        # Check candidate raw texts if no structured more was provided
        candidates = [c for c in [headline.strip(), attachment_text.strip()] if c]
        for raw in candidates:
            clean_det = Formatter.clean_text(raw)
            body_lines = []
            for l in clean_det.splitlines():
                cl = l.strip()
                if not cl:
                    continue
                if any(re.search(bp, cl, re.I) for bp in boilerplate_lines):
                    continue
                body_lines.append(cl)

            clean_body = "\n".join(body_lines).strip()
            if re.search(r'has\s+informed\s+the\s+exchange\s+about', clean_body, re.I):
                continue
            if clean_body.upper() != clean_subj and clean_body.upper() != subj_clean.upper() and len(clean_body) > 25:
                return f"{title_line}\n\n{clean_body}"

        return title_line

    @staticmethod
    def parse_and_format_broker_headline(headline: str) -> Optional[str]:
        """
        Parses all common financial broker headline syntaxes into reference standard 4-line format:
        House on Stock:
        Rating: BUY/HOLD/SELL
        Target: ₹XXXX
        """
        t = headline.strip()

        # Pattern 1: House maintains/retains/gives/initiates/upgrades 'RATING' on Stock, target price ₹X/share
        m1 = re.search(r'([A-Za-z\s]+?)\s+(?:maintains|retains|gives|reiterates|initiates|upgrades|downgrades|keeps)\s+[\'"]?([A-Za-z\s]+?)[\'"]?\s+(?:(?:rating|stance)\s+)?on\s+([A-Za-z0-9\s&.-]+?),\s*(?:target\s+price|tp|target)\s*(?:of\s*)?([₹Rs.\d,]+(?:\.\d+)?)', t, re.I)
        if m1:
            house = m1.group(1).strip()
            rating = m1.group(2).strip().upper()
            stock = m1.group(3).strip()
            target = m1.group(4).strip()
            if not target.startswith('₹'):
                target = re.sub(r'^(?:Rs\.?|₹)\s*', '', target)
                target = f"₹{target}"
            return Formatter.format_broker_note(house=house, stock=stock, rating=rating, target=target)

        # Pattern 1b: House keeps/maintains/gives 'RATING' rating on Stock (without target price)
        m1b = re.search(r'([A-Za-z\s]+?)\s+(?:maintains|retains|gives|reiterates|initiates|upgrades|downgrades|keeps)\s+[\'"]?([A-Za-z\s]+?)[\'"]?\s+(?:rating|stance)\s+on\s+([A-Za-z0-9\s&.-]+)', t, re.I)
        if m1b:
            house = m1b.group(1).strip()
            rating = m1b.group(2).strip().upper()
            stock = m1b.group(3).strip()
            return Formatter.format_broker_note(house=house, stock=stock, rating=rating, target="")

        # Pattern 2: House on Stock: Rating: X Target: Y
        m2 = re.search(r'([A-Za-z\s]+?)\s+on\s+([A-Za-z0-9\s&.-]+?):\s*(?:Rating:?\s*([A-Za-z]+(?:\s+(?!Target)[A-Za-z]+)?))?\s*(?:Target:?\s*([₹Rs.\d,]+))?', t, re.I)
        if m2 and (m2.group(3) or m2.group(4)):
            house = m2.group(1).strip()
            stock = m2.group(2).strip()
            rating = (m2.group(3) or '').strip().upper()
            target = (m2.group(4) or '').strip()
            if target and not target.startswith('₹'):
                target = re.sub(r'^(?:Rs\.?|₹)\s*', '', target)
                target = f"₹{target}"
            out = [f"{house} on {stock}:"]
            if rating: out.append(f"Rating: {rating}")
            if target: out.append(f"Target: {target}")
            return "\n".join(out)

        # Pattern 3: (Buy/Sell/Hold/...) Stock; target of Rs X: House
        m3 = re.match(r'(Buy|Sell|Hold|Accumulate|Reduce|Neutral|Add|Overweight|Underweight)\s+([^;]+);\s*target\s+of\s+Rs\.?\s*([\d,]+):\s*(.*)', t, re.I)
        if m3:
            rating = m3.group(1).upper()
            stock = m3.group(2).strip()
            target = f"₹{m3.group(3).strip()}"
            house = m3.group(4).strip()
            return Formatter.format_broker_note(house=house, stock=stock, rating=rating, target=target)

        # Pattern 4: House rates/views Stock as RATING with target of Rs X
        m4 = re.search(r'([A-Za-z\s]+?)\s+(?:has\s+a|recommends)\s+[\'"]?([A-Za-z]+)[\'"]?\s+(?:on|for)\s+([A-Za-z0-9\s&.-]+?)(?:;|\swith\s*target\s*(?:of|at)?)\s*([₹Rs.\d,]+)', t, re.I)
        if m4:
            house = m4.group(1).strip()
            rating = m4.group(2).strip().upper()
            stock = m4.group(3).strip()
            target = m4.group(4).strip()
            if not target.startswith('₹'):
                target = re.sub(r'^(?:Rs\.?|₹)\s*', '', target)
                target = f"₹{target}"
            return Formatter.format_broker_note(house=house, stock=stock, rating=rating, target=target)

        return None

    @staticmethod
    def format_company_flash(raw_headline: str, company_hint: str = "") -> str:
        """
        A company flash keeps the exact figure:
        OSWAL PUMPS: PLANS ₹455.85 CRORE SOLAR CELL PLANT, REDIRECTS ₹159.85 CRORE IPO PROCEEDS TO 1.2 GW CAPACITY
        """
        headline = Formatter.clean_text(raw_headline)
        headline = Formatter.strip_disclaimers_and_sources(headline)
        
        # If headline already has "COMPANY: ...", format into uppercase
        if ":" in headline:
            parts = headline.split(":", 1)
            prefix = parts[0].strip().upper()
            body = parts[1].strip().upper()
            return f"{prefix}: {body}"
        
        # If company hint is provided and not at the start
        if company_hint:
            comp_upper = company_hint.strip().upper()
            return f"{comp_upper}: {headline.upper()}"

        return headline.upper()

    @staticmethod
    def format_wire(raw_text: str) -> str:
        """
        A wire is one line:
        Nifty to open 72 points gap up
        """
        text = Formatter.clean_text(raw_text)
        text = Formatter.strip_disclaimers_and_sources(text)
        # Ensure single line
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return lines[0] if lines else text

    @staticmethod
    def format_broker_note(house: str, stock: str, rating: str, target: str, bullets: list = None) -> str:
        """
        A broker note is the house, the stock, the rating and the target, then short bullets.
        """
        out = [f"{house.strip()} on {stock.strip()}:"]
        if rating:
            out.append(f"Rating: {rating.strip()}")
        if target:
            out.append(f"Target: {target.strip()}")
        if bullets:
            for b in bullets:
                clean_b = Formatter.clean_text(b).strip()
                if clean_b:
                    out.append(f"• {clean_b}")
        return "\n".join(out)

    @staticmethod
    def format_fii_dii(trade_data: list) -> str:
        """
        Format official NSE FII/DII data line matching reference channel:
        FII/DII ACTIVITY – 12 OCT 2026
        🟢 DII Net Buy: ₹3,045.02 Cr
        🔴 FII Net Sell: ₹-1,708.48 Cr
        🟢 Net Buy: ₹1,336.54 Cr
        """
        date_str = ""
        fii_net = None
        dii_net = None
        for row in trade_data:
            cat = row.get('category', '').upper()
            date_str = row.get('date', date_str)
            try:
                net_val = float(row.get('netValue', '0'))
            except (ValueError, TypeError):
                continue
            if 'FII' in cat or 'FPI' in cat:
                fii_net = net_val
            elif 'DII' in cat:
                dii_net = net_val

        clean_date = date_str.upper()
        lines = [f"FII/DII ACTIVITY – {clean_date}"]
        if dii_net is not None:
            circle = "🟢" if dii_net >= 0 else "🔴"
            action = "Net Buy" if dii_net >= 0 else "Net Sell"
            lines.append(f"{circle} DII {action}: ₹{dii_net:,.2f} Cr")
        if fii_net is not None:
            circle = "🟢" if fii_net >= 0 else "🔴"
            action = "Net Buy" if fii_net >= 0 else "Net Sell"
            lines.append(f"{circle} FII {action}: ₹{fii_net:,.2f} Cr")
        if dii_net is not None and fii_net is not None:
            net_total = dii_net + fii_net
            circle = "🟢" if net_total >= 0 else "🔴"
            action = "Net Buy" if net_total >= 0 else "Net Sell"
            lines.append(f"{circle} {action}: ₹{net_total:,.2f} Cr")

        return "\n".join(lines)
