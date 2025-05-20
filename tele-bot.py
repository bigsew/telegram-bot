import os
import json
import logging
from datetime import datetime, timedelta
import asyncio
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, \
    ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler, \
    ConversationHandler
from apscheduler.triggers.date import DateTrigger
import requests
from io import BytesIO
from PIL import Image
import numpy as np

# Try to import NudeNet for image scanning
try:
    from nudenet import NudeClassifier

    classifier = NudeClassifier()
    NUDENET_AVAILABLE = True
except ImportError:
    NUDENET_AVAILABLE = False
    print("NudeNet not available. Install with: pip install nudenet")

# Bot configuration
BOT_TOKEN = "7947027516:AAGFCc6tNRnf3ZVn9Yd6quIfBj1twTsPSi4"
CHANNEL_ID = "@hayre37"
ADMIN_USERNAME = "Hayre32"  # Bot administrator username
AUTO_POST_ENABLED = True
AUTO_POST_INTERVAL = 6  # Hours between automatic posts
AUTO_POST_LIMIT = 1  # Number of products to post in each interval
PRODUCTS_FILE = 'products.json'

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for conversation
MAIN_MENU, PRODUCT_NAME, PRODUCT_DESCRIPTION, PRODUCT_PRICE, PRODUCT_IMAGE, SCHEDULE_POST, CONTACT_MESSAGE = range(7)

# Registration states
REGISTER_NAME, REGISTER_PHONE, REGISTER_ADDRESS, REGISTER_CONFIRM = range(7, 11)

# Product creation states
SELECT_CATEGORY, SELECT_SUBCATEGORY, CUSTOM_TAG = range(11, 14)

# Track posted products to avoid duplicates
posted_products = set()

# User preferences storage
PREFERENCES_FILE = 'preferences.json'
USERS_FILE = 'users.json'

# Product categories and subcategories
PRODUCT_CATEGORIES = {
    "#Electronics": ["#Phones", "#Computers", "#TVs", "#Accessories"],
    "#Clothing": ["#Men", "#Women", "#Children", "#Shoes"],
    "#Home": ["#Furniture", "#Kitchen", "#Decor", "#Appliances"],
    "#Beauty": ["#Makeup", "#Skincare", "#Haircare", "#Fragrance"],
    "#Sports": ["#Equipment", "#Clothing", "#Shoes", "#Accessories"],
    "#Vehicles": ["#Cars", "#Motorcycles", "#Parts", "#Rentals"],
    "#Services": ["#Cleaning", "#Repair", "#Education", "#Health"],
    "#Jobs": ["#FullTime", "#PartTime", "#Remote", "#Internship"],
    "#RealEstate": ["#Apartments", "#Houses", "#Land", "#Commercial"],
    "#Other": ["#Miscellaneous"]
}


def load_products():
    if os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE, 'r') as f:
            return json.load(f)
    return []


def save_products(products):
    with open(PRODUCTS_FILE, 'w') as f:
        json.dump(products, f, indent=4)


def load_preferences():
    if os.path.exists(PREFERENCES_FILE):
        with open(PREFERENCES_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_preferences(preferences):
    with open(PREFERENCES_FILE, 'w') as f:
        json.dump(preferences, f, indent=4)


def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}


def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=4)


def get_user_data(user_id):
    users = load_users()
    user_id_str = str(user_id)
    if user_id_str in users:
        return users[user_id_str]
    return None


def save_user_data(user_id, data):
    users = load_users()
    user_id_str = str(user_id)
    users[user_id_str] = data
    save_users(users)


def is_user_registered(user_id):
    user_data = get_user_data(user_id)
    return user_data is not None and user_data.get('registration_complete', False)


def is_admin(username):
    """Check if a user is an admin"""
    return username and username.lower() == ADMIN_USERNAME.lower()


def get_user_preferences(user_id):
    preferences = load_preferences()
    user_id_str = str(user_id)
    if user_id_str not in preferences:
        preferences[user_id_str] = {
            "auto_post": True,
            "notifications": True,
            "language": "en",
            "theme": "light"
        }
        save_preferences(preferences)
    return preferences[user_id_str]


def update_user_preference(user_id, key, value):
    preferences = load_preferences()
    user_id_str = str(user_id)
    if user_id_str not in preferences:
        preferences[user_id_str] = {
            "auto_post": True,
            "notifications": True,
            "language": "en",
            "theme": "light"
        }
    preferences[user_id_str][key] = value
    save_preferences(preferences)


def get_main_menu_keyboard():
    """Create the main menu keyboard."""
    keyboard = [
        [KeyboardButton("📦 My Products"), KeyboardButton("👤 My Account")],
        [KeyboardButton("⭐ Preferences"), KeyboardButton("📅 Schedule Post")],
        [KeyboardButton("📥 Contact Us"), KeyboardButton("🔍 Explore Products")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def get_cancel_keyboard():
    """Create a keyboard with cancel button."""
    keyboard = [[KeyboardButton("❌ Cancel")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Send a welcome message and check if user is registered."""
    user = update.effective_user
    user_id = user.id

    # Log the start command for debugging
    logger.info(f"Start command received from user {user_id} with args: {context.args}")

    # Check if this is a deep link for contacting a seller
    if context.args and context.args[0].startswith("contact_"):
        # Extract product ID
        product_id = context.args[0].split("_")[1]
        logger.info(f"Deep link detected for product {product_id}")

        # Check if user is registered
        if not is_user_registered(user_id):
            # Store the product ID for after registration
            context.user_data['pending_contact_product_id'] = product_id
            logger.info(f"User {user_id} not registered, storing pending contact for product {product_id}")

            # Start registration process
            await update.message.reply_html(
                f"Welcome {user.mention_html()}! Before you can contact the seller, please complete a quick registration.\n\n"
                f"What is your full name?"
            )
            return REGISTER_NAME

        # User is registered, show seller contact information immediately
        logger.info(f"User {user_id} is registered, showing seller contact for product {product_id}")
        await show_seller_contact_from_deeplink(update, context, product_id)
        return MAIN_MENU

    # Regular start command (not a deep link)
    # Check if user is registered
    if not is_user_registered(user_id):
        logger.info(f"New user {user_id} starting registration")
        # Start registration process
        await update.message.reply_html(
            f"Welcome {user.mention_html()}! Before you can use the bot, please complete a quick registration.\n\n"
            f"What is your full name?"
        )
        return REGISTER_NAME

    # User is already registered, show main menu
    logger.info(f"Registered user {user_id} accessing main menu")
    await update.message.reply_html(
        f"Hi {user.mention_html()}! Welcome to the Product Management Bot.\n\n"
        f"Use the menu below to navigate:",
        reply_markup=get_main_menu_keyboard()
    )

    return MAIN_MENU


async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle registration name input."""
    user = update.effective_user
    name = update.message.text

    # Store name in context
    context.user_data['register_name'] = name

    # Ask for phone number with share contact button
    phone_keyboard = ReplyKeyboardMarkup([
        [KeyboardButton("📱 Share My Phone Number", request_contact=True)]
    ], resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "Thank you! Now, please share your phone number by clicking the button below or enter it manually in the format: +251xxxxxxxx",
        reply_markup=phone_keyboard
    )
    return REGISTER_PHONE


async def register_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle registration phone input."""
    # Check if the user shared contact
    if update.message.contact:
        phone = update.message.contact.phone_number
        # Format the phone number if needed
        if not phone.startswith('+'):
            phone = '+' + phone
    else:
        phone = update.message.text

    # Ethiopian phone validation (+251 format)
    if not re.match(r'^\+251\d{9}$', phone):
        await update.message.reply_text(
            "Please enter a valid Ethiopian phone number in the format +251xxxxxxxxx\n"
            "For example: +251912345678",
            reply_markup=ReplyKeyboardMarkup([
                [KeyboardButton("📱 Share My Phone Number", request_contact=True)]
            ], resize_keyboard=True, one_time_keyboard=True)
        )
        return REGISTER_PHONE

    # Store phone in context
    context.user_data['register_phone'] = phone

    # Ask for address
    await update.message.reply_text(
        "Great! Now, please enter your address:",
        reply_markup=ReplyKeyboardRemove()
    )
    return REGISTER_ADDRESS


async def register_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle registration address input."""
    address = update.message.text

    # Store address in context
    context.user_data['register_address'] = address

    # Show confirmation
    name = context.user_data.get('register_name', '')
    phone = context.user_data.get('register_phone', '')

    await update.message.reply_text(
        f"Please confirm your information:\n\n"
        f"Name: {name}\n"
        f"Phone: {phone}\n"
        f"Address: {address}\n\n"
        f"Is this correct?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Save", callback_data="confirm_registration")],
            [InlineKeyboardButton("❌ No, Start Over", callback_data="restart_registration")]
        ])
    )
    return REGISTER_CONFIRM


