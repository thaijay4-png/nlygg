import os
import random
import re
import asyncio
import httpx
from datetime import datetime

try:
    import nest_asyncio
except ImportError:
    nest_asyncio = None

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler, CallbackQueryHandler,
    CommandHandler, filters
)

load_dotenv()
generated_cache = {}
bin_cache = {}

def luhn_checksum(card_number: str) -> int:
    digits = [int(d) for d in card_number]
    checksum = 0
    is_even = True
    for d in reversed(digits):
        if is_even:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
        is_even = not is_even
    return (10 - checksum % 10) % 10

def get_flag_emoji(country_code):
    if not country_code:
        return ""
    return "".join(chr(127397 + ord(c)) for c in country_code.upper())

def generate_cc_full(bin_code, exp_month=None, exp_year=None):
    is_amex = bin_code.startswith(('34', '37'))

    if is_amex:
        target_length_pre_checksum = 14
        cvv_length = 4
    else:
        target_length_pre_checksum = 15
        cvv_length = 3

    rand_len = target_length_pre_checksum - len(bin_code)
    if rand_len < 0:
        rand_len = 0

    base = bin_code + ''.join(str(random.randint(0, 9)) for _ in range(rand_len))
    cc = base + str(luhn_checksum(base))
    
    now = datetime.now()
    current_year = now.year
    current_month = now.month

    if exp_year:
        final_year = int(exp_year)
    else:
        final_year = random.randint(current_year, current_year + 6)

    if exp_month:
        final_month = exp_month
    else:
        if final_year == current_year:
            final_month = f"{random.randint(current_month, 12):02d}"
        else:
            final_month = f"{random.randint(1, 12):02d}"

    if is_amex:
        cvv = f"{random.randint(0, 9999):04d}"
    else:
        cvv = f"{random.randint(0, 999):03d}"

    return f"{cc}|{final_month}|{final_year}|{cvv}"

def generate_txt(data):
    return "\n".join(data).encode('utf-8')

def generate_csv(data):
    header = "CC Number,Expiry Month,Expiry Year,CVV\n"
    rows = ["".join(item.split("|")) for item in data]
    return (header + "\n".join(rows)).encode('utf-8')

async def fetch_bin_info(bin_code: str) -> dict:
    if bin_code in bin_cache:
        return bin_cache[bin_code]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(f"https://data.handyapi.com/bin/{bin_code}")
            if res.status_code == 200:
                data = res.json()
                if data.get("Status") == "SUCCESS":
                    country_data = data.get("Country", {})
                    info = {
                        "scheme": (data.get("Scheme") or "Unknown").capitalize(),
                        "type": (data.get("Type") or "Unknown").capitalize(),
                        "brand": (data.get("CardTier") or "Unknown").capitalize(),
                        "bank": (data.get("Issuer") or "Unknown"),
                        "country": country_data.get("Name", "Unknown"),
                        "emoji": get_flag_emoji(country_data.get("A2"))
                    }
                    bin_cache[bin_code] = info
                    return info
    except Exception as e:
        print(f"BIN lookup failed: {e}")

    return {
        "scheme": "Unknown",
        "type": "Unknown",
        "brand": "Unknown",
        "bank": "Unknown",
        "country": "Unknown",
        "emoji": ""
    }

