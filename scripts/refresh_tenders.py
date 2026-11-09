"""Build a rolling public feed from the two UK procurement APIs."""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen

NOW = datetime.now(timezone.utc)
START = (NOW - timedelta(days=45)).strftime("%Y-%m-%dT%H:%M:%S")
FT = "https://www.find-tender.service.gov.uk"
CF = "https://www.contractsfinder.service.gov.uk"
TERMS = re.compile(r"maintenan|\brepair|refurbish|roofing|plumbing|electrical|joinery|glazing|grounds maintenance|fire extinguisher|pat testing|gutter|decorating|flooring|boiler|water treatment", re.I)

def pages(origin, path, params):
    url = origin + path + "?" + urlencode(params)
    seen = set()
    while url:
        if url in seen or len(seen) >= 250 or urlsplit(url).netloc != urlsplit(origin).netloc:
            raise RuntimeError("Unexpected pagination")
        seen.add(url)
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=35) as response:
            result = json.load(response)
        yield from result["releases"]
        next_page = (result.get("links") or {}).get("next")
        if next_page:
            url = urljoin(url, next_page)
        elif result.get("maxPage", 0) > params.get("page", 1):
            params["page"] = params.get("page", 1) + 1
            url = origin + path + "?" + urlencode(params)
        else:
            url = None

def parse_notice(release, name, origin):
    tender = release.get("tender") or {}
    deadline = (tender.get("tenderPeriod") or {}).get("endDate")
    title = tender.get("title") or ""
    description = re.sub(r"<[^>]+>", " ", tender.get("description") or "")
    if tender.get("status") != "active" or not deadline or not title:
        return None
    if datetime.fromisoformat(deadline.replace("Z", "+00:00")) <= NOW:
        return None
    if not TERMS.search(title + " " + description):
        return None
    if re.search(r"\b(social housing|council housing|housing association|domestic properties|residential properties)\b", title, re.I):
        return None
    notice_id = re.search(r"\d{6}-\d{4}" if origin == FT else r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", release.get("id", ""))
    if not notice_id:
        return None
    value = tender.get("value") or {}
    return {
        "title": title,
        "description": description[:1800],
        "deadline": deadline,
        "published": release.get("date", ""),
        "buyer": (release.get("buyer") or {}).get("name", ""),
        "value": value.get("amount") if value.get("currency") == "GBP" else None,
        "source": name,
        "url": origin + "/Notice/" + notice_id[0],
    }

sources = [
    ("Find a Tender", FT, "/api/1.0/ocdsReleasePackages", {"updatedFrom": START, "stages": "tender", "limit": 100}),
    ("Contracts Finder", CF, "/Published/Notices/OCDS/Search", {"publishedFrom": START + "Z", "stages": "tender", "size": 100}),
]
notices = {}
errors = []
for name, origin, path, params in sources:
    try:
        releases = list(pages(origin, path, params))
        latest = {}
        for release in sorted(releases, key=lambda item: item.get("date") or ""):
            latest[release.get("ocid") or release["id"]] = release
        for release in latest.values():
            notice = parse_notice(release, name, origin)
            if notice:
                notices[notice["url"]] = notice
        print(f"{name}: checked {len(releases)} releases")
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"{name}: {error}", file=sys.stderr)
        errors.append(name)
if len(errors) == len(sources):
    raise SystemExit("Both procurement feeds failed")
Path(sys.argv[1]).write_text(json.dumps({
    "generated": NOW.isoformat(),
    "failedSources": errors,
    "notices": sorted(notices.values(), key=lambda notice: notice["published"] or "", reverse=True),
}, ensure_ascii=False), encoding="utf-8")