async def register_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle registration confirmation."""
    query = update.callback_query
    await query.answer()

    if query.data == "confirm_registration":
        user = query.from_user

        # Save user data
        user_data = {
            "name": context.user_data.get('register_name', ''),
            "phone": context.user_data.get('register_phone', ''),
            "address": context.user_data.get('register_address', ''),
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "registration_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "registration_complete": True
        }

        save_user_data(user.id, user_data)
        logger.info(f"User {user.id} completed registration")

        # Welcome the user
        await query.message.reply_text(
            f"Registration complete! Welcome to the Product Management Bot.",
            reply_markup=get_main_menu_keyboard()
        )

        # Check if there's a pending contact request
        if 'pending_contact_product_id' in context.user_data:
            product_id = context.user_data['pending_contact_product_id']
            logger.info(f"Processing pending contact request for product {product_id}")
            await show_seller_contact_from_deeplink(query.message, context, product_id)
            del context.user_data['pending_contact_product_id']

        return MAIN_MENU

    elif query.data == "restart_registration":
        await query.message.reply_text(
            "Let's start over. What is your full name?"
        )
        return REGISTER_NAME


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send help information."""
    help_text = (
            "📱 <b>Product Management Bot Help</b>\n\n"
            "<b>Main Menu Options:</b>\n"
            "📦 <b>My Products</b> - View and manage your products\n"
            "👤 <b>My Account</b> - View your account information\n"
            "⭐ <b>Preferences</b> - Set your preferences\n"
            "📅 <b>Schedule Post</b> - Schedule a product post\n"
            "📥 <b>Contact Us</b> - Send a message to the admin @" + ADMIN_USERNAME + "\n"
                                                                                     "🔍 <b>Explore Products</b> - Browse all products\n\n"
                                                                                     "<b>Other Commands:</b>\n"
                                                                                     "/start - Show the main menu\n"
                                                                                     "/help - Show this help message\n\n"
                                                                                     f"Auto-posting is {'enabled' if AUTO_POST_ENABLED else 'disabled'}.\n"
                                                                                     f"Products are automatically posted every {AUTO_POST_INTERVAL} hours."
    )

    await update.message.reply_html(help_text, reply_markup=get_main_menu_keyboard())


async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle main menu button selections."""
    text = update.message.text

    if text == "📦 My Products":
        return await my_products(update, context)
    elif text == "👤 My Account":
        return await my_account(update, context)
    elif text == "⭐ Preferences":
        return await preferences(update, context)
    elif text == "📅 Schedule Post":
        return await schedule_post_menu(update, context)
    elif text == "📥 Contact Us":
        return await contact_us(update, context)
    elif text == "🔍 Explore Products":
        return await explore_products(update, context)
    elif text == "❌ Cancel":
        await update.message.reply_text(
            "Returning to main menu.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU
    else:
        await update.message.reply_text(
            "Please select an option from the menu.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU


async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle cancel button press from any state."""
    await update.message.reply_text(
        "Operation cancelled. Returning to main menu.",
        reply_markup=get_main_menu_keyboard()
    )
    return MAIN_MENU


