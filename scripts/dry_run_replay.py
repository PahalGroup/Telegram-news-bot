"""Replay a saved day of real BSE filings (plus live deal/RBI/PIB snapshots) through the
daemon on a simulated clock. Dry run only: nothing is sent to Telegram.

    python scripts/dry_run_replay.py --ann bse_ann.json --pdf-cache pdf/ --out replay/

The clock advances 12 s per cycle, the production poll interval. A PDF job finishes
after its measured parse time plus a download allowance, so latency includes extraction.
"""
import argparse, json, os, statistics, sys, time as _time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import src.daemon as daemon_mod  # noqa: E402
from src import editorial  # noqa: E402
from src.daemon import RealtimeDaemon  # noqa: E402
from src.filter_engine import FilterEngine  # noqa: E402
from src.scheduler import IST, parse_time  # noqa: E402
import sources.pdf_download as pdf_download  # noqa: E402

TICK = 12


class Sim:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now


class TimeShim:
    """Stands in for the `time` module inside src.daemon so throttles follow the sim clock."""
    def __init__(self, sim):
        self.sim = sim

    def time(self):
        return self.sim.now.timestamp()

    def __getattr__(self, name):
        return getattr(_time, name)


class InlinePool:
    """Runs source calls immediately so each cycle sees what was visible at sim time."""
    def submit(self, fn, *a, **k):
        f = Future()
        try:
            f.set_result(fn(*a, **k))
        except Exception as e:  # noqa: BLE001
            f.set_exception(e)
        return f

    def shutdown(self, **_):
        pass


class SimFuture(Future):
    def __init__(self, sim, ready_at):
        super().__init__()
        self.sim, self.ready_at = sim, ready_at

    def done(self):
        return self.sim.now >= self.ready_at


class SimPdfPool:
    """Parses now, but reports done only after the measured parse time + download allowance."""
    def __init__(self, sim, sizes):
        self.sim, self.sizes, self.timings = sim, sizes, []

    def submit(self, fn, url, kind):
        t0 = _time.monotonic()
        f = None
        try:
            res = fn(url, kind)
        except Exception as e:  # noqa: BLE001
            res, err = None, e
        else:
            err = None
        parse = _time.monotonic() - t0
        size = self.sizes.get(os.path.basename(url), 1_000_000)
        allowance = 1.0 + size / 2_000_000          # 1 s connect + 2 MB/s
        f = SimFuture(self.sim, self.sim.now + timedelta(seconds=parse + allowance))
        self.timings.append(parse + allowance)
        f.set_exception(err) if err else f.set_result(res)
        return f

    def shutdown(self, **_):
        pass


def cached_download(cache):
    original = pdf_download.download_pdf

    def download(url, client=None, **kw):
        path = Path(cache) / os.path.basename(url.split("?")[0])
        if path.exists():
            return path.read_bytes()
        data = original(url, client=client, **kw)
        path.write_bytes(data)
        return data
    return download


def bse_rows(ann, sim):
    """What BSEFetcher would return at sim time: rows from the last 6 h, pre-dropped."""
    out = []
    for r in ann:
        t = parse_time(r["NEWS_DT"])
        if not t or not (timedelta(0) <= sim.now - t <= timedelta(hours=6)):
            continue
        raw = (r.get("NEWSSUB") or "").strip()
        subj = raw.split(" - ")[-1].strip() if " - " in raw else raw
        if FilterEngine.should_drop(f"{r.get('SLONGNAME','')} {subj} {raw} {r.get('HEADLINE','')}"):
            continue
        att = r.get("ATTACHMENTNAME") or ""
        out.append({"company": (r.get("SLONGNAME") or "").strip(), "subject": subj, "raw_subject": raw,
                    "headline": (r.get("HEADLINE") or "").strip(), "more": (r.get("MORE") or "").strip(),
                    "news_dt": r["NEWS_DT"], "newsid": str(r["NEWSID"]),
                    "pdf_url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else None,
                    "subcat": r.get("SUBCATNAME") or "", "category": r.get("CATEGORYNAME") or ""})
    return out


def visible(rows, sim, key):
    return [r for r in rows if (parse_time(r.get(key)) or sim.now) <= sim.now]


