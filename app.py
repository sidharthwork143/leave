import logging
import os # Environment variables ke liye
from flask import Flask, request # Webhook ke liye Flask
from telegram import Update, ChatMember, ChatMemberUpdated, Bot
from telegram.ext import Updater, CommandHandler, ChatMemberHandler, CallbackContext, Dispatcher

# --- CONFIGURATION ---
# Token aur anya settings environment variables se lenge
BOT_TOKEN = os.environ.get("BOT_TOKEN")
# Render aapko ek URL dega, woh yahan environment variable ke through set hoga
WEBHOOK_URL = os.environ.get("WEBHOOK_URL") # Example: "https://your-app-name.onrender.com"
TARGET_GROUP_ID_STR = os.environ.get("TARGET_GROUP_ID")

TARGET_GROUP_ID = None
if TARGET_GROUP_ID_STR:
    try:
        TARGET_GROUP_ID = int(TARGET_GROUP_ID_STR)
    except ValueError:
        logging.error(f"TARGET_GROUP_ID '{TARGET_GROUP_ID_STR}' sahi integer nahi hai.")
        # Aap yahan bot ko exit karwa sakte hain ya default None rehne de sakte hain

# Port environment variable se, Render ise set karta hai
PORT = int(os.environ.get("PORT", 8443)) # Default port agar RENDER_PORT set nahi hai

# Logging setup
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Flask app initialize karein
app = Flask(__name__)

# --- BOT FUNCTIONS (Pehle jaise hi) ---

def extract_status_change(chat_member_update: ChatMemberUpdated):
    """
    ChatMemberUpdated object se status change extract karta hai.
    Returns:
        Tuple (was_member, is_member) ya None agar koi relevant change nahi hua.
    """
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

def handle_member_left(update: Update, context: CallbackContext) -> None:
    """
    Jab koi member group chhodta hai toh yeh function call hota hai.
    """
    logger.info("ChatMemberUpdate received: %s", update.chat_member)
    
    if TARGET_GROUP_ID and update.chat_member.chat.id != TARGET_GROUP_ID:
        logger.info(f"Update group {update.chat_member.chat.id} ke liye hai, target group {TARGET_GROUP_ID} nahi. Ignore kar rahe hain.")
        return

    result = extract_status_change(update.chat_member)
    if result is None:
        logger.info("Koi relevant status change (member leaving) nahi hua.")
        return

    was_member, is_member = result
    user_who_left = update.chat_member.new_chat_member.user
    group_name = update.chat_member.chat.title

    if was_member and not is_member:
        logger.info("%s (%s) ne group '%s' chhod diya. Status: %s", 
                    user_who_left.full_name, user_who_left.id, group_name, update.chat_member.new_chat_member.status)
        
        message_to_user = (
            f"नमस्ते {user_who_left.first_name},\n\n"
            f"Humne dekha ki aapne group '{group_name}' chhod diya hai. "
            "Kya aap bata sakte hain ki aapne group kyun chhoda? \n\n"
            "Aapke feedback se humein group ko behtar banane mein madad milegi. धन्यवाद!"
        )
        try:
            context.bot.send_message(chat_id=user_who_left.id, text=message_to_user)
            logger.info("User %s ko message bheja gaya.", user_who_left.full_name)
        except Exception as e:
            logger.error(
                "User %s ko message bhejte waqt error: %s. Shayad bot block hai ya user DM nahi le sakta.", 
                user_who_left.full_name, e
            )
    else:
        logger.info("User %s ke status mein change hua, par woh group chhod kar nahi gaya/gayi. Old status: %s, New status: %s. Was member: %s, Is member: %s",
                    user_who_left.full_name,
                    update.chat_member.old_chat_member.status,
                    update.chat_member.new_chat_member.status,
                    was_member, is_member)

def start_command(update: Update, context: CallbackContext) -> None: # Function ka naam badla to avoid conflict with Flask's app.start
    """/start command ke liye handler."""
    user = update.effective_user
    update.message.reply_html(
        rf"Namaste {user.mention_html()}! Main group members ke leave karne par unhe message karne ke liye yahan hoon."
    )

def error_handler(update: Update, context: CallbackContext) -> None:
    """Telegram API se errors ko log karega."""
    logger.error(msg="Exception while handling an update:", exc_info=context.error)

# --- BOT SETUP & WEBHOOK ---
# Bot aur Dispatcher ko global scope mein ya function ke through access karna hoga
if BOT_TOKEN is None:
    logger.error("BOT_TOKEN environment variable set nahi hai! Bot start nahi ho sakta.")
    # Exit kar sakte hain ya error raise kar sakte hain
    # raise ValueError("BOT_TOKEN is not set")
else:
    bot = Bot(token=BOT_TOKEN)
    dispatcher = Dispatcher(bot, None, workers=0) # workers=0 webhook ke liye aam hai

    # Handlers add karein
    dispatcher.add_handler(CommandHandler("start", start_command))
    dispatcher.add_handler(ChatMemberHandler(handle_member_left, ChatMemberHandler.CHAT_MEMBER))
    dispatcher.add_error_handler(error_handler)

    # Webhook route (Telegram is route par updates POST karega)
    # URL mein token include karna ek security measure hai, taaki koi aur aapke webhook ko call na kar sake
    @app.route(f"/{BOT_TOKEN}", methods=["POST"])
    def webhook():
        try:
            update = Update.de_json(request.get_json(force=True), bot)
            dispatcher.process_update(update)
        except Exception as e:
            logger.error(f"Webhook handle karte waqt error: {e}")
        return "ok", 200 # Telegram ko success response bhejein

    @app.route("/")
    def index():
        return "Bot chal raha hai!" # Health check ke liye simple route

def main_setup_webhook():
    """Webhook set karta hai (Bot start hone par ek baar call karna hota hai)."""
    if BOT_TOKEN and WEBHOOK_URL:
        # Pehle se set webhook ko delete karein (optional, par development mein helpful)
        # bot.delete_webhook() 
        # Naya webhook set karein
        success = bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
        if success:
            logger.info(f"Webhook successfully set kiya gaya: {WEBHOOK_URL}/{BOT_TOKEN}")
        else:
            logger.error("Webhook set karne mein error!")
    else:
        logger.error("BOT_TOKEN ya WEBHOOK_URL set nahi hai. Webhook set nahi kiya ja sakta.")

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("Kripya BOT_TOKEN environment variable set karein.")
    elif not WEBHOOK_URL:
        print("Kripya WEBHOOK_URL environment variable set karein (aapke Render app ka URL).")
    else:
        # Bot start hone par ek baar webhook set karein
        main_setup_webhook()
        # Flask development server start karein
        # Render par, Gunicorn jaise production server ka istemal hoga
        app.run(host="0.0.0.0", port=PORT)
