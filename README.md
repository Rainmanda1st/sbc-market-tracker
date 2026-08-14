# SBC Market Tracker

Phone-first SBC price and stock tracker.

## Features
- Filter by CAD price, stock, condition, architecture, niche, retailer and region.
- Shows recently discovered listings.
- Keeps historical price/stock observations.
- Scheduled GitHub Action refreshes data every 6 hours.
- GitHub Pages hosts the mobile interface.

## Deploy
1. In this repository open **Settings → Pages**.
2. Set **Source** to **GitHub Actions**.
3. Open **Actions → Update SBC prices and deploy → Run workflow**.
4. When it finishes, open the Pages URL on your phone.

See `SETUP.html` for the phone-friendly setup guide.

## Coverage
Coverage comes from `scraper/sources.json`. Retailers and marketplaces change their pages often, so a source may occasionally fail until its adapter is updated. Always confirm final seller, shipping, stock and price before buying.
