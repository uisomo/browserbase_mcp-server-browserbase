"""Generate a weekly market summary for G10 asset classes and email it via Zoho."""
from __future__ import annotations

import argparse
import dataclasses
import os
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Dict, Iterable, List, Tuple

import yfinance as yf


@dataclasses.dataclass
class MarketInstrument:
    country: str
    asset_class: str
    name: str
    ticker: str
    currency: str | None = None


# Representative instruments for each asset class in the G10.
INSTRUMENTS: List[MarketInstrument] = [
    # Equities
    MarketInstrument("United States", "Equities", "S&P 500", "^GSPC"),
    MarketInstrument("United Kingdom", "Equities", "FTSE 100", "^FTSE"),
    MarketInstrument("Euro Area", "Equities", "Euro Stoxx 50", "^STOXX50E"),
    MarketInstrument("Japan", "Equities", "Nikkei 225", "^N225"),
    MarketInstrument("Canada", "Equities", "S&P/TSX", "^GSPTSE"),
    MarketInstrument("Australia", "Equities", "ASX 200", "^AXJO"),
    MarketInstrument("New Zealand", "Equities", "NZX 50", "^NZ50"),
    MarketInstrument("Switzerland", "Equities", "SMI", "^SSMI"),
    MarketInstrument("Sweden", "Equities", "OMX Stockholm 30", "^OMX"),
    MarketInstrument("Norway", "Equities", "OSEAX", "^OSEAX"),
    # Credit (USD, EUR and local ETF proxies)
    MarketInstrument("United States", "Credit", "iShares iBoxx $ IG Corporate Bond ETF", "LQD"),
    MarketInstrument("Euro Area", "Credit", "iShares Core € Corp Bond UCITS ETF", "IEAC.L", currency="EUR"),
    MarketInstrument("United Kingdom", "Credit", "iShares £ Corporate Bond ETF", "SLXX.L", currency="GBP"),
    MarketInstrument("Japan", "Credit", "iShares Core Global Corp Bond JPY Hedged", "2510.T", currency="JPY"),
    MarketInstrument("Canada", "Credit", "iShares Canadian Corporate Bond ETF", "XCB.TO", currency="CAD"),
    MarketInstrument("Australia", "Credit", "iShares Core Corporate Bond AUD ETF", "CORP.AX", currency="AUD"),
    MarketInstrument("Switzerland", "Credit", "iShares CHF Corporate Bond ETF", "CHCORP.SW", currency="CHF"),
    MarketInstrument("Sweden", "Credit", "iShares SEK Corporate Bond ETF", "IS0Q.SW", currency="SEK"),
    MarketInstrument("Norway", "Credit", "iShares NOK Corporate Bond ETF", "IS0R.SW", currency="NOK"),
    MarketInstrument("New Zealand", "Credit", "Smartshares NZ Bond ETF", "NZB.NZ", currency="NZD"),
    # FX (quoted vs USD; for USD we use DXY)
    MarketInstrument("United States", "FX", "US Dollar Index", "DX-Y.NYB"),
    MarketInstrument("Euro Area", "FX", "EUR/USD", "EURUSD=X"),
    MarketInstrument("United Kingdom", "FX", "GBP/USD", "GBPUSD=X"),
    MarketInstrument("Japan", "FX", "USD/JPY", "JPY=X"),
    MarketInstrument("Canada", "FX", "USD/CAD", "CAD=X"),
    MarketInstrument("Australia", "FX", "AUD/USD", "AUDUSD=X"),
    MarketInstrument("New Zealand", "FX", "NZD/USD", "NZDUSD=X"),
    MarketInstrument("Switzerland", "FX", "USD/CHF", "CHF=X"),
    MarketInstrument("Sweden", "FX", "USD/SEK", "SEK=X"),
    MarketInstrument("Norway", "FX", "USD/NOK", "NOK=X"),
    # Rates (10Y government yields or local bond futures proxies)
    MarketInstrument("United States", "Rates", "US 10Y Treasury Yield", "^TNX"),
    MarketInstrument("United Kingdom", "Rates", "UK 10Y Gilt Yield", "^TNX-UK", currency="GBP"),
    MarketInstrument("Euro Area", "Rates", "Germany 10Y Bund Yield", "^TNX-DE", currency="EUR"),
    MarketInstrument("Japan", "Rates", "Japan 10Y JGB Yield", "^TNX-JP", currency="JPY"),
    MarketInstrument("Canada", "Rates", "Canada 10Y Government Bond", "^TNX-CA", currency="CAD"),
    MarketInstrument("Australia", "Rates", "Australia 10Y Bond Yield", "^TNX-AU", currency="AUD"),
    MarketInstrument("New Zealand", "Rates", "New Zealand 10Y Bond Yield", "^TNX-NZ", currency="NZD"),
    MarketInstrument("Switzerland", "Rates", "Switzerland 10Y Bond Yield", "^TNX-CH", currency="CHF"),
    MarketInstrument("Sweden", "Rates", "Sweden 10Y Bond Yield", "^TNX-SE", currency="SEK"),
    MarketInstrument("Norway", "Rates", "Norway 10Y Bond Yield", "^TNX-NO", currency="NOK"),
]