def prefetch(ann, cache, rules):
    """Download, in parallel, every PDF the daemon would ask for (saves replay wall time)."""
    from curl_cffi import requests
    want = []
    for r in ann:
        att = r.get("ATTACHMENTNAME")
        if not att or (Path(cache) / att).exists():
            continue
        raw = r.get("NEWSSUB") or ""
        item = {"source": "BSE", "title": raw.split(" - ")[-1], "raw_subject": raw, "subcat": r.get("SUBCATNAME") or ""}
        if FilterEngine.should_drop(f"{r.get('SLONGNAME','')} {raw} {r.get('HEADLINE','')}"):
            continue
        if editorial.classify(item, rules).rule == "always_drop":
            continue
        want.append(att)

    def get(att):
        for base in ("AttachLive", "AttachHis"):
            try:
                resp = requests.get(f"https://www.bseindia.com/xml-data/corpfiling/{base}/{att}",
                                    headers={"Referer": "https://www.bseindia.com/"}, impersonate="chrome", timeout=40)
                if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                    (Path(cache) / att).write_bytes(resp.content)
                    return 1
            except Exception:  # noqa: BLE001
                pass
        return 0
    with ThreadPoolExecutor(8) as ex:
        got = sum(ex.map(get, want))
    print(f"prefetch: {got}/{len(want)} PDFs downloaded", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ann", required=True)
    ap.add_argument("--pdf-cache", required=True)
    ap.add_argument("--snapshots", help="JSON with live deals/rbi/rbi_speeches/pib rows")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prefetch", action="store_true")
    a = ap.parse_args()

    ann = json.load(open(a.ann))
    snaps = json.load(open(a.snapshots)) if a.snapshots else {}
    sim = Sim(datetime.fromisoformat(a.start).replace(tzinfo=IST))
    end = datetime.fromisoformat(a.end).replace(tzinfo=IST)
    os.makedirs(a.out, exist_ok=True)

    d = RealtimeDaemon(dry_run=True)
    if a.prefetch:
        prefetch(ann, a.pdf_cache, d.rules)
    daemon_mod.time = TimeShim(sim)
    pdf_download.download_pdf = cached_download(a.pdf_cache)
    d.clock = sim
    d._source_pool, d._source_pending = InlinePool(), {}
    sizes = {r["ATTACHMENTNAME"]: int(r.get("Fld_Attachsize") or 0) for r in ann if r.get("ATTACHMENTNAME")}
    d.prepare_items([])                                  # initialise PDF state, then swap in the sim pool
    d._pdf_pool = SimPdfPool(sim, sizes)
    d.check_fii_dii_routine = lambda: None               # wall-clock routine; not part of the replay

    # Sources: BSE from the saved feed; NSE/RSS/liveblog off (not archived); snapshots for the rest.
    d.bse.fetch_announcements = lambda skip_pdf=True: bse_rows(ann, sim)
    d.nse.fetch_announcements = lambda: []
    d.news.fetch_all_feeds = lambda: []
    d.liveblog.fetch_live_flashes = lambda: []
    deals_from = datetime.fromisoformat(snaps.get("deals_visible_from", "2100-01-01T00:00")).replace(tzinfo=IST)
    d.nse.fetch_large_deals = lambda: snaps.get("deals", []) if sim.now >= deals_from else []
    d.nse.fetch_past_issues = lambda: snaps.get("past_issues", {"data": []}) if "past_issues" in snaps else []
    d.rbi.fetch_press_releases = lambda: visible(snaps.get("rbi", []), sim, "pub_date")
    d.rbi.fetch_speeches = lambda: visible(snaps.get("rbi_speeches", []), sim, "pub_date")
    pib_from = datetime.fromisoformat(snaps.get("pib_visible_from", "2100-01-01T00:00")).replace(tzinfo=IST)
    d._fetch_pib = lambda: snaps.get("pib", []) if sim.now >= pib_from else []
    cues = snaps.get("cues")
    import sources.market_cues as mc
    mc.fetch_cues = lambda: cues or {}

    published = {}
    for r in ann:
        published[f"BSE_{r['NEWSID']}"] = (parse_time(r["NEWS_DT"]), r.get("SUBCATNAME"), r.get("SLONGNAME"))
    trace = []
    d.observer = type("Obs", (), {"record": lambda self, stage, item, reason="", **k: trace.append(
        (sim.now.isoformat(), stage, item.get("id"), reason)), "source": lambda self, *x, **k: None})()

    wall = _time.monotonic()
    while sim.now < end:
        d.process_cycle()
        sim.now += timedelta(seconds=TICK)
    print(f"replay wall time {(_time.monotonic()-wall)/60:.1f} min, {d.cycle_count} cycles", file=sys.stderr)

    posts = []
    for p in d.dry_posts:
        at = datetime.fromisoformat(p["at"])
        pub = published.get(p["key"], (None, None, None))
        posts.append({"key": p["key"], "at": p["at"], "filed": pub[0].isoformat() if pub[0] else None,
                      "latency_s": (at - pub[0]).total_seconds() if pub[0] else None,
                      "subcat": pub[1], "text": p["text"]})
    json.dump(posts, open(Path(a.out) / "posts.json", "w"), indent=1, ensure_ascii=False)
    json.dump(trace, open(Path(a.out) / "trace.json", "w"))
    json.dump(d._pdf_pool.timings, open(Path(a.out) / "pdf_timings.json", "w"))

    lat = [p["latency_s"] for p in posts if p["latency_s"] is not None]
    quiet = [p for p in posts if not (7 * 60 <= datetime.fromisoformat(p["at"]).hour * 60
                                      + datetime.fromisoformat(p["at"]).minute < 23 * 60)]
    gaps = sorted((datetime.fromisoformat(b["at"]) - datetime.fromisoformat(x["at"])).total_seconds()
                  for x, b in zip(posts, posts[1:]))
    metrics = {
        "posts": len(posts),
        "filing_posts": len(lat),
        "median_filing_to_post_s": statistics.median(lat) if lat else None,
        "p90_filing_to_post_s": sorted(lat)[int(len(lat) * 0.9)] if lat else None,
        "procedural_posts": sum(bool(editorial.PROCEDURAL_POST.search(p["text"])) for p in posts),
        "junk_posts": sum(bool(editorial.JUNK_IN_POST.search(p["text"])) for p in posts),
        "posts_23_00_to_07_00": len(quiet),
        "posts_23_30_to_06_30": sum(1 for p in quiet if not (datetime.fromisoformat(p["at"]).hour == 23 and datetime.fromisoformat(p["at"]).minute < 30) and not (datetime.fromisoformat(p["at"]).hour == 6 and datetime.fromisoformat(p["at"]).minute >= 30)),
        "min_gap_s": gaps[0] if gaps else None,
        "rejections": {},
    }
    for _, stage, _, reason in trace:
        if stage == "rejected":
            metrics["rejections"][reason] = metrics["rejections"].get(reason, 0) + 1
    metrics["procedural_share"] = metrics["procedural_posts"] / len(posts) if posts else 0
    json.dump(metrics, open(Path(a.out) / "metrics.json", "w"), indent=1)
    print(json.dumps(metrics, indent=1))


if __name__ == "__main__":
    main()
