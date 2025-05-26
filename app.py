import logging
import os
import asyncio # For running async setup
from flask import Flask, request
from telegram import Update, ChatMember, ChatMemberUpdated
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ChatMemberHandler,
    CallbackContext, # Kept for now, can be ContextTypes.DEFAULT_TYPE
    ContextTypes, # For more precise typing if needed
)

# --- CONFIGURATION ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")
TARGET_GROUP_ID_STR = os.environ.get("TARGET_GROUP_ID")

TARGET_GROUP_ID = None
if TARGET_GROUP_ID_STR:
    try:
        TARGET_GROUP_ID = int(TARGET_GROUP_ID_STR)
    except ValueError:
        logging.error(f"TARGET_GROUP_ID '{TARGET_GROUP_ID_STR}' is not a valid integer.")

PORT = int(os.environ.get("PORT", 8443))

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app initialize
app = Flask(__name__)

# Global application object for the bot
# Will be initialized in the main block if BOT_TOKEN is present
ptb_application: Application | None = None

# --- BOT FUNCTIONS (now async) ---

def extract_status_change(chat_member_update: ChatMemberUpdated):
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member", (None, None))

    if status_change is None:
        if old_is_member is True and new_is_member is False:
            return True, False
        return None

    old_status, new_status = status_change
    was_member = old_status in [
        ChatMember.MEMBER,
        ChatMember.ADMINISTRATOR,
        ChatMember.OWNER,
    ] or old_is_member
    is_member = new_status in [
        ChatMember.MEMBER,
        ChatMember.ADMINISTRATOR,
        ChatMember.OWNER,
    ] or new_is_member
    return was_member, is_member

async def handle_member_left(update: Update, context: CallbackContext) -> None:
    logger.info("ChatMemberUpdate received: %s", update.chat_member)
    
    if not update.chat_member:
        logger.warning("ChatMemberUpdate received without chat_member object.")
        return

    if TARGET_GROUP_ID and update.chat_member.chat.id != TARGET_GROUP_ID:
        logger.info(f"Update for group {update.chat_member.chat.id}, not target group {TARGET_GROUP_ID}. Ignoring.")
        return

    result = extract_status_change(update.chat_member)
    if result is None:
        logger.info("No relevant status change (member leaving) detected.")
        return

    was_member, is_member = result
    user_who_left = update.chat_member.new_chat_member.user
    group_name = update.chat_member.chat.title

    if was_member and not is_member:
        logger.info("%s (%s) left group '%s'. Status: %s", 
                    user_who_left.full_name, user_who_left.id, group_name, update.chat_member.new_chat_member.status)
        
        message_to_user = (
            f"नमस्ते {user_who_left.first_name},\n\n"
            f"Humne dekha ki aapne group '{group_name}' chhod diya hai. "
            "Kya aap bata sakte hain ki aapne group kyun chhoda? \n\n"
            "Aapke feedback se humein group ko behtar banane mein madad milegi. धन्यवाद!"
        )
        try:
            await context.bot.send_message(chat_id=user_who_left.id, text=message_to_user)
            logger.info("Message sent to user %s.", user_who_left.full_name)
        except Exception as e:
            logger.error(
                "Error sending message to user %s: %s. Bot might be blocked or user DMs are off.", 
                user_who_left.full_name, e
            )
    else:
        logger.info("Status change for user %s, but not a leave event. Old: %s, New: %s. Was member: %s, Is member: %s",
                    user_who_left.full_name,
                    update.chat_member.old_chat_member.status,
                    update.chat_member.new_chat_member.status,
                    was_member, is_member)

async def start_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if user and update.message: # Ensure user and message are not None
        await update.message.reply_html(
            rf"Namaste {user.mention_html()}! Main group members ke leave karne par unhe message karne ke liye yahan hoon."
        )
    else:
        logger.warning("Start command received with no effective_user or message.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log Errors caused by Updates."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- WEBHOOK SETUP ---
async def actual_webhook_setup(application_instance: Application):
    """Sets up the webhook for Telegram."""
    if not BOT_TOKEN or not WEBHOOK_URL:
        logger.error("BOT_TOKEN or WEBHOOK_URL not set. Cannot set webhook.")
        return
    
    await application_instance.initialize() # Important for Application setup
    webhook_full_url = f"{WEBHOOK_URL}/{BOT_TOKEN}"
    try:
        # You might want to delete any existing webhook first for clean setup
        # await application_instance.bot.delete_webhook()
        await application_instance.bot.set_webhook(url=webhook_full_url)
        logger.info(f"Webhook successfully set to: {webhook_full_url}")
    except Exception as e:
        logger.error(f"Error setting webhook to {webhook_full_url}: {e}")

def setup_webhook_sync_for_flask():
    """Synchronous wrapper to call async webhook setup. Called before Flask app runs."""
    global ptb_application
    if ptb_application:
        try:
            asyncio.run(actual_webhook_setup(ptb_application))
        except RuntimeError as e:
            # This can happen if an event loop is already running,
            # e.g., in some WSGI server setups or Jupyter.
            # For Render/Gunicorn, this should generally be fine if called once at startup.
            logger.warning(f"Could not run async webhook setup (possibly due to existing event loop): {e}")
            # As a fallback, try to get or create a new loop if needed,
            # but this can be tricky. The initial asyncio.run should work in most server startup scripts.
            # loop = asyncio.get_event_loop()
            # if loop.is_running():
            #     logger.info("Event loop already running, trying to schedule webhook setup.")
            #     loop.create_task(actual_webhook_setup(ptb_application))
            # else:
            #     loop.run_until_complete(actual_webhook_setup(ptb_application))

    else:
        logger.error("Bot application not initialized. Cannot set webhook.")


# --- FLASK ROUTES ---
# Define routes after ptb_application might be initialized or ensure it's checked inside
if BOT_TOKEN: # Only define webhook route if BOT_TOKEN is present
    @app.route(f"/{BOT_TOKEN}", methods=["POST"])
    async def webhook_handler_route(): # Renamed to avoid conflict if any
        global ptb_application
        if not ptb_application:
            logger.error("Bot application not ready to handle webhook.")
            return "error", 500
        try:
            update_json = request.get_json(force=True)
            update = Update.de_json(update_json, ptb_application.bot)
            await ptb_application.process_update(update)
        except Exception as e:
            logger.error(f"Error handling webhook: {e}")
        return "ok", 200
else:
    logger.error("BOT_TOKEN not found, webhook route not created.")

@app.route("/")
def index():
    return "Bot is running!"

# --- MAIN ---
if __name__ == "__main__":
    if not BOT_TOKEN:
        print("FATAL: BOT_TOKEN environment variable not set.")
    elif not WEBHOOK_URL:
        print("FATAL: WEBHOOK_URL environment variable not set (your Render app's URL).")
    else:
        # Initialize the bot application
        ptb_application = ApplicationBuilder().token(BOT_TOKEN).build()

        # Add handlers
        ptb_application.add_handler(CommandHandler("start", start_command))
        ptb_application.add_handler(ChatMemberHandler(handle_member_left, ChatMemberHandler.CHAT_MEMBER))
        ptb_application.add_error_handler(error_handler)
        
        # Setup webhook (synchronously calling the async setup)
        # This should be done *before* app.run()
        setup_webhook_sync_for_flask()
        
        # Run Flask app
        # For Render, Gunicorn will typically run this using a command like:
        # gunicorn -k uvicorn.workers.UvicornWorker app:app
        # The -k uvicorn.workers.UvicornWorker is important for async Flask routes.
        app.run(host="0.0.0.0", port=PORT)
