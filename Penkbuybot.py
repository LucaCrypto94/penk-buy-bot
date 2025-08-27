import os
import telegram
from telegram.ext import CommandHandler, MessageHandler
import asyncio
import requests
from datetime import datetime, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from tenacity import retry, wait_fixed, stop_after_attempt
from collections import deque

# ─────────────────────────────
# Configuration (via Env Vars)
# ─────────────────────────────
TELEGRAM_TOKEN = os.getenv("8438467171:AAGtRIvbecoG4EzE01nlK2jNVWwazcRbvrU")
CHAT_ID = os.getenv("-2843689356")
THREAD_MESSAGE_ID = os.getenv("THREAD_MESSAGE_ID", "1")

POOL_ADDRESS = os.getenv("POOL_ADDRESS", "0x71942200c579319c89c357b55a9d5c0e0ad2403e").lower()
TOKEN_ADDRESS = os.getenv("TOKEN_ADDRESS", "0x82144c93bd531e46f31033fe22d1055af17a514c").lower()
TOKEN_SYMBOL = os.getenv("TOKEN_SYMBOL", "PENK")
MIN_TOKEN_AMOUNT = int(os.getenv("MIN_TOKEN_AMOUNT", 1))
CIRCULATING_SUPPLY = int(os.getenv("CIRCULATING_SUPPLY", 1_000_000_000))

# Path opzionale per GIF/logo header
HEADER_GIF_PATH = os.getenv("HEADER_GIF_PATH", None)


# ─────────────────────────────
# Helper functions
# ─────────────────────────────
def shorten_address(address):
    """Shorten a wallet address for display, e.g. '0x1234...abcd'."""
    return address[:6] + "..." + address[-4:] if len(address) > 10 else address


