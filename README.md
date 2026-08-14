# SBC Market Tracker

Phone-first SBC price and stock tracker, deployed with GitHub Pages and refreshed automatically by GitHub Actions.

## Features
- Filter by CAD price, stock, condition, architecture, niche, retailer and region.
- Shows recently discovered listings.
- Keeps historical price/stock observations and price charts.
- Scheduled GitHub Action refreshes data every 6 hours.
- Filters obvious accessories and microcontroller-only boards out of SBC results.
- Uses credential-free public retailer pages/feeds only for now.

## Live site
`https://rainmanda1st.github.io/sbc-market-tracker/`

## Active credential-free coverage
The active source list is in `scraper/sources.json`. Current sources include:
- ameriDroid
- PiShop.ca
- The Pi Hut
- Pimoroni
- Hardkernel / ODROID
- FriendlyELEC
- PINE64

Retailer pages change over time. If a source temporarily fails, the updater keeps the last known valid SBC rows for that source instead of deleting them immediately.

## Reserved future integrations
`scraper/sources.json` already contains disabled placeholders for **Amazon.ca** and **AliExpress**. They do not require, request or use credentials while disabled. Their entries include the future secret names so authenticated marketplace adapters can be added later without redesigning the tracker.

RobotShop Canada is also kept as a disabled source entry because its collection currently loads dynamically and its normal Shopify product feed returns HTTP 403 from GitHub Actions. It can be re-enabled later if a stable public feed becomes available.

## Deploy / run manually
1. Open **Settings → Pages** and use **GitHub Actions** as the source.
2. Open **Actions → Update SBC prices and deploy**.
3. Choose **Run workflow**.
4. When it finishes, open the live site above.

The workflow also runs automatically every 6 hours.

## Add another credential-free retailer
Add an entry to `scraper/sources.json` using either the `shopify` or `generic` adapter. The generic adapter supports JSON-LD product data and common server-rendered product cards with pagination.

## Accuracy
Prices and stock can change between checks, and shipping/taxes are not included unless a retailer itself includes them in the displayed price. Always confirm the final seller, shipping, stock and checkout price before buying.
