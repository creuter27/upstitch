"""
Upstitch Telegram Bot — warehouse assistant.

Supported voice/text commands:
  - "reduce stock by 2 for Trixie backpack large Fox"
  - "set stock to 10 for TRX rucksack l Fox"
  - "how many Trixie large Fox backpacks do we have?"

Flow for stock updates:
  1. Parse intent via Claude.
  2. Find matching products in Billbee.
  3. If ambiguous: show list and ask user to pick.
  4. Confirm the action with the user.
  5. Execute on confirmation.
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import agent
import billbee
import mappings
import transcribe

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
# Suppress noisy polling logs from httpx and telegram internals
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext.Updater").setLevel(logging.WARNING)
log = logging.getLogger("bot")

# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------

_ALLOWED_IDS: set[int] = set()
_raw = os.environ.get("ALLOWED_USER_IDS", "").strip()
if _raw:
    _ALLOWED_IDS = {int(x.strip()) for x in _raw.split(",") if x.strip()}


def _is_allowed(update: Update) -> bool:
    if not _ALLOWED_IDS:
        return True
    return (update.effective_user.id if update.effective_user else 0) in _ALLOWED_IDS


# ---------------------------------------------------------------------------
# Pending-confirmation state
# Keyed by (chat_id, user_id) → pending action dict
# ---------------------------------------------------------------------------

_pending: dict[tuple[int, int], dict] = {}


def _state_key(update: Update) -> tuple[int, int]:
    return (update.effective_chat.id, update.effective_user.id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _confirm_msg(p: dict, delta: float | None, new_qty: float | None) -> str:
    current = p.get("cachedStock")
    target = p.get("stockTarget")
    if delta is not None:
        new_stock = (current or 0) + delta
        change_str = f"{'➕' if delta > 0 else '➖'} {abs(delta):g}"
    else:
        new_stock = new_qty
        change_str = f"= {new_qty:g}"
    lines = [
        f"*Confirm stock update*",
        f"Product: `{p['sku']}`",
        f"{_product_label(p)}",
        f"",
        f"Current stock:  *{current:g}*" if current is not None else "Current stock:  _unknown_",
        f"Target stock:   *{target:g}*" if target is not None else "Target stock:   _unknown_",
        f"Change:         *{change_str}*",
        f"New stock:      *{new_stock:g}*",
    ]
    return "\n".join(lines)


def _product_label(p: dict) -> str:
    parts = [p.get("manufacturer", ""), p.get("category", ""),
             p.get("size", ""), p.get("variant", ""), p.get("color", "")]
    label = " ".join(x for x in parts if x)
    sku = p.get("sku", "")
    stock = p.get("cachedStock")
    stock_str = f"  (stock: {int(stock)})" if stock is not None else ""
    return f"{label}  [{sku}]{stock_str}"


def _clean_filters(filters_dict: dict | None) -> dict:
    if not filters_dict:
        return {}
    return {k: v for k, v in filters_dict.items() if v}


# ---------------------------------------------------------------------------
# Core command processor
# ---------------------------------------------------------------------------

async def process_text(text: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat

    await chat.send_action("typing")

    try:
        parsed = agent.parse_command(text)
    except Exception as e:
        await update.effective_message.reply_text(f"⚠️ Failed to parse command:\n`{e}`", parse_mode="Markdown")
        return
    action = parsed.get("action")

    if action == "unknown":
        await update.effective_message.reply_text(
            f"Sorry, I didn't understand that.\n_{parsed.get('message', '')}_",
            parse_mode="Markdown",
        )
        return

    manufacturer = parsed.get("manufacturer")
    if not manufacturer:
        await update.effective_message.reply_text(
            "Which manufacturer? (e.g. Trixie / TRX, Fresk / FRE)"
        )
        return

    clean = mappings.resolve_filters(_clean_filters(parsed.get("filters")))

    # -----------------------------------------------------------------------
    # GET STOCK
    # -----------------------------------------------------------------------
    if action == "get_stock":
        status = await update.effective_message.reply_text("🔍 Looking up products…")
        try:
            products = await asyncio.get_event_loop().run_in_executor(
                None, lambda: billbee.find_products(manufacturer, **clean)
            )
        except Exception as e:
            await status.edit_text(f"Error: {e}")
            return

        if not products:
            await status.edit_text("No matching products found.")
            return

        await status.edit_text(f"📡 Fetching live stock from Billbee for {len(products)} product(s)…")
        try:
            live = await asyncio.get_event_loop().run_in_executor(
                None, lambda: billbee.get_live_stock(products)
            )
        except Exception as e:
            await status.edit_text(f"Error fetching stock: {e}")
            return
        await status.delete()

        lines = [f"*Stock levels for {manufacturer}:*"]
        for p in products[:20]:
            sku = p.get("sku", "")
            stock = live.get(sku)
            target = p.get("stockTarget")
            label = " ".join(x for x in [p.get("category",""), p.get("size",""),
                                          p.get("variant",""), p.get("color","")] if x)
            stock_str = f"{stock:g}" if stock is not None else "?"
            target_str = f" / target: {target:g}" if target is not None else ""
            lines.append(f"`{sku}`  {label}  — *{stock_str}*{target_str}")
        if len(products) > 20:
            lines.append(f"_(…and {len(products) - 20} more)_")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # -----------------------------------------------------------------------
    # UPDATE STOCK
    # -----------------------------------------------------------------------
    if action == "update_stock":
        delta = parsed.get("delta")
        new_qty = parsed.get("new_quantity")

        if delta is None and new_qty is None:
            await update.effective_message.reply_text(
                "I understood a stock update, but couldn't determine the new quantity."
            )
            return

        status = await update.effective_message.reply_text("🔍 Looking up products…")
        try:
            products = await asyncio.get_event_loop().run_in_executor(
                None, lambda: billbee.find_products(manufacturer, **clean)
            )
        except Exception as e:
            await status.edit_text(f"Error: {e}")
            return

        if products:
            await status.edit_text(f"📡 Fetching live stock from Billbee…")
            try:
                live = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: billbee.get_live_stock(products)
                )
                for p in products:
                    if p["sku"] in live:
                        p["cachedStock"] = live[p["sku"]]
            except Exception as e:
                await status.edit_text(f"Error fetching stock: {e}")
                return

        await status.delete()

        if not products:
            filter_summary = ", ".join(f"{k}={v}" for k, v in clean.items()) or "none"
            await update.effective_message.reply_text(
                f"No matching products found.\n"
                f"_Searched: manufacturer={manufacturer}, filters: {filter_summary}_\n\n"
                f"Try `/list {manufacturer}` to see all products for this manufacturer.",
                parse_mode="Markdown",
            )
            return

        if len(products) == 1:
            # Single match — ask for confirmation
            p = products[0]
            key = _state_key(update)
            _pending[key] = {
                "action": "update_stock",
                "product": p,
                "delta": delta,
                "new_quantity": new_qty,
            }
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
            ]])
            await update.effective_message.reply_text(
                _confirm_msg(p, delta, new_qty), parse_mode="Markdown", reply_markup=keyboard
            )
            return

        # Multiple matches — show a numbered list and ask user to pick
        if len(products) > 10:
            await update.effective_message.reply_text(
                f"Found {len(products)} products — please be more specific "
                f"(add category, size, variant or color)."
            )
            return

        key = _state_key(update)
        _pending[key] = {
            "action": "select_product",
            "products": products,
            "delta": delta,
            "new_quantity": new_qty,
        }

        lines = ["*Multiple matches — which product?*"]
        buttons = []
        for i, p in enumerate(products):
            lines.append(f"{i + 1}. {_product_label(p)}")
            buttons.append([InlineKeyboardButton(str(i + 1), callback_data=f"pick_{i}")])
        buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="cancel")])

        await update.effective_message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    await update.effective_message.reply_text(f"Unsupported action: {action}")


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return
    text = update.message.text or ""
    if text.startswith("/"):
        return  # handled by CommandHandler
    await process_text(text, update, context)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_allowed(update):
        return

    voice = update.message.voice or update.message.audio
    if not voice:
        return

    # On first use the Whisper model downloads (~75 MB) — warn the user
    status_text = "⬇️ Downloading voice model (first use, ~75 MB)…" if not transcribe.is_model_loaded() else "🎙 Transcribing…"
    status = await update.message.reply_text(status_text)

    file = await context.bot.get_file(voice.file_id)
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        await file.download_to_drive(tmp_path)
        if not transcribe.is_model_loaded():
            await status.edit_text("⬇️ Downloading voice model (first use, ~75 MB)… this takes a minute.")
        text = await asyncio.get_event_loop().run_in_executor(
            None, lambda: transcribe.transcribe(tmp_path)
        )
    except Exception as e:
        await status.edit_text(f"Transcription failed: {e}")
        return
    finally:
        tmp_path.unlink(missing_ok=True)

    await status.delete()

    await update.message.reply_text(f'_Heard: "{text}"_', parse_mode="Markdown")
    await process_text(text, update, context)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    key = _state_key(update)
    pending = _pending.get(key)

    if query.data == "cancel":
        _pending.pop(key, None)
        await query.edit_message_text("Cancelled.")
        return

    if not pending:
        await query.edit_message_text("No pending action. Please start over.")
        return

    # Product selection step
    if query.data.startswith("pick_") and pending.get("action") == "select_product":
        idx = int(query.data.split("_")[1])
        products = pending["products"]
        if idx >= len(products):
            await query.edit_message_text("Invalid selection.")
            _pending.pop(key, None)
            return

        p = products[idx]
        delta = pending["delta"]
        new_qty = pending["new_quantity"]

        _pending[key] = {
            "action": "update_stock",
            "product": p,
            "delta": delta,
            "new_quantity": new_qty,
        }

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Confirm", callback_data="confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]])
        await query.edit_message_text(
            _confirm_msg(p, delta, new_qty), parse_mode="Markdown", reply_markup=keyboard
        )
        return

    # Confirmation step
    if query.data == "confirm" and pending.get("action") == "update_stock":
        p = pending["product"]
        delta = pending["delta"]
        new_qty = pending["new_quantity"]
        _pending.pop(key, None)

        await query.edit_message_text(f"⏳ Updating stock for `{p['sku']}`…", parse_mode="Markdown")

        try:
            result = billbee.update_stock(
                sku=p["sku"],
                billbee_id=p["billbeeId"],
                delta=delta,
                new_quantity=new_qty,
                reason="Telegram bot: manual correction",
            )
        except Exception as e:
            await query.edit_message_text(f"Error: {e}")
            return

        prev = result["previousStock"]
        new = result["newStock"]
        actual_delta = new - prev
        target = p.get("stockTarget")
        target_line = f"\nTarget stock:  *{target:g}*" if target is not None else ""
        await query.edit_message_text(
            f"✅ Done! `{p['sku']}`\n"
            f"Previous:      *{prev:g}*\n"
            f"Change:        *{'➕' if actual_delta >= 0 else '➖'}{abs(actual_delta):g}*\n"
            f"New stock:     *{new:g}*"
            f"{target_line}",
            parse_mode="Markdown",
        )
        return

    await query.edit_message_text("Unexpected state. Please start over.")
    _pending.pop(key, None)


async def cmd_refresh(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /refresh TRX   — re-fetch products from Billbee and update cache."""
    if not _is_allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/refresh TRX` or `/refresh FRE`", parse_mode="Markdown")
        return
    manufacturer = args[0].upper()
    status = await update.message.reply_text(f"⏳ Fetching {manufacturer} products from Billbee…")

    loop = asyncio.get_event_loop()
    fetch_task = loop.run_in_executor(None, lambda: billbee.refresh_cache(manufacturer))

    # Update message every 5 s so the user knows it's still running
    spinner = ["⏳", "🔄"]
    elapsed = 0
    while not fetch_task.done():
        await asyncio.sleep(5)
        elapsed += 5
        icon = spinner[(elapsed // 5) % len(spinner)]
        try:
            await status.edit_text(f"{icon} Fetching {manufacturer} products from Billbee… ({elapsed}s)")
        except Exception:
            pass  # ignore if message unchanged

    try:
        products = await fetch_task
    except Exception as e:
        await status.edit_text(f"❌ Error: {e}")
        return
    await status.edit_text(f"✅ Cache updated — {len(products)} {manufacturer} products loaded in {elapsed}s.")


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /list TRX   — shows all products for a manufacturer."""
    if not _is_allowed(update):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: `/list TRX` or `/list FRE`", parse_mode="Markdown")
        return
    manufacturer = args[0].upper()
    from_cache = billbee._load_cache(manufacturer) is not None
    status = await update.message.reply_text(
        f"🔍 Loading {manufacturer} products{'from cache' if from_cache else ' from Billbee'}…"
    )
    try:
        products = await asyncio.get_event_loop().run_in_executor(
            None, lambda: billbee.find_products(manufacturer)
        )
    except Exception as e:
        await status.edit_text(f"Error: {e}")
        return
    await status.delete()
    if not products:
        await update.message.reply_text(f"No products found for {manufacturer}.")
        return
    lines = [f"*{manufacturer} — {len(products)} products:*"]
    for p in products[:30]:
        lines.append(_product_label(p))
    if len(products) > 30:
        lines.append(f"_(…and {len(products) - 30} more)_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error("Unhandled exception", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        await update.effective_message.reply_text(
            f"⚠️ Unexpected error:\n`{context.error}`", parse_mode="Markdown"
        )


_HELP_TEXT = (
    "👋 *Upstitch Warehouse Bot*\n\n"
    "*Voice or text commands:*\n"
    "• _reduce stock by 2 for Trixie backpack large Fox_\n"
    "• _set Trixie rucksack l Fox to 5_\n"
    "• _how many Trixie large Fox backpacks do we have?_\n\n"
    "*Commands:*\n"
    "/list `TRX` — show all products for a manufacturer\n"
    "/refresh `TRX` — re-fetch products from Billbee and update cache\n"
    "/help — show this message"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_HELP_TEXT, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("refresh", cmd_refresh))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_error_handler(handle_error)

    log.info("Bot starting…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