def calculate_diamonds(total_buy, token_symbol):
    """Return a string of emojis based on the purchased token amount."""
    emoji = "🔍"
    if total_buy >= 10_000_000:
        return emoji * 100
    elif total_buy >= 1_000_000:
        return emoji * int(total_buy // 100_000)
    else:
        return ""


@retry(wait=wait_fixed(2), stop=stop_after_attempt(5))
def get_market_cap(pool_address):
    """Fetch market cap using GeckoTerminal's pool endpoint."""
    try:
        url = f"https://api.geckoterminal.com/api/v2/networks/pepe-unchained/pools/{pool_address}"
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()["data"]["attributes"]
        price_str = data.get("base_token_price_usd")
        if price_str:
            price = float(price_str)
            return price * CIRCULATING_SUPPLY
        else:
            return "N/A"
    except Exception as e:
        print("❌ Error fetching market cap:", e)
        return "N/A"


def build_buy_alert_message(
    buyer,
    quantity,
    price,
    market_cap,
    total,
    tx_hash,
    symbol,
    pool_address,
    token_address,
):
    """Build the HTML message body for a 'buy' alert."""
    buyer_link = f"<a href='https://pepuscan.com/address/{buyer}'>{shorten_address(buyer)}</a>"
    formatted_quantity = f"{quantity:,.0f}".replace(",", ".")
    formatted_mc = f"{float(market_cap):,.2f}" if isinstance(market_cap, (float, int)) else str(market_cap)

    header = (
        "Penk Holders" if total < 150
        else "Penk Diamond Hand" if total < 301
        else "TRUE PENKER" if total < 501
        else "FUTURE MILIONAIRE"
    )

    diamonds = calculate_diamonds(quantity, symbol)

    return (
        f"<b>{header}</b>\n"
        f"<b>💸 Coin: {symbol} </b>\n\n"
        f"🎯 Buyer {buyer_link}\n"
        f"💲 Price <code>$ {price:.8f}</code>\n"
        f"🛒 Total Buy <code>$ {total:.2f}</code>\n"
        f"🐸 Amount: <code>{formatted_quantity} {symbol}</code>\n"
        f"💰 Market Cap: <code>$ {formatted_mc}</code>\n\n"
        f"{diamonds}"
    )


async def send_telegram_alert(message, tx_hash=None, media_path=None):
    bot = telegram.Bot(token=TELEGRAM_TOKEN)

    keyboard = [
        [
            InlineKeyboardButton(
                "📈 Chart",
                url=f"https://www.geckoterminal.com/pepe-unchained/pools/{POOL_ADDRESS}",
            ),
            InlineKeyboardButton(
                "💸 Buy now",
                url=f"https://pepuswap.com//#/swap?inputCurrency=ETH&outputCurrency={TOKEN_ADDRESS}",
            ),
        ],
        [
            InlineKeyboardButton("🌉 Superbridge", url="https://superbridge.pepubank.net/"),
            InlineKeyboardButton("👨‍🎓 Advisor", url="https://web.telegram.org/k/#@Pepu_bank_bot"),
        ],
    ]
    if tx_hash:
        keyboard.append([
            InlineKeyboardButton(
                "🔗 TX HASH",
                url=f"https://pepuscan.com/tx/{tx_hash}",
            ),
        ])

    reply_markup = InlineKeyboardMarkup(keyboard)

    try:
        if media_path:
            with open(media_path, "rb") as f:
                await bot.send_animation(
                    chat_id=CHAT_ID,
                    animation=f,
                    caption=message,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    disable_web_page_preview=True
                )
        else:
            await bot.send_message(
                chat_id=CHAT_ID,
                text=message,
                parse_mode="HTML",
                reply_markup=reply_markup,
                disable_web_page_preview=True
            )
    except telegram.error.RetryAfter as e:
        print(f"⏳ Flood control – waiting {e.retry_after} seconds...")
        await asyncio.sleep(e.retry_after)
    except Exception as e:
        print("❌ Telegram send error:", e)


def get_latest_gecko_trades(min_usd=MIN_TOKEN_AMOUNT):
    """Retrieve latest trades from GeckoTerminal filtered by minimum USD volume."""
    url = f"https://api.geckoterminal.com/api/v2/networks/pepe-unchained/pools/{POOL_ADDRESS}/trades"
    params = {"trade_volume_in_usd_greater_than": min_usd}
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("data", [])
    except Exception as e:
        print("❌ Error fetching Gecko trades:", e)
        return []


async def monitor_gecko_trades():
    """Poll GeckoTerminal for new 'buy' trades and post them to Telegram."""
    print("🧪 GeckoTrade monitoring started...")
    processed_tx = deque(maxlen=1000)
    first_run = True

    while True:
        trades = get_latest_gecko_trades(min_usd=MIN_TOKEN_AMOUNT)
        print(f"🔍 Poll complete – received {len(trades)} trades")

        new_trade_detected = False
        found_count = 0

        for t in trades:
            attrs = t.get("attributes", {})
            tx_hash = attrs.get("tx_hash")
            if not tx_hash or tx_hash in set(processed_tx):
                continue

            if attrs.get("kind") != "buy":
                continue

            timestamp_str = attrs.get("block_timestamp")
            try:
                trade_time = datetime.strptime(timestamp_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                seconds_old = (now - trade_time).total_seconds()
            except Exception:
                seconds_old = 999_999

            if first_run and seconds_old > 60:
                processed_tx.append(tx_hash)
                continue

            buyer = attrs.get("tx_from_address")
            amount = float(attrs.get("to_token_amount", 0))
            total = float(attrs.get("volume_in_usd", 0))
            price = float(attrs.get("price_to_in_usd", 0))
            market_cap = price * CIRCULATING_SUPPLY

            if total < MIN_TOKEN_AMOUNT:
                processed_tx.append(tx_hash)
                continue

            msg = build_buy_alert_message(
                buyer=buyer,
                quantity=amount,
                price=price,
                market_cap=market_cap,
                total=total,
                tx_hash=tx_hash,
                symbol=TOKEN_SYMBOL,
                pool_address=POOL_ADDRESS,
                token_address=TOKEN_ADDRESS,
            )

            await send_telegram_alert(msg, tx_hash, media_path=HEADER_GIF_PATH)
            processed_tx.append(tx_hash)
            new_trade_detected = True
            found_count += 1
            await asyncio.sleep(1.5)

        if new_trade_detected:
            print(f"✅ Detected and sent {found_count} new buy(s)")

        if first_run:
            first_run = False

        await asyncio.sleep(15)


if __name__ == "__main__":
    if not TELEGRAM_TOKEN or not CHAT_ID or not POOL_ADDRESS or not TOKEN_ADDRESS:
        print("❌ Missing environment variables. Please check TELEGRAM_TOKEN, CHAT_ID, POOL_ADDRESS, TOKEN_ADDRESS.")
    else:
        asyncio.run(monitor_gecko_trades())