async def handle_gen(update: Update, context: ContextTypes.DEFAULT_TYPE, command_mode=False):
    if command_mode:
        text = " ".join(context.args)
    else:
        text = update.message.text

    bin_match = re.search(r"(?:\.gen|/gen)?\s*(\d{6,15})", text)
    count_match = re.search(r"x(\d{1,3})", text)
    exp_match = re.search(r"\b(\d{1,2})[|/](\d{2,4})\b", text)

    if not bin_match:
        await update.message.reply_text(
            "⚠️ Usage: `/gen <bin> x<qty> MM|YYYY`\nExample: `/gen 434769 09|28 x10`", 
            parse_mode="Markdown"
        )
        return

    bin_code = bin_match.group(1)
    count = min(int(count_match.group(1)) if count_match else 1, 50)
    
    exp_month, exp_year = None, None
    if exp_match:
        exp_month = exp_match.group(1).zfill(2)
        raw_year = exp_match.group(2)
        if len(raw_year) == 2:
            exp_year = "20" + raw_year
        else:
            exp_year = raw_year

    results = [generate_cc_full(bin_code, exp_month, exp_year) for _ in range(count)]
    generated_cache[update.effective_chat.id] = results

    bin_info = await fetch_bin_info(bin_code)
    brand = bin_info["scheme"]
    bank = bin_info["bank"]
    country = f"{bin_info['country']} {bin_info['emoji']}"
    card_type = bin_info["type"]
    level = bin_info["brand"]

    card_list = "\n".join(results)

    keyboard = [[
        InlineKeyboardButton("⬇️ Export .txt", callback_data='export_txt'),
        InlineKeyboardButton("⬇️ Export .csv", callback_data='export_csv')
    ]]

    await update.message.reply_text(
        f"💳 *Issuer:* {brand}\n"
        f"🏦 *Bank:* {bank}\n"
        f"🌍 *Country:* {country}\n"
        f"📦 *Type:* {card_type}\n"
        f"💎 *Level:* {level}\n"
        f"🔢 *Generated {len(results)} Cards:*\n"
        f"```\n{card_list}\n```\n"
        f"Generated by @Arcaxbydz",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_bin(update: Update, context: ContextTypes.DEFAULT_TYPE, command_mode=False):
    if command_mode:
        text = " ".join(context.args)
    else:
        text = update.message.text

    bin_match = re.search(r"(?:\.bin|/bin)?\s*(\d{6,15})", text)
    if not bin_match:
        await update.message.reply_text("⚠️ Usage: `/bin <6-15 digit BIN>`", parse_mode="Markdown")
        return

    bin_code = bin_match.group(1)
    bin_info = await fetch_bin_info(bin_code)

    await update.message.reply_text(
        f"🔍 *BIN Lookup:* `{bin_code}`\n"
        f"💳 *Scheme:* {bin_info['scheme']}\n"
        f"🏦 *Bank:* {bin_info['bank']}\n"
        f"🌍 *Country:* {bin_info['country']} {bin_info['emoji']}\n"
        f"💼 *Type:* {bin_info['type']}\n"
        f"💎 *Level:* {bin_info['brand']}\n"
        f"\nGenerated by @Arcaxbydz",
        parse_mode="Markdown"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return
    text = update.message.text.lower()
    if text.startswith(".gen"):
        await handle_gen(update, context)
    elif text.startswith(".bin"):
        await handle_bin(update, context)

async def export_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = generated_cache.get(query.message.chat.id)
    if not data:
        await query.edit_message_text("❌ No recent generation found.")
        return

    if query.data == 'export_txt':
        file_data, name = generate_txt(data), "cards.txt"
    else:
        file_data, name = generate_csv(data), "cards.csv"

    await context.bot.send_document(chat_id=query.message.chat.id, document=InputFile(file_data, name))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to @Arcaxbydz — the card generator!\n\n"
        "Use `/gen <bin> x<qty> MM|YYYY` to generate cards.\n\n"
        "*Examples:*\n"
        "Value: `/gen 457821`\n"
        "Bulk: `/gen 457821 x10`\n"
        "Custom Date: `/gen 457821 09|28 x10`\n"
        "Full Date: `/gen 457821 09|2028`\n\n"
        "Or simply type:\n"
        "`.gen 457821 09|28`",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📘 *How to use @Arcaxbydz:*\n\n"
        "`/gen <bin>` - Generate 1 card\n"
        "`/gen <bin> x10` - Generate 10 cards\n"
        "`/gen <bin> 08|28` - Generate with date 08/2028\n"
        "`/gen <bin> x5 08|2030` - Mix quantity and date\n\n"
        "**Formats supported:** `MM|YY`, `MM|YYYY`\n"
        "**Amex Support:** Start BIN with 34 or 37 for 15-digit generation.\n\n"
        "`/bin <bin>` or `.bin <bin>` - Lookup BIN details.",
        parse_mode="Markdown"
    )

print("ENV:", os.environ)

token = os.getenv("BOT_TOKEN")
if not token:
    print("❌ BOT_TOKEN is missing in environment variables.")
    return

    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("gen", lambda u, c: handle_gen(u, c, command_mode=True)))
    app.add_handler(CommandHandler("bin", lambda u, c: handle_bin(u, c, command_mode=True)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(export_callback))

    print("✅ Bot is running...")
app.run_polling()

if __name__ == "__main__":
    main()