async def my_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show user's products and options to manage them."""
    user_id = update.effective_user.id
    products = load_products()

    # Filter products by this user
    user_products = [p for p in products if p.get('poster_id') == user_id]

    if not user_products:
        # No products, offer to add one
        keyboard = [
            [InlineKeyboardButton("➕ Add New Product", callback_data="add_product")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "You don't have any products yet. Would you like to add one?",
            reply_markup=reply_markup
        )
    else:
        # Show product count and management options
        keyboard = [
            [InlineKeyboardButton("➕ Add New Product", callback_data="add_product")],
            [InlineKeyboardButton("📋 List My Products", callback_data="list_my_products")],
            [InlineKeyboardButton("📊 Product Statistics", callback_data="product_stats")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"You have {len(user_products)} products. What would you like to do?",
            reply_markup=reply_markup
        )

    return MAIN_MENU


async def my_account(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show user account information."""
    user = update.effective_user
    products = load_products()

    # Get user registration data
    user_data = get_user_data(user.id)
    if not user_data:
        # This shouldn't happen if registration flow works correctly
        await update.message.reply_text(
            "Your account information is not complete. Please restart the bot with /start",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Filter products by this user
    user_products = [p for p in products if p.get('poster_id') == user.id]
    posted_count = sum(1 for p in user_products if p.get('posted', False))
    scheduled_count = sum(1 for p in user_products if p.get('scheduled_time') and not p.get('posted', False))

    # Get user preferences
    prefs = get_user_preferences(user.id)

    # Check if user is admin
    admin_status = "✅ Yes" if is_admin(user.username) else "❌ No"

    account_info = (
        f"👤 <b>Account Information</b>\n\n"
        f"Name: {user_data.get('name', 'Not set')}\n"
        f"Phone: {user_data.get('phone', 'Not set')}\n"
        f"Address: {user_data.get('address', 'Not set')}\n"
        f"Username: @{user.username if user.username else 'Not set'}\n"
        f"User ID: {user.id}\n"
        f"Admin: {admin_status}\n\n"
        f"<b>Your Activity:</b>\n"
        f"Total Products: {len(user_products)}\n"
        f"Posted Products: {posted_count}\n"
        f"Scheduled Products: {scheduled_count}\n\n"
        f"<b>Preferences:</b>\n"
        f"Auto-post: {'✅ Enabled' if prefs.get('auto_post', True) else '❌ Disabled'}\n"
        f"Notifications: {'✅ Enabled' if prefs.get('notifications', True) else '❌ Disabled'}\n"
        f"Language: {prefs.get('language', 'en')}\n"
        f"Theme: {prefs.get('theme', 'light').capitalize()}"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Profile", callback_data="edit_profile")],
        [InlineKeyboardButton("⭐ Change Preferences", callback_data="edit_preferences")],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(account_info, reply_markup=reply_markup)
    return MAIN_MENU


async def preferences(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show and edit user preferences."""
    user_id = update.effective_user.id
    prefs = get_user_preferences(user_id)

    keyboard = [
        [InlineKeyboardButton(
            f"Auto-post: {'✅ ON' if prefs.get('auto_post', True) else '❌ OFF'}",
            callback_data="toggle_auto_post"
        )],
        [InlineKeyboardButton(
            f"Notifications: {'✅ ON' if prefs.get('notifications', True) else '❌ OFF'}",
            callback_data="toggle_notifications"
        )],
        [InlineKeyboardButton(
            f"Theme: {prefs.get('theme', 'light').capitalize()}",
            callback_data="toggle_theme"
        )],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "⭐ <b>Preferences</b>\n\n"
        "Customize your experience by adjusting the settings below:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )
    return MAIN_MENU


async def schedule_post_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show menu for scheduling posts."""
    user_id = update.effective_user.id
    products = load_products()

    # Filter unposted products by this user
    user_products = [p for p in products if p.get('poster_id') == user_id and not p.get('posted', False)]

    if not user_products:
        # No products to schedule
        keyboard = [
            [InlineKeyboardButton("➕ Add New Product", callback_data="add_product")],
            [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "You don't have any products available for scheduling. Would you like to add one?",
            reply_markup=reply_markup
        )
    else:
        # Show products that can be scheduled
        keyboard = []
        for product in user_products:
            scheduled = f" ⏰ {product['scheduled_time']}" if product.get('scheduled_time') else ""
            keyboard.append([InlineKeyboardButton(
                f"{product['name']}{scheduled}",
                callback_data=f"schedule_{product['id']}"
            )])

        keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "📅 <b>Schedule a Post</b>\n\n"
            "Select a product to schedule for posting:",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    return MAIN_MENU


async def contact_us(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle contact us functionality - directly send message to admin."""
    await update.message.reply_text(
        "📥 <b>Contact Us</b>\n\n"
        f"Please enter your message below. It will be sent directly to @{ADMIN_USERNAME}.",
        parse_mode='HTML'
    )
    return CONTACT_MESSAGE


async def handle_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Process the contact message from the user and send directly to admin."""
    user = update.effective_user
    message = update.message.text

    # Format user info for admin
    user_info = f"User: {user.first_name}"
    if user.username:
        user_info += f" (@{user.username})"
    user_info += f", ID: {user.id}"

    # Try to send a notification to the admin
    try:
        await context.bot.send_message(
            chat_id=f"@{ADMIN_USERNAME}",
            text=f"📩 <b>New Contact Message</b>\n\n"
                 f"From: {user_info}\n\n"
                 f"Message:\n{message}",
            parse_mode='HTML'
        )

        # Confirm to user
        await update.message.reply_text(
            f"✅ Your message has been sent to @{ADMIN_USERNAME}. They will get back to you if needed.",
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to forward message to admin: {e}")
        await update.message.reply_text(
            "❌ There was an error sending your message. Please try again later.",
            reply_markup=get_main_menu_keyboard()
        )

    # Log the contact message
    logger.info(f"Contact message from {user.username} (ID: {user.id}): {message}")

    return MAIN_MENU


async def explore_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Show all products for exploration."""
    products = load_products()

    if not products:
        await update.message.reply_text(
            "No products available to explore yet.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Show the first few products with navigation
    await show_product_page(update, context, products, 0)
    return MAIN_MENU


async def show_product_page(update: Update, context: ContextTypes.DEFAULT_TYPE, products, page=0, items_per_page=3):
    """Show a paginated view of products."""
    start_idx = page * items_per_page
    end_idx = min(start_idx + items_per_page, len(products))
    current_products = products[start_idx:end_idx]

    for product in current_products:
        # Create inline keyboard for each product
        keyboard = []
        # Add Contact Seller button if seller has a username
        if product.get('poster_username'):
            keyboard.append([InlineKeyboardButton("📞 Contact Seller",
                                                  url=f"https://t.me/{product.get('poster_username')}")])
        else:
            # Fallback to deep link if no username
            keyboard.append([InlineKeyboardButton("📞 Contact Seller",
                                                  url=f"https://t.me/{context.bot.username}?start=item_{product['id']}")])
        keyboard.append([InlineKeyboardButton("📋 Product Details", callback_data=f"product_details_{product['id']}")])

        # Add View Post button if the product has been posted and has a message_id
        if product.get('posted', False) and product.get('channel_message_id'):
            keyboard.append([InlineKeyboardButton("👁️ View Post",
                                                  url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{product['channel_message_id']}")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        # Status indicator
        if product.get('scheduled_time'):
            status = f"⏰ Scheduled for {product['scheduled_time']}"
        elif product.get('posted', False):
            status = "✅ Posted"
        else:
            status = "⏳ Not posted yet"

        # Show category and subcategory if available
        category_info = ""
        if product.get('category'):
            category_info = f"{product.get('category')}"
            if product.get('subcategory'):
                category_info += f" - {product.get('subcategory')}"
            category_info += "\n"

        await update.message.reply_photo(
            photo=product['image_file_id'],
            caption=f"📦 <b>{product['name']}</b>\n\n"
                    f"{category_info}"
                    f"💰 Price: {product['price']:.2f} ETB\n"
                    f"Status: {status}",
            reply_markup=reply_markup,
            parse_mode='HTML'
        )

    # Navigation buttons
    has_prev = page > 0
    has_next = end_idx < len(products)

    nav_buttons = []
    if has_prev:
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"page_{page - 1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"page_{page + 1}"))

    nav_keyboard = []
    if nav_buttons:
        nav_keyboard.append(nav_buttons)
    nav_keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")])

    nav_markup = InlineKeyboardMarkup(nav_keyboard)

    await update.message.reply_text(
        f"Showing products {start_idx + 1}-{end_idx} of {len(products)}",
        reply_markup=nav_markup
    )


async def add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the add product conversation by selecting a category."""
    # Create a keyboard with product categories
    keyboard = []
    for category in PRODUCT_CATEGORIES.keys():
        keyboard.append([InlineKeyboardButton(category, callback_data=f"category_{category}")])

    # Add custom category option
    keyboard.append([InlineKeyboardButton("➕ Custom Category", callback_data="custom_category")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📂 Main Category: ይምረጡ (ለምሳሌ፡ 'Electronics')።",
        reply_markup=reply_markup
    )
    await update.message.reply_text(
        "Or use the button below to cancel:",
        reply_markup=get_cancel_keyboard()
    )
    return SELECT_CATEGORY


async def select_product_category(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle category selection for a new product."""
    query = update.callback_query
    await query.answer()

    # Delete the original message with inline keyboard
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

    if query.data.startswith("category_"):
        category = query.data[9:]  # Remove "category_" prefix
        context.user_data['product_category'] = category

        # Show subcategories for this category
        subcategories = PRODUCT_CATEGORIES.get(category, [])

        keyboard = []
        for subcategory in subcategories:
            keyboard.append([InlineKeyboardButton(subcategory, callback_data=f"subcategory_{subcategory}")])

        keyboard.append([InlineKeyboardButton("➕ Custom Subcategory", callback_data="custom_subcategory")])
        keyboard.append([InlineKeyboardButton("🔙 Back to Categories", callback_data="back_to_categories")])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.message.reply_text(
            f"Selected category: {category}\n\n📂 Sub Category: ይምረጡ (ለምሳሌ፡ Accessories )።",
            reply_markup=reply_markup
        )
        await query.message.reply_text(
            "Or use the button below to cancel:",
            reply_markup=get_cancel_keyboard()
        )
        return SELECT_SUBCATEGORY

    elif query.data == "custom_category":
        await query.message.reply_text(
            "Please enter a custom category for your product.\n"
            "Make sure it starts with # (e.g., #Fashion, #Technology):",
            reply_markup=get_cancel_keyboard()
        )
        return CUSTOM_TAG

    elif query.data == "back_to_categories":
        return await add_product_start(query, context)

    return SELECT_CATEGORY


async def select_product_subcategory(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle subcategory selection for a new product."""
    query = update.callback_query
    await query.answer()

    # Delete the original message with inline keyboard
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

    if query.data.startswith("subcategory_"):
        subcategory = query.data[12:]  # Remove "subcategory_" prefix
        context.user_data['product_subcategory'] = subcategory

        await query.message.reply_text(
            f"Selected category: {context.user_data.get('product_category')}\n"
            f"Selected subcategory: {subcategory}\n\n"
            f"✍🏻 የምርትዎን ስም ያስገቡ (ግልፅ ይሁን)።",
            reply_markup=get_cancel_keyboard()
        )
        return PRODUCT_NAME

    elif query.data == "custom_subcategory":
        await query.message.reply_text(
            "Please enter a custom subcategory for your product.\n"
            "Make sure it starts with # (e.g., #Premium, #Budget):",
            reply_markup=get_cancel_keyboard()
        )
        return CUSTOM_TAG

    elif query.data == "back_to_categories":
        return await add_product_start(query, context)

    return SELECT_SUBCATEGORY


async def custom_product_tag(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle custom tag input for a product."""
    tag = update.message.text.strip()

    # Ensure tag starts with #
    if not tag.startswith('#'):
        tag = '#' + tag

    # Check if we're in category or subcategory selection
    if 'product_category' not in context.user_data:
        # We're setting the category
        context.user_data['product_category'] = tag

        # Ask for subcategory
        keyboard = [
            [InlineKeyboardButton("➕ Custom Subcategory", callback_data="custom_subcategory")],
            [InlineKeyboardButton("Skip Subcategory", callback_data="skip_subcategory")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Custom category set: {tag}\n\nNow, select or create a subcategory:",
            reply_markup=reply_markup
        )
        await update.message.reply_text(
            "Or use the button below to cancel:",
            reply_markup=get_cancel_keyboard()
        )
        return SELECT_SUBCATEGORY
    else:
        # We're setting the subcategory
        await update.message.reply_text(
            f"Custom subcategory set: {tag}\n\n✍🏻 የምርትዎን ስም ያስገቡ (ግልፅ ይሁን)።",
            reply_markup=get_cancel_keyboard()
        )
        return PRODUCT_NAME


async def product_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the product name and ask for description."""
    context.user_data['product_name'] = update.message.text
    await update.message.reply_text(
        "✍️ Description: ስለ ምርትዎ ተጨማሪ መረጃ ይስጡ (ልዩ ባህሪያቱ ምንድን ናቸው?)።",
        reply_markup=get_cancel_keyboard()
    )
    return PRODUCT_DESCRIPTION


async def product_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the product description and ask for price."""
    context.user_data['product_description'] = update.message.text
    await update.message.reply_text(
        "💵 Price: የምርትዎን ዋጋ ያስገቡ ።",
        reply_markup=get_cancel_keyboard()
    )
    return PRODUCT_PRICE


async def product_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the product price and ask for an image."""
    try:
        price = float(update.message.text)
        context.user_data['product_price'] = price
        await update.message.reply_text(
            "Please send an image of the product.\n\n"
            "Note: The image width should be greater than or equal to its height for proper display.",
            reply_markup=get_cancel_keyboard()
        )
        return PRODUCT_IMAGE
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid price (numbers only).",
            reply_markup=get_cancel_keyboard()
        )
        return PRODUCT_PRICE


async def check_image_safety_and_dimensions(file_id, context):
    """Check if the image is safe and has proper dimensions."""
    try:
        # Download the image
        file = await context.bot.get_file(file_id)
        file_url = file.file_path

        # Use requests to download the image
        response = requests.get(file_url)
        img = Image.open(BytesIO(response.content))

        # Check dimensions
        width, height = img.size
        if width < height:
            return False, "Image dimensions issue", f"Image width ({width}) is less than height ({height}). Please use an image where width ≥ height."

        # Save temporarily for safety check
        if NUDENET_AVAILABLE:
            temp_path = f"temp_{file_id}.jpg"
            img.save(temp_path)

            # Classify the image
            result = classifier.classify(temp_path)

            # Clean up
            if os.path.exists(temp_path):
                os.remove(temp_path)

            # Check if the image is unsafe
            unsafe_score = result[temp_path]['unsafe']

            if unsafe_score > 0.6:  # Threshold for unsafe content
                return False, "Safety issue", f"Image appears to contain inappropriate content (score: {unsafe_score:.2f})"

        return True, "OK", "Image is safe and has proper dimensions"

    except Exception as e:
        logger.error(f"Error checking image: {e}")
        return False, "Error", f"Error checking image: {str(e)}"


async def product_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the product image and save the product."""
    if update.message.photo:
        # Get the largest photo (best quality)
        photo = update.message.photo[-1]
        file_id = photo.file_id

        # Check if the image is safe and has proper dimensions
        is_valid, issue_type, message = await check_image_safety_and_dimensions(file_id, context)

        if not is_valid:
            await update.message.reply_text(
                f"⚠️ {issue_type}: {message}\n\n"
                "Please send a different image that meets our requirements.",
                reply_markup=get_cancel_keyboard()
            )
            return PRODUCT_IMAGE

        # Get user information
        user = update.effective_user
        username = user.username if user.username else user.first_name
        user_id = user.id

        # Get user data
        user_data = get_user_data(user_id)

        # Save product data
        product = {
            'id': str(datetime.now().timestamp()),  # Unique ID based on timestamp
            'name': context.user_data['product_name'],
            'description': context.user_data['product_description'],
            'price': context.user_data['product_price'],
            'category': context.user_data.get('product_category', '#Other'),
            'subcategory': context.user_data.get('product_subcategory', ''),
            'image_file_id': file_id,
            'date_added': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'posted': False,
            'poster_username': username,
            'poster_id': user_id,
            'poster_name': user_data.get('name', ''),
            'poster_phone': user_data.get('phone', ''),
            'poster_address': user_data.get('address', ''),
            'scheduled_time': None,
            'channel_message_id': None  # Store the message ID when posted to channel
        }

        # Store in context for scheduling
        context.user_data['product'] = product

        # Ask if user wants to schedule the post
        keyboard = [
            [InlineKeyboardButton("📢 Post Now", callback_data="schedule_now")],
            [InlineKeyboardButton("⏰ Schedule for Later", callback_data="schedule_later")],
            [InlineKeyboardButton("💾 Save Only", callback_data="save_only")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Format category info
        category_info = f"Category: {product['category']}"
        if product['subcategory']:
            category_info += f"\nSubcategory: {product['subcategory']}"

        await update.message.reply_text(
            f"✅ Product details saved!\n\n"
            f"Name: {product['name']}\n"
            f"{category_info}\n"
            f"Description: {product['description']}\n"
            f"Price: {product['price']:.2f} ETB\n\n"
            f"What would you like to do with this product?",
            reply_markup=reply_markup
        )

        return SCHEDULE_POST
    else:
        await update.message.reply_text(
            "Please send an image file.",
            reply_markup=get_cancel_keyboard()
        )
        return PRODUCT_IMAGE


async def handle_scheduling(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the scheduling decision."""
    query = update.callback_query
    await query.answer()

    # Delete the original message with inline keyboard
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

    if query.data == "schedule_now":
        # Save the product
        product = context.user_data['product']
        products = load_products()
        products.append(product)
        save_products(products)

        # Post immediately
        post_result = await post_product_by_id(context, product['id'])

        if post_result['success']:
            await query.message.reply_text(
                f"✅ Product '{product['name']}' has been added and posted to the channel!"
            )

            # Show view post button if available
            if post_result.get('message_id'):
                keyboard = [
                    [InlineKeyboardButton("👁️ View Post",
                                          url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{post_result['message_id']}")],
                    [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.message.reply_text(
                    "You can view your posted product in the channel:",
                    reply_markup=reply_markup
                )
        else:
            await query.message.reply_text(
                f"❌ Error posting product: {post_result['message']}"
            )

        # Show main menu
        await query.message.reply_text(
            "What would you like to do next?",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    elif query.data == "schedule_later":
        await query.message.reply_text(
            "When would you like to schedule this post? Please enter date and time in format:\n"
            "YYYY-MM-DD HH:MM\n\n"
            "For example: 2025-05-15 14:30",
            reply_markup=get_cancel_keyboard()
        )
        return SCHEDULE_POST

    elif query.data == "save_only":
        # Just save the product without posting
        product = context.user_data['product']
        products = load_products()
        products.append(product)
        save_products(products)

        await query.message.reply_text(
            f"✅ Product '{product['name']}' has been saved to your products."
        )

        # Show main menu
        await query.message.reply_text(
            "What would you like to do next?",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    return MAIN_MENU


async def schedule_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Schedule the post for a specific time."""
    text = update.message.text

    # Parse the date and time
    try:
        # Check if the format is correct
        if not re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$', text):
            await update.message.reply_text(
                "Invalid format. Please use YYYY-MM-DD HH:MM format.\n"
                "For example: 2025-05-15 14:30",
                reply_markup=get_cancel_keyboard()
            )
            return SCHEDULE_POST

        scheduled_time = datetime.strptime(text, "%Y-%m-%d %H:%M")

        # Check if the time is in the future
        if scheduled_time <= datetime.now():
            await update.message.reply_text(
                "The scheduled time must be in the future. Please enter a future date and time.",
                reply_markup=get_cancel_keyboard()
            )
            return SCHEDULE_POST

        # Save the product with scheduled time
        product = context.user_data['product']
        product['scheduled_time'] = scheduled_time.strftime("%Y-%m-%d %H:%M:%S")

        products = load_products()
        products.append(product)
        save_products(products)

        # Schedule the post using the application's job queue
        context.job_queue.run_once(
            lambda ctx: post_scheduled_product(product['id'], ctx.bot),
            scheduled_time,
            name=f"scheduled_{product['id']}"
        )

        await update.message.reply_text(
            f"✅ Product '{product['name']}' has been scheduled for posting on:\n"
            f"{scheduled_time.strftime('%Y-%m-%d %H:%M')}",
            reply_markup=get_main_menu_keyboard()
        )

        return MAIN_MENU

    except ValueError:
        await update.message.reply_text(
            "Invalid date format. Please use YYYY-MM-DD HH:MM format.\n"
            "For example: 2025-05-15 14:30",
            reply_markup=get_cancel_keyboard()
        )
        return SCHEDULE_POST


async def post_scheduled_product(product_id, bot):
    """Post a scheduled product."""
    products = load_products()

    # Find the product
    product = None
    for p in products:
        if p.get('id') == product_id:
            product = p
            break

    if not product:
        logger.error(f"Scheduled product {product_id} not found")
        return

    try:
        # Create inline keyboard with contact seller button
        keyboard = []
        if product.get('poster_username'):
            keyboard.append([InlineKeyboardButton("📞 Contact Seller",
                                                  url=f"https://t.me/{product.get('poster_username')}")])
        else:
            # Fallback to deep link if no username
            keyboard.append([InlineKeyboardButton("📞 Contact Seller",
                                                  url=f"https://t.me/{bot.username}?start=item_{product_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Format category info
        category_info = ""
        if product.get('category'):
            category_info = f"{product.get('category')}"
            if product.get('subcategory'):
                category_info += f" - {product.get('subcategory')}"
            category_info += "\n\n"

        # Post to channel
        message = await bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=product['image_file_id'],
            caption=f"🆕 NEW PRODUCT 🆕\n\n"
                    f"📌 {product['name']}\n\n"
                    f"{category_info}"
                    f"📝 {product['description']}\n\n"
                    f"💰 Price: {product['price']:.2f} ETB\n\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            reply_markup=reply_markup
        )

        # Update product status and store message ID
        for p in products:
            if p.get('id') == product_id:
                p['posted'] = True
                p['post_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                p['channel_message_id'] = message.message_id
                break

        save_products(products)
        posted_products.add(product_id)

        logger.info(f"Scheduled product '{product['name']}' posted successfully")

    except Exception as e:
        logger.error(f"Error posting scheduled product: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the conversation."""
    await update.message.reply_text(
        "Operation cancelled.",
        reply_markup=get_main_menu_keyboard()
    )
    return MAIN_MENU


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle button callbacks."""
    query = update.callback_query
    await query.answer()

    # Delete the original message with inline keyboard
    try:
        await query.message.delete()
    except Exception as e:
        logger.error(f"Error deleting message: {e}")

    # Main menu navigation
    if query.data == "back_to_main":
        await query.message.reply_text(
            "Main Menu:",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Product management
    elif query.data == "add_product":
        return await add_product_start(query, context)

    elif query.data == "cancel_add_product":
        await query.message.reply_text(
            "Product addition cancelled.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    elif query.data == "list_my_products":
        return await list_user_products(query, context)

    # Category and subcategory selection
    elif query.data.startswith("category_") or query.data == "custom_category" or query.data == "back_to_categories":
        return await select_product_category(update, context)

    elif query.data.startswith("subcategory_") or query.data == "custom_subcategory":
        return await select_product_subcategory(update, context)

    elif query.data == "skip_subcategory":
        await query.message.reply_text(
            f"Selected category: {context.user_data.get('product_category')}\n"
            f"Subcategory: Skipped\n\n"
            f"Now, what's the product name?",
            reply_markup=get_cancel_keyboard()
        )
        return PRODUCT_NAME

    # Scheduling
    elif query.data.startswith("schedule_"):
        if query.data in ["schedule_now", "schedule_later", "save_only"]:
            return await handle_scheduling(update, context)
        else:
            product_id = query.data.split("_")[1]
            return await handle_product_scheduling(query, context, product_id)

    # Post product
    elif query.data.startswith("post_"):
        product_id = query.data.split("_")[1]
        post_result = await post_product_by_id(context, product_id)

        if post_result['success']:
            # Show success message with view post button if available
            if post_result.get('message_id'):
                keyboard = [
                    [InlineKeyboardButton("👁️ View Post",
                                          url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{post_result['message_id']}")],
                    [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)

                await query.message.reply_text(
                    "✅ Product posted successfully! You can view it in the channel:",
                    reply_markup=reply_markup
                )
            else:
                await query.message.reply_text(
                    "✅ Product posted successfully!",
                    reply_markup=get_main_menu_keyboard()
                )
        else:
            await query.message.reply_text(
                f"❌ Error posting product: {post_result['message']}",
                reply_markup=get_main_menu_keyboard()
            )

        return MAIN_MENU

    # Delete product
    elif query.data.startswith("delete_"):
        product_id = query.data.split("_")[1]
        return await delete_product(query, context, product_id)

    # Confirm delete product
    elif query.data.startswith("confirm_delete_"):
        product_id = query.data.split("_")[2]
        return await confirm_delete_product(query, context, product_id)

    # Edit product
    elif query.data.startswith("edit_"):
        product_id = query.data.split("_")[1]
        await query.message.reply_text(
            "Edit feature is currently under development.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Preferences
    elif query.data == "toggle_auto_post":
        return await toggle_preference(query, context, 'auto_post')

    elif query.data == "toggle_notifications":
        return await toggle_preference(query, context, 'notifications')

    elif query.data == "toggle_theme":
        return await toggle_theme(query, context)

    # Registration
    elif query.data in ["confirm_registration", "restart_registration"]:
        return await register_confirm(update, context)

    # Contact seller
    elif query.data.startswith("contact_seller_"):
        product_id = query.data.split("_")[2]
        await show_seller_contact(query, context, product_id)
        return MAIN_MENU

    # Product details
    elif query.data.startswith("product_details_"):
        product_id = query.data.split("_")[2]
        await show_product_details(query, context, product_id)
        return MAIN_MENU

    # Pagination
    elif query.data.startswith("page_"):
        page = int(query.data.split("_")[1])
        products = load_products()
        await show_product_page(query, context, products, page)
        return MAIN_MENU

    # Edit profile
    elif query.data == "edit_profile":
        await query.message.reply_text(
            "Profile editing feature is currently under development.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # View post in channel
    elif query.data.startswith("view_post_"):
        product_id = query.data.split("_")[2]
        products = load_products()

        # Find the product
        product = None
        for p in products:
            if p.get('id') == product_id:
                product = p
                break

        if product and product.get('channel_message_id'):
            # Redirect to the post in the channel
            await query.message.reply_text(
                f"Opening the post in the channel...",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👁️ View Post",
                                         url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{product['channel_message_id']}")
                ]])
            )
        else:
            await query.message.reply_text(
                "Post not found or not yet published to the channel.",
                reply_markup=get_main_menu_keyboard()
            )

        return MAIN_MENU

    return MAIN_MENU


async def delete_product(query, context, product_id):
    """Delete a product."""
    products = load_products()

    # Find the product index
    product_idx = None
    for i, p in enumerate(products):
        if p.get('id') == product_id:
            product_idx = i
            break

    if product_idx is None:
        await query.message.reply_text(
            "Product not found.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Ask for confirmation
    product = products[product_idx]

    # Create confirmation buttons
    keyboard = [
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_{product_id}")],
        [InlineKeyboardButton("❌ No, Cancel", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text(
        f"Are you sure you want to delete the product '{product['name']}'?\n\n"
        "This action cannot be undone.",
        reply_markup=reply_markup
    )

    return MAIN_MENU


async def confirm_delete_product(query, context, product_id):
    """Confirm and execute product deletion."""
    products = load_products()

    # Find the product index
    product_idx = None
    for i, p in enumerate(products):
        if p.get('id') == product_id:
            product_idx = i
            break

    if product_idx is None:
        await query.message.reply_text(
            "Product not found or already deleted.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Get product name before deletion
    product_name = products[product_idx]['name']

    # Remove the product
    del products[product_idx]
    save_products(products)

    # Confirm deletion
    await query.message.reply_text(
        f"✅ Product '{product_name}' has been deleted successfully.",
        reply_markup=get_main_menu_keyboard()
    )

    return MAIN_MENU


async def show_seller_contact(query, context, product_id):
    """Show seller contact information."""
    products = load_products()

    # Find the product
    product = None
    for p in products:
        if p.get('id') == product_id:
            product = p
            break

    if not product:
        await query.message.reply_text(
            "Product information not found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
            ]])
        )
        return

    # Get seller information
    seller_name = product.get('poster_name', 'Not provided')
    seller_phone = product.get('poster_phone', 'Not provided')
    seller_username = product.get('poster_username', 'Not provided')
    seller_address = product.get('poster_address', 'Not provided')

    message = (
        f"📞 <b>Seller Contact Information</b>\n\n"
        f"Product: <b>{product['name']}</b>\n\n"
        f"<b>👤 @{seller_username}</b>\n"
        f"Seller Name: {seller_name}\n"
        f"Phone: {seller_phone}\n"
        f"Address: {seller_address}\n\n"
        f"You can contact the seller directly about this product."
    )

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data=f"product_details_{product_id}")]]

    # Add View Post button if the product has been posted
    if product.get('posted', False) and product.get('channel_message_id'):
        keyboard.append([InlineKeyboardButton("👁️ View Post",
                                              url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{product['channel_message_id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )


async def list_user_products(query, context):
    """List products for the current user."""
    user_id = query.from_user.id
    products = load_products()

    # Filter products by this user
    user_products = [p for p in products if p.get('poster_id') == user_id]

    if not user_products:
        await query.message.reply_text(
            "You don't have any products yet.",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await query.message.reply_text(f"You have {len(user_products)} products:")

        for i, product in enumerate(user_products):
            if product.get('scheduled_time'):
                status = f"⏰ Scheduled for {product['scheduled_time']}"
            elif product.get('posted', False):
                status = "✅ Posted"
            else:
                status = "⏳ Not posted yet"

            # Create inline keyboard for each product
            keyboard = [
                [InlineKeyboardButton("📢 Post Now", callback_data=f"post_{product['id']}")],
                [InlineKeyboardButton("✏️ Edit", callback_data=f"edit_{product['id']}")],
                [InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{product['id']}")]
            ]

            # Add View Post button if the product has been posted
            if product.get('posted', False) and product.get('channel_message_id'):
                keyboard.append([InlineKeyboardButton("👁️ View Post",
                                                      url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{product['channel_message_id']}")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            # Format category info
            category_info = ""
            if product.get('category'):
                category_info = f"{product.get('category')}"
                if product.get('subcategory'):
                    category_info += f" - {product.get('subcategory')}"
                category_info += "\n"

            await query.message.reply_photo(
                photo=product['image_file_id'],
                caption=f"Product #{i + 1}\n"
                        f"Name: {product['name']}\n"
                        f"{category_info}"
                        f"Price: {product['price']:.2f} ETB\n"
                        f"Added: {product['date_added']}\n"
                        f"Status: {status}",
                reply_markup=reply_markup
            )

    return MAIN_MENU


async def handle_product_scheduling(query, context, product_id):
    """Handle scheduling for a specific product."""
    products = load_products()

    # Find the product
    product = None
    for p in products:
        if p.get('id') == product_id:
            product = p
            break

    if not product:
        await query.message.reply_text(
            "Product not found.",
            reply_markup=get_main_menu_keyboard()
        )
        return MAIN_MENU

    # Store the product ID in context
    context.user_data['scheduling_product_id'] = product_id

    await query.message.reply_text(
        f"When would you like to schedule '{product['name']}' for posting?\n\n"
        "Please enter date and time in format: YYYY-MM-DD HH:MM\n"
        "For example: 2025-05-15 14:30",
        reply_markup=get_cancel_keyboard()
    )

    return SCHEDULE_POST


async def toggle_preference(query, context, pref_key):
    """Toggle a user preference."""
    user_id = query.from_user.id
    prefs = get_user_preferences(user_id)

    # Toggle the preference
    current_value = prefs.get(pref_key, True)
    update_user_preference(user_id, pref_key, not current_value)

    # Refresh the preferences display
    return await refresh_preferences(query, context)


async def toggle_theme(query, context):
    """Toggle between light and dark theme."""
    user_id = query.from_user.id
    prefs = get_user_preferences(user_id)

    # Toggle theme
    current_theme = prefs.get('theme', 'light')
    new_theme = 'dark' if current_theme == 'light' else 'light'
    update_user_preference(user_id, 'theme', new_theme)

    # Refresh the preferences display
    return await refresh_preferences(query, context)


async def refresh_preferences(query, context):
    """Refresh the preferences display."""
    user_id = query.from_user.id
    prefs = get_user_preferences(user_id)

    keyboard = [
        [InlineKeyboardButton(
            f"Auto-post: {'✅ ON' if prefs.get('auto_post', True) else '❌ OFF'}",
            callback_data="toggle_auto_post"
        )],
        [InlineKeyboardButton(
            f"Notifications: {'✅ ON' if prefs.get('notifications', True) else '❌ OFF'}",
            callback_data="toggle_notifications"
        )],
        [InlineKeyboardButton(
            f"Theme: {prefs.get('theme', 'light').capitalize()}",
            callback_data="toggle_theme"
        )],
        [InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.reply_text(
        "⭐ <b>Preferences</b>\n\n"
        "Customize your experience by adjusting the settings below:",
        reply_markup=reply_markup,
        parse_mode='HTML'
    )

    return MAIN_MENU


async def show_product_details(query, context, product_id):
    """Show detailed information about a product."""
    products = load_products()

    # Find the product
    product = None
    for p in products:
        if p.get('id') == product_id:
            product = p
            break

    if not product:
        await query.message.reply_text(
            "Product not found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="back_to_main")
            ]])
        )
        return

    # Status indicator
    if product.get('scheduled_time'):
        status = f"⏰ Scheduled for {product['scheduled_time']}"
    elif product.get('posted', False):
        status = f"✅ Posted"
    else:
        status = "⏳ Not posted yet"

    # Format category info
    category_info = ""
    if product.get('category'):
        category_info = f"{product.get('category')}"
        if product.get('subcategory'):
            category_info += f" - {product.get('subcategory')}"
        category_info += "\n\n"

    # Create detailed message
    message = (
        f"📦 <b>{product['name']}</b>\n\n"
        f"{category_info}"
        f"📝 <b>Description:</b>\n{product['description']}\n\n"
        f"💰 <b>Price:</b> {product['price']:.2f} ETB\n"
        f"📅 <b>Added:</b> {product['date_added']}\n"
        f"🔄 <b>Status:</b> {status}"
    )

    # Create action buttons
    keyboard = []

    # Only show post button if not already posted
    if not product.get('posted', False):
        keyboard.append([InlineKeyboardButton("📢 Post Now", callback_data=f"post_{product['id']}")])

    keyboard.append([InlineKeyboardButton("📞 Contact Seller", callback_data=f"contact_seller_{product['id']}")])

    # Add View Post button if the product has been posted
    if product.get('posted', False) and product.get('channel_message_id'):
        keyboard.append([InlineKeyboardButton("👁️ View Post",
                                              url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{product['channel_message_id']}")])

    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="back_to_main")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Edit the message with detailed info
    await query.message.reply_text(
        message,
        reply_markup=reply_markup,
        parse_mode='HTML'
    )


async def post_product_by_id(context, product_id, query=None):
    """Post a product to the channel by its ID."""
    products = load_products()
    result = {'success': False, 'message': '', 'message_id': None}

    # Find the product with the given ID
    product = None
    for p in products:
        if p.get('id') == product_id:
            product = p
            break

    if not product:
        result['message'] = "Product not found."
        if query:
            await query.message.reply_text(result['message'])
        return result

    try:
        # Create inline keyboard with contact seller button
        keyboard = []
        if product.get('poster_username'):
            keyboard.append([InlineKeyboardButton("📞 Contact Seller",
                                                  url=f"https://t.me/{product.get('poster_username')}")])
        else:
            # Fallback to deep link if no username
            keyboard.append([InlineKeyboardButton("📞 Contact Seller",
                                                  url=f"https://t.me/{context.bot.username}?start=item_{product_id}")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Format category info
        category_info = ""
        if product.get('category'):
            category_info = f"{product.get('category')}"
            if product.get('subcategory'):
                category_info += f" - {product.get('subcategory')}"
            category_info += "\n\n"

        # Post to channel
        message = await context.bot.send_photo(
            chat_id=CHANNEL_ID,
            photo=product['image_file_id'],
            caption=f"🆕 NEW PRODUCT 🆕\n\n"
                    f"📌 {product['name']}\n\n"
                    f"{category_info}"
                    f"📝 {product['description']}\n\n"
                    f"💰 Price: {product['price']:.2f} ETB\n\n"
                    f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            reply_markup=reply_markup
        )

        # Update product status and store message ID
        for p in products:
            if p.get('id') == product_id:
                p['posted'] = True
                p['post_date'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                p['channel_message_id'] = message.message_id
                break

        save_products(products)
        posted_products.add(product_id)

        result['success'] = True
        result['message'] = f"Product '{product['name']}' posted to the channel successfully!"
        result['message_id'] = message.message_id

        if query:
            await query.message.reply_text(result['message'])

        return result

    except Exception as e:
        logger.error(f"Error posting to channel: {e}")
        result['message'] = (
            f"Error posting to channel. Make sure the bot is an admin in the channel "
            f"and has posting permissions.\n\nError: {str(e)}"
        )
        if query:
            await query.message.reply_text(result['message'])

        return result


async def auto_post_products(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Automatically post products to the channel."""
    if not AUTO_POST_ENABLED:
        return

    logger.info("Running scheduled auto-post job")
    products = load_products()

    # Filter products that haven't been posted yet and aren't scheduled
    unposted_products = [p for p in products if not p.get('posted', False)
                         and p.get('id') not in posted_products
                         and not p.get('scheduled_time')]

    if not unposted_products:
        logger.info("No unposted products found")
        return

    # Select products to post (up to the limit)
    to_post = unposted_products[:AUTO_POST_LIMIT]

    # Post each product
    for product in to_post:
        # Check if user has auto-post enabled in preferences
        user_id = product.get('poster_id')
        if user_id:
            prefs = get_user_preferences(user_id)
            if not prefs.get('auto_post', True):
                logger.info(f"Skipping auto-post for product '{product['name']}' as user has disabled auto-post")
                continue

        post_result = await post_product_by_id(context, product['id'])
        if post_result['success']:
            logger.info(f"Auto-posted product: {product['name']}")
        else:
            logger.error(f"Failed to auto-post product: {product['name']}")

        # Add a small delay between posts to avoid flooding
        await asyncio.sleep(2)


async def handle_deep_linking(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle deep linking for contact seller."""
    user = update.effective_user
    logger.info(f"Deep linking handler called for user {user.id} with args: {context.args}")

    # Check if this is a deep link
    if context.args and (context.args[0].startswith("contact_") or context.args[0].startswith("item_")):
        # Extract product ID
        product_id = context.args[0].split("_")[1]
        logger.info(f"Processing deep link for product {product_id}")

        # Check if user is registered
        if not is_user_registered(user.id):
            # Store the product ID for after registration
            context.user_data['pending_contact_product_id'] = product_id
            logger.info(f"User {user.id} not registered, storing pending contact for product {product_id}")

            # Start registration process
            await update.message.reply_html(
                f"Welcome {user.mention_html()}! Before you can contact the seller, please complete a quick registration.\n\n"
                f"What is your full name?"
            )
            return REGISTER_NAME

        # User is registered, immediately show seller contact
        logger.info(f"User {user.id} is registered, showing seller contact for product {product_id}")
        await show_seller_contact_from_deeplink(update, context, product_id)
        return MAIN_MENU

    # Regular start command
    logger.info(f"No deep link detected, proceeding with normal start for user {user.id}")
    return await start(update, context)


async def show_seller_contact_from_deeplink(update, context, product_id):
    """Show seller contact information from deep link."""
    logger.info(f"Showing seller contact for product {product_id}")

    # Delete any previous messages if this is a callback query
    if hasattr(update, 'callback_query') and update.callback_query:
        try:
            await update.callback_query.message.delete()
        except Exception as e:
            logger.error(f"Error deleting message: {e}")

    products = load_products()

    # Find the product
    product = None
    for p in products:
        if p.get('id') == product_id:
            product = p
            break

    if not product:
        logger.error(f"Product {product_id} not found when trying to show seller contact")
        await update.message.reply_text(
            "Product information not found.",
            reply_markup=get_main_menu_keyboard()
        )
        return

    # Get seller information
    seller_name = product.get('poster_name', 'Not provided')
    seller_phone = product.get('poster_phone', 'Not provided')
    seller_username = product.get('poster_username', 'Not provided')
    seller_address = product.get('poster_address', 'Not provided')

    # Format product price
    price = f"{product.get('price', 0):.2f} ETB"

    # Create caption with seller details
    caption = (
        f"🔔 <b>NEW LISTING: {product['name']}</b>\n\n"
        f"💰 <b>Price:</b> {price}\n\n"
        f"👤 <b>SELLER DETAILS:</b>\n"
        f"<b>👤 @{seller_username}</b>\n"
        f"📋 <b>Name:</b> {seller_name}\n"
        f"📱 <b>Phone:</b> {seller_phone}\n"
        f"📍 <b>Address:</b> {seller_address}\n\n"
        f"⌛️ <b>Only one available!</b>"
    )

    keyboard = [[InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")]]

    # Add View Post button if the product has been posted
    if product.get('posted', False) and product.get('channel_message_id'):
        keyboard.insert(0, [InlineKeyboardButton("👁️ View Post",
                                                 url=f"https://t.me/{CHANNEL_ID.replace('@', '')}/{product['channel_message_id']}")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    # Send product image with seller information as caption
    try:
        await update.message.reply_photo(
            photo=product['image_file_id'],
            caption=caption,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Error sending product image: {e}")
        # Fallback to text-only message if image sending fails
        await update.message.reply_html(
            caption,
            reply_markup=reply_markup
        )

    # Log that contact information was viewed
    logger.info(f"User {update.effective_user.id} viewed seller contact for product {product['id']}")

    # Notify the seller that someone is interested (if notifications are enabled)
    await notify_seller_of_interest(context, product, update.effective_user)


async def notify_seller_of_interest(context, product, interested_user):
    """Notify the seller that someone is interested in their product."""
    # Check if the product has a seller ID
    seller_id = product.get('poster_id')
    if not seller_id:
        return

    # Get seller preferences
    seller_prefs = get_user_preferences(seller_id)

    # Only send notification if seller has notifications enabled
    if not seller_prefs.get('notifications', True):
        return

    try:
        # Format buyer info
        buyer_name = interested_user.first_name
        if interested_user.last_name:
            buyer_name += f" {interested_user.last_name}"

        buyer_username = f"@{interested_user.username}" if interested_user.username else "No username"

        # Get buyer data if registered
        buyer_data = get_user_data(interested_user.id)
        buyer_phone = buyer_data.get('phone', 'Not provided') if buyer_data else 'Not provided'

        # Send notification to seller
        await context.bot.send_message(
            chat_id=seller_id,
            text=f"🔔 <b>New Buyer Interest!</b>\n\n"
                 f"Someone is interested in your product: <b>{product['name']}</b>\n\n"
                 f"<b>Potential Buyer:</b>\n"
                 f"Name: {buyer_name}\n"
                 f"Username: {buyer_username}\n\n"
                 f"They have viewed your contact information and may contact you soon.",
            parse_mode='HTML'
        )

        logger.info(
            f"Notified seller {seller_id} about interest from user {interested_user.id} in product {product['id']}")

    except Exception as e:
        logger.error(f"Failed to notify seller {seller_id}: {e}")


def main() -> None:
    """Start the bot."""
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add conversation handler for the entire bot interaction
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", handle_deep_linking)],
        states={
            # Main menu states
            MAIN_MENU: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), handle_main_menu),
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],

            # Registration states
            REGISTER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), register_name),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            REGISTER_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), register_phone),
                MessageHandler(filters.CONTACT, register_phone),  # Handle shared contact
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            REGISTER_ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), register_address),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            REGISTER_CONFIRM: [
                CallbackQueryHandler(register_confirm),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],

            # Product creation states
            SELECT_CATEGORY: [
                CallbackQueryHandler(select_product_category),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            SELECT_SUBCATEGORY: [
                CallbackQueryHandler(select_product_subcategory),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            CUSTOM_TAG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), custom_product_tag),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            PRODUCT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), product_name),
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            PRODUCT_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), product_description),
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            PRODUCT_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), product_price),
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            PRODUCT_IMAGE: [
                MessageHandler(filters.PHOTO, product_image),
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            SCHEDULE_POST: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), schedule_post),
                CallbackQueryHandler(button_callback),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ],
            CONTACT_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~filters.Regex('^❌ Cancel$'), handle_contact_message),
                MessageHandler(filters.Regex('^❌ Cancel$'), handle_cancel),
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )

    # Add command handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("help", help_command))

    # Set up auto-posting using the application's job queue
    if AUTO_POST_ENABLED:
        # Schedule auto-posting using the application's job queue
        application.job_queue.run_repeating(
            auto_post_products,
            interval=timedelta(hours=AUTO_POST_INTERVAL),
            first=60,  # Start 60 seconds after bot startup
            name="auto_post"
        )

        logger.info(f"Auto-posting scheduled every {AUTO_POST_INTERVAL} hours")

    # Start the Bot
    logger.info("Starting bot...")
    application.run_polling()
    logger.info("Bot started")


if __name__ == '__main__':
    main()
