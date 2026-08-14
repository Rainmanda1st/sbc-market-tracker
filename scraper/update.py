from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "scraper" / "sources.json"
MARKET = ROOT / "site" / "data" / "market.json"
HISTORY = ROOT / "site" / "data" / "history.json"
UA = "SBC-Market-Tracker/1.1 (+GitHub Actions; public-retail pages only)"
TIMEOUT = 30
NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z")

session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
})


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def slug(value: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return s[:90] or hashlib.sha1(value.encode()).hexdigest()[:12]


def infer_architecture(name: str) -> str:
    n = name.lower()
    if any(k in n for k in (
        "risc-v", "riscv", "starfive", "visionfive", "milk-v", "jh7110",
        "star64", "starpro64", "ox64", "oz64", "lichee", "orange pi rv",
    )):
        return "RISC-V"
    if any(k in n for k in (
        "x86", "intel", "celeron", "pentium", "core i", "amd", "ryzen",
        "lattepanda", "radxa x4", "up board",
    )) or re.search(r"\bodroid-h\d", n):
        return "x86"
    return "ARM64"


def infer_niche(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("ai", "npu", "hailo", "jetson", "beagley", "dragon q")):
        return "AI / accelerator"
    if any(k in n for k in (
        "router", "firewall", "r2s", "r3s", "r4s", "r5s", "r6s", "r76s",
        "gateway", "network", "rock pi e",
    )):
        return "Networking"
    if any(k in n for k in ("nas", "storage", "sata", "hc4")):
        return "NAS / storage"
    if infer_architecture(name) == "x86":
        return "x86 / mini server"
    if any(k in n for k in (
        "rk3588", "rk3576", "orange pi 5", "rock 5", "rock5", "cm5",
        "compute module 5", "quartzpro64", "starpro64",
    )):
        return "High performance"
    if any(k in n for k in ("zero", "nano", "cubie", "tiny", "ox64", "oz64")):
        return "Compact / general"
    if infer_architecture(name) == "RISC-V":
        return "RISC-V / development"
    return "General / server"


def infer_condition(name: str) -> str:
    n = name.lower()
    if "refurb" in n:
        return "refurbished"
    if "open box" in n or "open-box" in n:
        return "open-box"
    if re.search(r"\bused\b", n):
        return "used"
    return "new"


ACCESSORY_TERMS = (
    "case", "enclosure", "heatsink", "heat sink", "cooler", "cooling fan",
    "power supply", "power adapter", "adapter cable", "usb cable", "hdmi cable",
    "display", "screen", "camera", "sensor", " hat", "hat+", "shield",
    "keyboard", "mouse", "mount", "rack panel", "bracket", "starter kit",
    "desktop kit", "bundle", "sd card", "microsd", "ssd", "emmc module",
    "antenna", "io board", "i/o board", "carrier board", "expansion board",
    "add-on board", "add on board", "gpio board", "header set", "connector",
    "battery", "charger", "switching power", "usb hub", "pcie board", "m.2 board",
    "sata card", "memory card", "remote", "lens", "speaker", "dac", "amplifier",
    "relay board", "ups", "housing", "protective", "thermal pad",
)


def is_probable_sbc(name: str) -> bool:
    n = clean_text(name).lower()
    if not n:
        return False
    if any(term in n for term in ACCESSORY_TERMS):
        return False
    if any(k in n for k in ("raspberry pi pico", "rp2040", "rp2350", "arduino", "esp32", "micro:bit")):
        return False

    strong = (
        "single board computer", "single-board computer",
        "orange pi", "banana pi", "nanopi", "nanopc", "odroid-",
        "rockpro64", "rock64", "quartz64", "quartzpro64", "star64", "starpro64",
        "pine a64", "pine64 board", "oz64", "ox64", "avaota",
        "visionfive", "milk-v", "lichee", "beaglebone", "beagley",
        "khadas", "libre computer", "lattepanda", "tinker board",
        "jetson developer kit", "up board",
        "radxa zero", "radxa x4", "rock 2", "rock 3", "rock 4", "rock 5", "rock pi",
        "cubie a", "dragon q", "orion o6", "sbc",
    )
    if any(k in n for k in strong):
        return True

    if "raspberry pi" in n:
        return bool(re.search(
            r"raspberry pi\s+(?:zero(?:\s*2)?(?:\s*w)?|[2345](?:\s|/|-|$)|400\b|500\+?\b|compute module)",
            n,
        ))
    return False


def parse_price(value):
    if value is None:
        return None
    matches = re.findall(r"(?:CA\$|US\$|C\$|\$|£|€)?\s*(-?\d[\d,]*(?:\.\d+)?)", str(value))
    if not matches:
        return None
    try:
        return float(matches[-1].replace(",", ""))
    except ValueError:
        return None


def stock_from_text(value: str) -> str:
    t = clean_text(value).lower()
    if any(k in t for k in ("out of stock", "sold out", "notify me", "coming soon", "re-stocking soon", "restocking soon")):
        return "out"
    if any(k in t for k in ("in stock", "add to cart", "choose options", "add to basket")):
        return "in"
    return "unknown"


def get_fx_to_cad() -> dict[str, float]:
    rates = {"CAD": 1.0}
    url = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD,FXGBPCAD,FXEURCAD/json?recent=1"
    try:
        data = session.get(url, timeout=TIMEOUT).json()
        obs = (data.get("observations") or [{}])[-1]
        for currency, key in (("USD", "FXUSDCAD"), ("GBP", "FXGBPCAD"), ("EUR", "FXEURCAD")):
            value = (obs.get(key) or {}).get("v")
            if value:
                rates[currency] = float(value)
    except Exception as exc:
        print(f"FX warning: {exc}")
    return rates


def load_previous_history():
    local = read_json(HISTORY, None)
    if local:
        return local
    remote = os.environ.get("PAGES_HISTORY_URL")
    if remote:
        try:
            r = session.get(remote, timeout=15)
            if r.ok:
                return r.json()
        except Exception as exc:
            print(f"History restore warning: {exc}")
    return {"updated_at": None, "series": {}}


def make_row(source: dict, name: str, variant: str, price: float, currency: str,
             stock: str, product_url: str, source_mode: str) -> dict:
    full = f"{name} {variant}".strip()
    return {
        "name": clean_text(name),
        "variant": clean_text(variant),
        "price": float(price),
        "currency": clean_text(currency).upper() or source["currency"],
        "stock": stock,
        "condition": infer_condition(full),
        "retailer": source["name"],
        "region": source["region"],
        "architecture": infer_architecture(full),
        "niche": infer_niche(full),
        "url": product_url,
        "source_mode": source_mode,
    }


def shopify_products(source: dict) -> list[dict]:
    out = []
    max_pages = int(source.get("max_pages", 8))
    base = source["url"].rstrip("/")
    for page in range(1, max_pages + 1):
        endpoint = f"{base}/products.json?limit=250&page={page}"
        r = session.get(endpoint, timeout=TIMEOUT)
        r.raise_for_status()
        products = (r.json() or {}).get("products", [])
        if not products:
            break
        for p in products:
            title = clean_text(p.get("title"))
            if not title:
                continue
            product_url = urljoin(source["url"], "/products/" + str(p.get("handle", "")))
            variants = p.get("variants") or [{}]
            for v in variants:
                variant = clean_text(v.get("title"))
                full = title if variant.lower() in ("", "default title") else f"{title} {variant}"
                if not is_probable_sbc(full):
                    continue
                price = parse_price(v.get("price"))
                if price is None or price <= 0:
                    continue
                stock = "in" if v.get("available") is True else "out" if v.get("available") is False else "unknown"
                out.append(make_row(
                    source, title, "" if variant.lower() == "default title" else variant,
                    price, source["currency"], stock, product_url, "shopify",
                ))
        if len(products) < 250:
            break
    return out


def walk_jsonld(node):
    if isinstance(node, list):
        for item in node:
            yield from walk_jsonld(item)
    elif isinstance(node, dict):
        typ = node.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if "Product" in types:
            yield node
        for value in node.values():
            if isinstance(value, (dict, list)):
                yield from walk_jsonld(value)


def rows_from_jsonld(source: dict, soup: BeautifulSoup) -> list[dict]:
    out = []
    seen = set()
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        for p in walk_jsonld(data):
            name = clean_text(p.get("name"))
            if not name or not is_probable_sbc(name):
                continue
            offers = p.get("offers") or {}
            offers = offers if isinstance(offers, list) else [offers]
            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                price = parse_price(offer.get("price") or (offer.get("priceSpecification") or {}).get("price"))
                if price is None or price <= 0:
                    continue
                currency = clean_text(offer.get("priceCurrency")) or source["currency"]
                avail = clean_text(offer.get("availability")).lower()
                stock = "out" if "outofstock" in avail else "in" if "instock" in avail else "unknown"
                product_url = offer.get("url") or p.get("url") or source["url"]
                key = (name, price, str(product_url))
                if key in seen:
                    continue
                seen.add(key)
                out.append(make_row(
                    source, name, clean_text(p.get("sku")), price, currency, stock,
                    urljoin(source["url"], str(product_url)), "json-ld",
                ))
    return out


def nearest_product_container(anchor):
    node = anchor
    for _ in range(7):
        if node is None:
            break
        text = clean_text(node.get_text(" ", strip=True))
        if re.search(r"(?:CA\$|US\$|C\$|\$|£|€)\s*\d", text):
            return node
        node = node.parent
    return anchor.parent


def rows_from_cards(source: dict, soup: BeautifulSoup) -> list[dict]:
    out = []
    seen_urls = set()
    source_host = urlparse(source["url"]).netloc
    for a in soup.find_all("a", href=True):
        name = clean_text(a.get_text(" ", strip=True))
        if not is_probable_sbc(name):
            continue
        product_url = urljoin(source["url"], a["href"])
        if urlparse(product_url).netloc != source_host:
            continue
        if product_url in seen_urls:
            continue
        container = nearest_product_container(a)
        text = clean_text(container.get_text(" ", strip=True)) if container else name
        price = parse_price(text)
        if price is None or price <= 0:
            continue
        seen_urls.add(product_url)
        out.append(make_row(
            source, name, "", price, source["currency"], stock_from_text(text),
            product_url, "html-card",
        ))
    return out


def next_page_url(current_url: str, soup: BeautifulSoup):
    nxt = soup.find("a", rel=lambda v: v and "next" in v)
    if not nxt:
        nxt = soup.select_one("a.next, a.next.page-numbers, a[aria-label='Next'], a[aria-label='Next page']")
    if nxt and nxt.get("href"):
        return urljoin(current_url, nxt["href"])
    return None


def generic_products(source: dict) -> list[dict]:
    out = []
    page_url = source["url"]
    seen_pages = set()
    max_pages = int(source.get("max_pages", 4))
    for _ in range(max_pages):
        if not page_url or page_url in seen_pages:
            break
        seen_pages.add(page_url)
        r = session.get(page_url, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        page_rows = rows_from_jsonld(source, soup)
        if not page_rows:
            page_rows = rows_from_cards(source, soup)
        out.extend(page_rows)
        page_url = next_page_url(page_url, soup)

    deduped = {}
    for row in out:
        key = (row["name"].lower(), row["variant"].lower(), row["url"], row["price"])
        deduped[key] = row
    return list(deduped.values())


def normalize(rows: list[dict], old_by_id: dict, fx: dict[str, float]) -> list[dict]:
    result = []
    for row in rows:
        identity = f"{row.get('retailer','')}|{row.get('name','')}|{row.get('variant','')}|{row.get('url','')}"
        item_id = slug(row.get("retailer", "source")) + "-" + hashlib.sha1(identity.encode()).hexdigest()[:12]
        old = old_by_id.get(item_id, {})
        row["id"] = item_id
        row["first_seen"] = old.get("first_seen") or NOW_ISO
        row["checked_at"] = NOW_ISO
        rate = fx.get(str(row.get("currency", "")).upper())
        row["price_cad"] = round(float(row["price"]) * rate, 2) if rate else None
        result.append(row)
    return result


def scrape_source(source: dict) -> list[dict]:
    adapter = source.get("adapter", "generic")
    if adapter == "shopify":
        return shopify_products(source)
    if adapter == "generic":
        return generic_products(source)
    raise RuntimeError(f"unsupported adapter: {adapter}")


def main():
    configured = read_json(SOURCES, [])
    sources = [s for s in configured if s.get("enabled", True)]
    disabled = [s for s in configured if not s.get("enabled", True)]
    for source in disabled:
        print(f"{source.get('name')}: disabled ({source.get('disabled_reason', 'not active')})")

    previous = read_json(MARKET, {"listings": []})
    old_rows = previous.get("listings", [])
    old_by_id = {x.get("id"): x for x in old_rows if x.get("id")}
    old_by_retailer = {}
    for x in old_rows:
        old_by_retailer.setdefault(x.get("retailer"), []).append(x)

    fx = get_fx_to_cad()
    final = []
    active_names = {s["name"] for s in sources}
    configured_names = {s["name"] for s in configured}

    # Keep manual/non-configured sources, including manually seeded marketplace listings.
    for row in old_rows:
        if row.get("retailer") not in configured_names:
            final.append(row)

    for source in sources:
        try:
            scraped = scrape_source(source)
            if not scraped:
                raise RuntimeError("source returned zero SBC listings")
            normalized = normalize(scraped, old_by_id, fx)
            final.extend(normalized)
            print(f"{source['name']}: {len(normalized)} SBC listings")
        except Exception as exc:
            retained = old_by_retailer.get(source.get("name"), [])
            retained = [x for x in retained if is_probable_sbc(f"{x.get('name','')} {x.get('variant','')}")]
            final.extend(retained)
            print(f"{source.get('name')}: FAILED ({exc}); retained {len(retained)} previous SBC listings")

    # Do not carry rows from now-disabled configured sources into the live market.
    final = [x for x in final if x.get("retailer") in active_names or x.get("retailer") not in configured_names]

    for row in final:
        if row.get("price_cad") is None and row.get("price") is not None:
            rate = fx.get(str(row.get("currency", "")).upper())
            if rate:
                row["price_cad"] = round(float(row["price"]) * rate, 2)

    deduped = {}
    for row in final:
        key = row.get("id") or hashlib.sha1(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]
        deduped[key] = row
    final = sorted(
        deduped.values(),
        key=lambda x: (x.get("price_cad") is None, x.get("price_cad") or 10**9, x.get("name", "")),
    )

    MARKET.parent.mkdir(parents=True, exist_ok=True)
    MARKET.write_text(
        json.dumps({"updated_at": NOW_ISO, "listings": final}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    history = load_previous_history()
    series = history.setdefault("series", {})
    cutoff = NOW - timedelta(days=730)
    for row in final:
        item_id = row.get("id")
        if not item_id:
            continue
        points = series.setdefault(item_id, [])
        valid_points = []
        for p in points:
            try:
                if datetime.fromisoformat(p["at"].replace("Z", "+00:00")) >= cutoff:
                    valid_points.append(p)
            except Exception:
                pass
        points = valid_points
        point = {
            "at": NOW_ISO,
            "price_cad": row.get("price_cad"),
            "price": row.get("price"),
            "currency": row.get("currency"),
            "stock": row.get("stock"),
        }
        if not points or points[-1].get("price_cad") != point["price_cad"] or points[-1].get("stock") != point["stock"]:
            points.append(point)
        else:
            try:
                last_at = datetime.fromisoformat(points[-1]["at"].replace("Z", "+00:00"))
                if NOW - last_at >= timedelta(hours=24):
                    points.append(point)
            except Exception:
                points.append(point)
        series[item_id] = points
    history["updated_at"] = NOW_ISO
    HISTORY.write_text(json.dumps(history, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(final)} total SBC listings")


if __name__ == "__main__":
    main()