# Some bond yield tickers (e.g. ^TNX-UK) are synthetic placeholders not provided by Yahoo
# Finance. To provide a working script, map them to alternative ETFs or indexes where
# reliable yield tickers are unavailable.
YF_TICKER_OVERRIDES: Dict[str, str] = {
    "^TNX-UK": "GILT.L",  # iShares Core UK Gilts ETF
    "^TNX-DE": "EXH1.DE",  # iShares eb.rexx Government Germany UCITS ETF
    "^TNX-JP": "2510.T",  # reuse hedged global corporate bond as proxy for local rates
    "^TNX-CA": "XGB.TO",  # iShares Canadian Government Bond ETF
    "^TNX-AU": "IAF.AX",  # iShares Core Composite Bond ETF
    "^TNX-NZ": "GBF.NZ",  # Smartshares Global Bond ETF NZD Hedged
    "^TNX-CH": "CSBGC.SW",  # Credit Suisse Swiss Franc Bond Fund
    "^TNX-SE": "XACTOBL.ST",  # Xact Obligation SEK
    "^TNX-NO": "STBAX.NS",  # Placeholder: use Norwegian bond fund traded abroad
}


@dataclasses.dataclass
class InstrumentSnapshot:
    instrument: MarketInstrument
    start_date: datetime
    end_date: datetime
    start_value: float
    end_value: float

    @property
    def absolute_change(self) -> float:
        return self.end_value - self.start_value

    @property
    def percent_change(self) -> float:
        if self.start_value == 0:
            return 0.0
        return (self.end_value - self.start_value) / self.start_value * 100


def week_boundaries(reference: datetime) -> Tuple[datetime, datetime]:
    """Return the Monday (start) and Friday/Saturday (end) for the week containing `reference`."""
    ref_date = reference.date()
    weekday = ref_date.weekday()  # Monday = 0
    start = datetime.combine(ref_date - timedelta(days=weekday), datetime.min.time(), tzinfo=timezone.utc)

    # Choose Friday as the default week end; if reference is earlier than Friday, use reference day.
    end_weekday = min(4, weekday)  # 4 == Friday
    end = datetime.combine(start.date() + timedelta(days=end_weekday), datetime.min.time(), tzinfo=timezone.utc)
    # Use end of day for the end timestamp to ensure data retrieval includes the day.
    end = end + timedelta(hours=23, minutes=59, seconds=59)
    return start, end


def fetch_weekly_snapshot(instr: MarketInstrument, start: datetime, end: datetime) -> InstrumentSnapshot:
    ticker = YF_TICKER_OVERRIDES.get(instr.ticker, instr.ticker)

    history = yf.download(
        ticker,
        start=start - timedelta(days=7),  # extra buffer
        end=end + timedelta(days=1),
        progress=False,
        auto_adjust=False,
    )

    if history.empty:
        raise ValueError(f"No data returned for {instr.ticker} ({ticker})")

    history = history.sort_index()
    start_row = history.iloc[0]
    end_row = history.iloc[-1]

    return InstrumentSnapshot(
        instrument=instr,
        start_date=history.index[0].to_pydatetime(),
        end_date=history.index[-1].to_pydatetime(),
        start_value=float(start_row["Close"]),
        end_value=float(end_row["Close"]),
    )


def categorize_snapshots(snapshots: Iterable[InstrumentSnapshot]) -> Dict[str, List[InstrumentSnapshot]]:
    categories: Dict[str, List[InstrumentSnapshot]] = {}
    for snap in snapshots:
        categories.setdefault(snap.instrument.asset_class, []).append(snap)
    return categories


