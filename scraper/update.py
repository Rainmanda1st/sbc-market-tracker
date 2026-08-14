from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "scraper" / "sources.json"
MARKET = ROOT / "site" / "data" / "market.json"
HISTORY = ROOT / "site" / "data" / "history.json"
UA = "SBC-Market-Tracker/1.0 (+GitHub Actions)"
TIMEOUT = 30
NOW = datetime.now(timezone.utc)
NOW_ISO = NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z")

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})


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
    if any(k in n for k in ("risc-v", "riscv", "starfive", "visionfive", "milk-v", "jh7110")):
        return "RISC-V"
    if any(k in n for k in ("x86", "intel", "celeron", "pentium", "core i", "amd", "ryzen", "lattepanda", "radxa x4")):
        return "x86"
    return "ARM64"


def infer_niche(name: str) -> str:
    n = name.lower()
    if any(k in n for k in ("ai", "npu", "hailo", "jetson", "beagley")):
        return "AI / accelerator"
    if any(k in n for k in ("router", "firewall", "r2s", "r4s", "r5s", "r6s", "gateway", "network")):
        return "Networking"
    if any(k in n for k in ("rk3588", "orange pi 5", "rock 5", "rock5", "cm5", "compute module 5")):
        return "High performance"
    if any(k in n for k in ("zero", "pico", "nano", "cubie", "tiny")):
        return "Compact / general"
    if any(k in n for k in ("nas", "storage", "sata")):
        return "NAS / storage"
    if infer_architecture(name) == "RISC-V":
        return "RISC-V / development"
    return "General / server"


def parse_price(value):
    if value is None:
        return None
    m = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def get_fx_to_cad() -> dict[str, float]:
    rates = {"CAD": 1.0}
    url = "https://www.bankofcanada.ca/valet/observations/FXUSDCAD,FXGBPCAD/json?recent=1"
    try:
        data = session.get(url, timeout=TIMEOUT).json()
        obs = (data.get("observations") or [{}])[-1]
        for currency, key in (("USD", "FXUSDCAD"), ("GBP", "FXGBPCAD")):
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


def shopify_products(source: dict) -> list[dict]:
    endpoint = source["url"].rstrip("/") + "/products.json?limit=250"
    r = session.get(endpoint, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    out = []
    for p in data.get("products", []):
        title = clean_text(p.get("title"))
        if not title:
            continue
        product_url = urljoin(source["url"], "/products/" + str(p.get("handle", "")))
        variants = p.get("variants") or [{}]
        for v in variants:
            price = parse_price(v.get("price"))
            if price is None or price <= 0:
                continue
            variant = clean_text(v.get("title"))
            full = title if variant.lower() in ("", "default title") else f"{title} {variant}"
            stock = "in" if v.get("available") is True else "out" if v.get("available") is False else "unknown"
            out.append({
                "name": title,
                "variant": "" if variant.lower() == "default title" else variant,
                "price": price,
                "currency": source["currency"],
                "stock": stock,
                "condition": "new",
                "retailer": source["name"],
                "region": source["region"],
                "architecture": infer_architecture(full),
                "niche": infer_niche(full),
                "url": product_url,
                "source_mode": "shopify"
            })
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


def generic_products(source: dict) -> list[dict]:
    r = session.get(source["url"], timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    seen = set()
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text())
        except Exception:
            continue
        for p in walk_jsonld(data):
            name = clean_text(p.get("name"))
            if not name:
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
                key = (name, price, product_url)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "name": name,
                    "variant": clean_text(p.get("sku")),
                    "price": price,
                    "currency": currency,
                    "stock": stock,
                    "condition": "new",
                    "retailer": source["name"],
                    "region": source["region"],
                    "architecture": infer_architecture(name),
                    "niche": infer_niche(name),
                    "url": urljoin(source["url"], str(product_url)),
                    "source_mode": "json-ld"
                })
    return out


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


def main():
    sources = read_json(SOURCES, [])
    previous = read_json(MARKET, {"listings": []})
    old_rows = previous.get("listings", [])
    old_by_id = {x.get("id"): x for x in old_rows if x.get("id")}
    old_by_retailer = {}
    for x in old_rows:
        old_by_retailer.setdefault(x.get("retailer"), []).append(x)

    fx = get_fx_to_cad()
    final = []
    source_names = {s["name"] for s in sources}

    # Keep manual/non-configured sources, such as the Amazon.ca seed listing.
    for row in old_rows:
        if row.get("retailer") not in source_names:
            final.append(row)

    for source in sources:
        try:
            adapter = source.get("adapter", "generic")
            scraped = shopify_products(source) if adapter == "shopify" else generic_products(source)
            if not scraped:
                raise RuntimeError("source returned zero product listings")
            normalized = normalize(scraped, old_by_id, fx)
            final.extend(normalized)
            print(f"{source['name']}: {len(normalized)} listings")
        except Exception as exc:
            retained = old_by_retailer.get(source.get("name"), [])
            final.extend(retained)
            print(f"{source.get('name')}: FAILED ({exc}); retained {len(retained)} previous listings")

    # Convert retained/manual listings to CAD when possible.
    for row in final:
        if row.get("price_cad") is None and row.get("price") is not None:
            rate = fx.get(str(row.get("currency", "")).upper())
            if rate:
                row["price_cad"] = round(float(row["price"]) * rate, 2)

    # Stable de-duplication.
    deduped = {}
    for row in final:
        key = row.get("id") or hashlib.sha1(json.dumps(row, sort_keys=True).encode()).hexdigest()[:16]
        deduped[key] = row
    final = sorted(deduped.values(), key=lambda x: (x.get("price_cad") is None, x.get("price_cad") or 10**9, x.get("name", "")))

    MARKET.parent.mkdir(parents=True, exist_ok=True)
    MARKET.write_text(json.dumps({"updated_at": NOW_ISO, "listings": final}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    history = load_previous_history()
    series = history.setdefault("series", {})
    cutoff = NOW - timedelta(days=730)
    for row in final:
        item_id = row.get("id")
        if not item_id:
            continue
        points = series.setdefault(item_id, [])
        points = [p for p in points if datetime.fromisoformat(p["at"].replace("Z", "+00:00")) >= cutoff]
        point = {"at": NOW_ISO, "price_cad": row.get("price_cad"), "price": row.get("price"), "currency": row.get("currency"), "stock": row.get("stock")}
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
    print(f"Wrote {len(final)} total listings")


if __name__ == "__main__":
    main()