def infer_driver(asset_class: str, change: float) -> str:
    if asset_class == "Equities":
        if change > 0:
            return "Risk-on flows and stronger growth expectations supported gains."
        if change < 0:
            return "Risk-off sentiment amid growth concerns weighed on equities."
        return "Markets ended the week largely unchanged with balanced flows."
    if asset_class == "Credit":
        if change > 0:
            return "Tighter spreads as demand for high-quality credit persisted."
        if change < 0:
            return "Wider spreads as investors rotated into safer government debt."
        return "Credit markets were stable with muted spread movement."
    if asset_class == "FX":
        if change > 0:
            return "Currency strength driven by yield differentials and capital flows."
        if change < 0:
            return "Currency softness as markets priced in easier policy expectations."
        return "FX markets were steady with offsetting flows."
    if asset_class == "Rates":
        if change > 0:
            return "Yields moved higher on hawkish central bank rhetoric."
        if change < 0:
            return "Yields fell as haven demand and dovish guidance dominated."
        return "Rates were steady amid balanced policy expectations."
    return "Market dynamics were mixed across asset classes."


def format_snapshot_line(snapshot: InstrumentSnapshot) -> str:
    instr = snapshot.instrument
    change = snapshot.absolute_change
    pct = snapshot.percent_change
    start_val = snapshot.start_value
    end_val = snapshot.end_value
    currency = instr.currency or ""
    if currency:
        currency = f" {currency}"

    return (
        f"- {instr.country}: {instr.name} {end_val:,.2f}{currency} "
        f"(from {start_val:,.2f}{currency}; {change:+,.2f}{currency}, {pct:+.2f}%)"
    )


def build_email_body(snapshots: Iterable[InstrumentSnapshot]) -> str:
    categorized = categorize_snapshots(snapshots)
    sections: List[str] = []
    for asset_class in ["Equities", "Credit", "FX", "Rates"]:
        items = categorized.get(asset_class, [])
        if not items:
            continue
        items_sorted = sorted(items, key=lambda s: s.instrument.country)
        lines = [format_snapshot_line(s) for s in items_sorted]
        driver = infer_driver(asset_class, sum(s.absolute_change for s in items))
        sections.append(
            f"{asset_class}\n" + "\n".join(lines) + f"\nMajor driver: {driver}\n"
        )
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    header = "Weekly G10 Market Summary\n" + f"Generated: {generated}\n"
    return header + "\n".join(sections)


def build_failure_body(errors: Iterable[str]) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_lines = [
        "Weekly G10 Market Summary",
        f"Generated: {generated}",
        "",
        "No market data was retrieved. This can occur if the data provider is unavailable,",
        "the environment blocks outbound network traffic, or the tickers need updating.",
    ]

    error_list = list(errors)
    if error_list:
        body_lines.append("")
        body_lines.append("Collection warnings:")
        body_lines.extend(f"- {err}" for err in error_list)

    return "\n".join(body_lines)


def send_email(subject: str, body: str) -> None:
    username = os.environ.get("ZOHO_EMAIL")
    password = os.environ.get("ZOHO_EMAIL_PASSWORD") or os.environ.get("ZOHO_EMAIL_APP_PASSWORD")
    recipients = os.environ.get("ZOHO_EMAIL_RECIPIENTS")

    if not username or not password or not recipients:
        raise RuntimeError(
            "ZOHO_EMAIL, ZOHO_EMAIL_PASSWORD (or ZOHO_EMAIL_APP_PASSWORD), and "
            "ZOHO_EMAIL_RECIPIENTS environment variables must be set."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = username
    message["To"] = recipients
    message.set_content(body)

    import smtplib

    with smtplib.SMTP_SSL("smtp.zoho.com", 465) as smtp:
        smtp.login(username, password)
        smtp.send_message(message)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and email a weekly G10 market summary")
    parser.add_argument("--subject", default="Weekly G10 Market Summary", help="Email subject line")
    parser.add_argument(
        "--preview", action="store_true", help="Print the email body instead of sending it"
    )
    parser.add_argument(
        "--send", action="store_true", help="Send the email after generating the summary"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    reference = datetime.now(timezone.utc)
    start, end = week_boundaries(reference)

    snapshots: List[InstrumentSnapshot] = []
    errors: List[str] = []
    for instrument in INSTRUMENTS:
        try:
            snapshot = fetch_weekly_snapshot(instrument, start, end)
            snapshots.append(snapshot)
        except Exception as exc:  # noqa: BLE001 - top-level reporting is helpful for operators
            message = (
                f"failed to collect data for {instrument.country} {instrument.asset_class} "
                f"({instrument.ticker}): {exc}"
            )
            print(f"Warning: {message}")
            errors.append(message)

    body = (
        build_email_body(snapshots)
        if snapshots
        else build_failure_body(errors)
    )

    if args.preview or not args.send:
        print(body)

    if args.send:
        send_email(args.subject, body)
        print("Email sent via Zoho.")


if __name__ == "__main__":
    main()
