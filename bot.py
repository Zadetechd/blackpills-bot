import os
import logging
import re
import sqlite3
from datetime import datetime, time
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from flask import Flask, render_template_string
import threading

# ==================== CONFIGURATION ====================
BOT_TOKEN = "7886209681:AAEAnX1vqna6LWycavtd8RMjX_57khuCtCk"
PAYMENT_GROUP_ID = -4946335222
DEPOSIT_GROUP_ID = -4818580578
DASHBOARD_URL = "https://blackpills-bot.onrender.com"
ADMIN_PASSCODE = "nova"

PRIVILEGED_USERS = ["gann0r"]

GHANA_TZ = pytz.timezone('Africa/Accra')

# ==================== DATABASE SETUP ====================
def init_db():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS payments
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  username TEXT,
                  amount REAL,
                  timestamp TEXT,
                  date TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS deposits
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  message_id INTEGER UNIQUE,
                  amount REAL,
                  approved_by TEXT,
                  status TEXT,
                  timestamp TEXT,
                  date TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS admins
                 (username TEXT PRIMARY KEY,
                  user_id INTEGER)''')
    
    for username in PRIVILEGED_USERS:
        c.execute("INSERT OR IGNORE INTO admins (username, user_id) VALUES (?, ?)", (username, None))
    
    conn.commit()
    conn.close()

def get_today_date():
    return datetime.now(GHANA_TZ).strftime('%Y-%m-%d')

def is_privileged_user(username):
    if not username:
        return False
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username FROM admins WHERE username = ?", (username.lower(),))
    result = c.fetchone()
    conn.close()
    return result is not None

def get_all_admin_usernames():
    """Get all admin usernames"""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username FROM admins")
    admins = [row[0] for row in c.fetchall()]
    conn.close()
    return admins

def store_user_id(username, user_id):
    """Store or update user ID for an admin"""
    if not username:
        return
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    try:
        c.execute("UPDATE admins SET user_id = ? WHERE username = ?", (user_id, username.lower()))
        if c.rowcount > 0:
            logging.info(f"✅ Stored User ID {user_id} for @{username}")
        conn.commit()
    except Exception as e:
        logging.error(f"Error storing user ID: {e}")
    finally:
        conn.close()

def get_admins_with_user_ids():
    """Get all admins who have user IDs stored"""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username, user_id FROM admins WHERE user_id IS NOT NULL")
    admins = c.fetchall()
    conn.close()
    return admins

def add_payment(username, amount):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    timestamp = datetime.now(GHANA_TZ).strftime('%Y-%m-%d %H:%M:%S')
    c.execute("INSERT INTO payments (username, amount, timestamp, date) VALUES (?, ?, ?, ?)",
              (username, amount, timestamp, today))
    conn.commit()
    conn.close()

def add_or_update_deposit(message_id, amount, approved_by, status):
    """Add or update deposit status"""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    timestamp = datetime.now(GHANA_TZ).strftime('%Y-%m-%d %H:%M:%S')
    
    # Check if exists
    c.execute("SELECT id, status FROM deposits WHERE message_id = ?", (message_id,))
    existing = c.fetchone()
    
    if existing:
        # Update existing
        c.execute("UPDATE deposits SET status = ?, approved_by = ?, timestamp = ? WHERE message_id = ?",
                  (status, approved_by, timestamp, message_id))
    else:
        # Insert new
        c.execute("INSERT INTO deposits (message_id, amount, approved_by, status, timestamp, date) VALUES (?, ?, ?, ?, ?, ?)",
                  (message_id, amount, approved_by, status, timestamp, today))
    
    conn.commit()
    conn.close()

def get_deposit_status(message_id):
    """Get deposit status and who approved it"""
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT status, approved_by FROM deposits WHERE message_id = ?", (message_id,))
    result = c.fetchone()
    conn.close()
    return result if result else (None, None)

def get_today_total_payments():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    c.execute("SELECT SUM(amount) FROM payments WHERE date = ?", (today,))
    result = c.fetchone()[0]
    conn.close()
    return result if result else 0.0

def get_today_total_deposits():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    c.execute("SELECT SUM(amount) FROM deposits WHERE date = ? AND status = 'approved'", (today,))
    result = c.fetchone()[0]
    conn.close()
    return result if result else 0.0

def get_all_transactions_today():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    c.execute("SELECT username, amount, timestamp, 'payment' as type, username as approved_by FROM payments WHERE date = ? ORDER BY timestamp DESC", (today,))
    payments = c.fetchall()
    c.execute("SELECT approved_by, amount, timestamp, 'deposit' as type, approved_by FROM deposits WHERE date = ? AND status = 'approved' ORDER BY timestamp DESC", (today,))
    deposits = c.fetchall()
    conn.close()
    all_transactions = payments + deposits
    all_transactions.sort(key=lambda x: x[2], reverse=True)
    return all_transactions

def get_user_statistics_today():
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    c.execute("""SELECT username, COUNT(*) as count, SUM(amount) as total 
                 FROM payments WHERE date = ? GROUP BY username ORDER BY total DESC""", (today,))
    payment_stats = c.fetchall()
    c.execute("""SELECT approved_by, COUNT(*) as count, SUM(amount) as total 
                 FROM deposits WHERE date = ? AND status = 'approved' GROUP BY approved_by ORDER BY total DESC""", (today,))
    deposit_stats = c.fetchall()
    conn.close()
    return payment_stats, deposit_stats

def delete_transaction_by_id(transaction_id, transaction_type):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    if transaction_type == 'payment':
        c.execute("DELETE FROM payments WHERE id = ?", (transaction_id,))
    elif transaction_type == 'deposit':
        c.execute("DELETE FROM deposits WHERE id = ?", (transaction_id,))
    deleted = c.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

def get_recent_transactions(limit=10):
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    today = get_today_date()
    c.execute("SELECT id, username, amount, timestamp, 'payment' as type FROM payments WHERE date = ? ORDER BY timestamp DESC LIMIT ?", (today, limit))
    payments = c.fetchall()
    c.execute("SELECT id, approved_by, amount, timestamp, 'deposit' as type FROM deposits WHERE date = ? AND status = 'approved' ORDER BY timestamp DESC LIMIT ?", (today, limit))
    deposits = c.fetchall()
    conn.close()
    all_transactions = payments + deposits
    all_transactions.sort(key=lambda x: x[3], reverse=True)
    return all_transactions[:limit]

# ==================== BOT HANDLERS ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Capture user ID if they're an admin
    username = update.effective_user.username
    user_id = update.effective_user.id
    
    if username and is_privileged_user(username):
        store_user_id(username, user_id)
        logging.info(f"Admin @{username} started bot - User ID captured: {user_id}")
    
    await update.message.reply_text(
        "👋 Hello! I'm Blackpills - Your Payment & Deposit Tracking Bot.\n\n"
        "Commands:\n"
        "/stats - View today's statistics\n"
        "/history - View recent transactions\n"
        "/userstats - View individual user statistics\n"
        "/delete <id> <type> - Delete transaction\n"
        "/addadmin <username> <passcode> - Add privileged user\n"
        "/removeadmin <username> - Remove privileged user\n"
        "/listadmins - List all privileged users\n"
        "/test - Test bot permissions"
    )

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    is_priv = is_privileged_user(username)
    
    # Store user ID if they're an admin
    if username and is_priv:
        store_user_id(username, user_id)
    
    message = (
        f"🧪 *Bot Test Results*\n\n"
        f"Your Username: @{username}\n"
        f"Your User ID: `{user_id}`\n"
        f"Current Chat ID: `{chat_id}`\n"
        f"Privileged User: {'✅ Yes' if is_priv else '❌ No'}\n\n"
        f"Payment Group ID: `{PAYMENT_GROUP_ID}`\n"
        f"Deposit Group ID: `{DEPOSIT_GROUP_ID}`\n\n"
        f"Match Status:\n"
        f"• Payment Group: {'✅' if chat_id == PAYMENT_GROUP_ID else '❌'}\n"
        f"• Deposit Group: {'✅' if chat_id == DEPOSIT_GROUP_ID else '❌'}"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Capture user ID if they're an admin
    username = update.effective_user.username
    user_id = update.effective_user.id
    if username and is_privileged_user(username):
        store_user_id(username, user_id)
    
    total_payments = get_today_total_payments()
    total_deposits = get_today_total_deposits()
    message = (
        f"📊 *Today's Statistics*\n\n"
        f"💰 Total Payments: GHS {total_payments:.2f}\n"
        f"📥 Total Deposits: GHS {total_deposits:.2f}\n\n"
        f"🔗 Full Dashboard: {DASHBOARD_URL}"
    )
    await update.message.reply_text(message, parse_mode='Markdown')

async def add_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    user_id = update.effective_user.id
    
    # Capture user ID
    if username and is_privileged_user(username):
        store_user_id(username, user_id)
    
    if not is_privileged_user(username):
        await update.message.reply_text("❌ You don't have permission to add admins.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /addadmin <username> <passcode>")
        return
    new_admin = context.args[0].replace('@', '').lower()
    passcode = context.args[1]
    if passcode != ADMIN_PASSCODE:
        await update.message.reply_text("❌ Incorrect passcode!")
        return
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO admins (username, user_id) VALUES (?, ?)", (new_admin, None))
        conn.commit()
        await update.message.reply_text(f"✅ @{new_admin} has been added as a privileged user.\n\n💡 They need to send /start to the bot in private chat to receive daily summaries.")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"⚠️ @{new_admin} is already a privileged user.")
    finally:
        conn.close()

async def remove_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    if not is_privileged_user(username):
        await update.message.reply_text("❌ You don't have permission to remove admins.")
        return
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /removeadmin <username>")
        return
    admin_to_remove = context.args[0].replace('@', '').lower()
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("DELETE FROM admins WHERE username = ?", (admin_to_remove,))
    if c.rowcount > 0:
        conn.commit()
        await update.message.reply_text(f"✅ @{admin_to_remove} has been removed.")
    else:
        await update.message.reply_text(f"⚠️ @{admin_to_remove} is not a privileged user.")
    conn.close()

async def list_admins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    if not is_privileged_user(username):
        await update.message.reply_text("❌ You don't have permission to view admins.")
        return
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username FROM admins")
    admins = c.fetchall()
    conn.close()
    if admins:
        admin_list = "\n".join([f"• @{admin[0]}" for admin in admins])
        await update.message.reply_text(f"👥 *Privileged Users:*\n\n{admin_list}", parse_mode='Markdown')
    else:
        await update.message.reply_text("No privileged users found.")

async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    if not is_privileged_user(username):
        await update.message.reply_text("❌ You don't have permission to view history.")
        return
    transactions = get_recent_transactions(limit=10)
    if not transactions:
        await update.message.reply_text("📋 No transactions today.")
        return
    message = "📋 *Recent Transactions (Today)*\n\n"
    for txn in transactions:
        txn_id, user, amount, timestamp, txn_type = txn
        time_str = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S').strftime('%I:%M %p')
        emoji = "💰" if txn_type == "payment" else "📥"
        message += f"{emoji} *{txn_type.upper()}* #{txn_id}\n"
        message += f"   User: @{user}\n"
        message += f"   Amount: GHS {amount:.2f}\n"
        message += f"   Time: {time_str}\n\n"
    message += f"🔗 Full Dashboard: {DASHBOARD_URL}"
    await update.message.reply_text(message, parse_mode='Markdown')

async def userstats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    if not is_privileged_user(username):
        await update.message.reply_text("❌ You don't have permission to view statistics.")
        return
    payment_stats, deposit_stats = get_user_statistics_today()
    message = "📊 *User Statistics (Today)*\n\n"
    if payment_stats:
        message += "💰 *PAYMENTS*\n"
        for user, count, total in payment_stats:
            message += f"• @{user}: {count} transactions, GHS {total:.2f}\n"
        message += "\n"
    if deposit_stats:
        message += "📥 *DEPOSITS APPROVED*\n"
        for user, count, total in deposit_stats:
            message += f"• @{user}: {count} approvals, GHS {total:.2f}\n"
    if not payment_stats and not deposit_stats:
        message += "No activity today."
    await update.message.reply_text(message, parse_mode='Markdown')

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.effective_user.username
    if not is_privileged_user(username):
        await update.message.reply_text("❌ You don't have permission to delete transactions.")
        return
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /delete <id> <type>\nExample: /delete 5 payment")
        return
    try:
        txn_id = int(context.args[0])
        txn_type = context.args[1].lower()
        if txn_type not in ['payment', 'deposit']:
            await update.message.reply_text("❌ Type must be 'payment' or 'deposit'")
            return
        deleted = delete_transaction_by_id(txn_id, txn_type)
        if deleted:
            await update.message.reply_text(
                f"✅ {txn_type.capitalize()} #{txn_id} deleted.\n\n"
                f"💰 Payment Total: GHS {get_today_total_payments():.2f}\n"
                f"📥 Deposit Total: GHS {get_today_total_deposits():.2f}"
            )
        else:
            await update.message.reply_text(f"❌ {txn_type.capitalize()} #{txn_id} not found.")
    except ValueError:
        await update.message.reply_text("❌ Invalid transaction ID.")

async def handle_group_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Combined handler for both payment and deposit groups"""
    chat_id = update.effective_chat.id
    
    logging.info(f"=== MESSAGE HANDLER TRIGGERED ===")
    logging.info(f"Chat ID: {chat_id}")
    logging.info(f"Payment Group ID: {PAYMENT_GROUP_ID}")
    logging.info(f"Deposit Group ID: {DEPOSIT_GROUP_ID}")
    
    # Get message text
    text = update.message.text if update.message.text else update.message.caption
    if not text:
        logging.info("No text found - ignoring")
        return
    
    logging.info(f"Message text: {text}")
    
    # Check for GHS amount pattern
    pattern = r'(?i)(?:ghs\s*([0-9]+\.?[0-9]*)|([0-9]+\.?[0-9]*)\s*ghs)'
    match = re.search(pattern, text)
    
    if not match:
        logging.info("No GHS pattern found - ignoring")
        return
    
    amount_str = match.group(1) if match.group(1) else match.group(2)
    amount = float(amount_str)
    logging.info(f"✅ Amount detected: GHS {amount}")
    
    # === PAYMENT GROUP ===
    if chat_id == PAYMENT_GROUP_ID:
        logging.info("📍 This is PAYMENT GROUP")
        username = update.effective_user.username
        logging.info(f"User: @{username}")
        
        # Only proceed if user is privileged
        if not is_privileged_user(username):
            logging.info(f"User @{username} not privileged - silent ignore")
            return
        
        logging.info(f"User @{username} IS privileged - logging payment")
        
        add_payment(username, amount)
        
        # Just send checkmark emoji
        await update.message.reply_text("✅")
        logging.info(f"✅ Payment logged: @{username} - GHS {amount:.2f}")
    
    # === DEPOSIT GROUP ===
    elif chat_id == DEPOSIT_GROUP_ID:
        logging.info("📍 This is DEPOSIT GROUP")
        
        # Create inline buttons
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{update.message.message_id}_{amount}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{update.message.message_id}_{amount}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        logging.info(f"Creating buttons for message ID: {update.message.message_id}")
        
        try:
            # Add buttons to the message
            sent_message = await update.message.reply_text(
                "Choose action:",
                reply_to_message_id=update.message.message_id,
                reply_markup=reply_markup
            )
            logging.info(f"✅✅✅ BUTTONS SENT SUCCESSFULLY! Message ID: {sent_message.message_id}")
        except Exception as e:
            logging.error(f"❌❌❌ FAILED TO SEND BUTTONS: {e}")
    
    else:
        logging.info(f"Message not from payment or deposit group - ignoring")

async def handle_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    username = update.effective_user.username
    
    logging.info(f"=== BUTTON CLICKED ===")
    logging.info(f"User: @{username}")
    logging.info(f"Callback data: {query.data}")
    
    # Check if user is privileged
    if not is_privileged_user(username):
        logging.warning(f"❌ User @{username} not authorized")
        await query.answer("❌ You are not authorized to perform this action.", show_alert=True)
        return
    
    logging.info(f"✅ User @{username} is authorized")
    
    # Parse callback data
    try:
        data = query.data
        action, message_id, amount = data.split('_')
        message_id = int(message_id)
        amount = float(amount)
        
        logging.info(f"Action: {action}, Message ID: {message_id}, Amount: {amount}")
    except Exception as e:
        logging.error(f"Error parsing callback data: {e}")
        await query.answer("❌ Error processing request", show_alert=True)
        return
    
    # Get current status
    current_status, approved_by = get_deposit_status(message_id)
    logging.info(f"Current status: {current_status}, Approved by: {approved_by}")
    
    if action == "approve":
        if current_status == "approved":
            logging.info(f"Already approved by @{approved_by}")
            await query.answer(f"❌ Already approved by @{approved_by}", show_alert=True)
            return
        
        # Approve deposit
        add_or_update_deposit(message_id, amount, username, "approved")
        
        # Update button
        keyboard = [
            [
                InlineKeyboardButton("✅ Approved", callback_data=f"approve_{message_id}_{amount}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{message_id}_{amount}")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer("✅ Deposit approved!")
        logging.info(f"✅ Deposit approved: GHS {amount} by @{username}")
        
    elif action == "reject":
        if current_status == "rejected":
            logging.info(f"Already rejected by @{approved_by}")
            await query.answer(f"❌ Already rejected by @{approved_by}", show_alert=True)
            return
        
        # Reject deposit (or remove if was approved)
        add_or_update_deposit(message_id, amount, username, "rejected")
        
        # Update button
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{message_id}_{amount}"),
                InlineKeyboardButton("❌ Rejected", callback_data=f"reject_{message_id}_{amount}")
            ]
        ]
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))
        await query.answer("❌ Deposit rejected!")
        logging.info(f"❌ Deposit rejected: GHS {amount} by @{username}")

async def send_daily_summary(application):
    """Send daily summary to privileged users via DM"""
    total_payments = get_today_total_payments()
    total_deposits = get_today_total_deposits()
    
    message = (
        f"📋 *Daily Summary - {get_today_date()}*\n\n"
        f"💰 Total Payments: GHS {total_payments:.2f}\n"
        f"📥 Total Deposits: GHS {total_deposits:.2f}\n\n"
        f"🔗 View Full Dashboard: {DASHBOARD_URL}"
    )
    
    # Get all admins with user IDs
    admins = get_admins_with_user_ids()
    
    logging.info(f"📤 Sending daily summary to {len(admins)} admins")
    
    sent_count = 0
    failed_count = 0
    
    # Send to each admin via DM
    for username, user_id in admins:
        try:
            await application.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode='Markdown'
            )
            logging.info(f"✅ Daily summary sent to @{username} (ID: {user_id})")
            sent_count += 1
        except Exception as e:
            logging.error(f"❌ Failed to send summary to @{username} (ID: {user_id}): {e}")
            failed_count += 1
    
    logging.info(f"📊 Daily summary complete: {sent_count} sent, {failed_count} failed")
    
    # Get admins without user IDs
    conn = sqlite3.connect('bot_data.db')
    c = conn.cursor()
    c.execute("SELECT username FROM admins WHERE user_id IS NULL")
    no_id_admins = c.fetchall()
    conn.close()
    
    if no_id_admins:
        logging.warning(f"⚠️ {len(no_id_admins)} admins haven't started bot yet: {[a[0] for a in no_id_admins]}")

# ==================== FLASK DASHBOARD ====================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Blackpills Dashboard</title>
    <meta name="view" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 15px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        h1 { color: #333; text-align: center; margin-bottom: 8px; font-size: 2.2em; }
        .date { text-align: center; color: #666; margin-bottom: 25px; font-size: 1.1em; }
        .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 25px; }
        .stat-card {
            padding: 25px;
            border-radius: 10px;
            color: white;
            text-align: center;
        }
        .stat-card.payments { background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%); }
        .stat-card.deposits { background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%); }
        .stat-label { font-size: 0.95em; opacity: 0.9; margin-bottom: 8px; }
        .stat-value { font-size: 2.3em; font-weight: bold; }
        .section { margin-top: 30px; }
        .section-title {
            font-size: 1.6em;
            color: #333;
            margin-bottom: 15px;
            border-bottom: 2px solid #667eea;
            padding-bottom: 8px;
        }
        .transaction-list {
            background: #f9f9f9;
            border-radius: 10px;
            padding: 15px;
            max-height: 400px;
            overflow-y: auto;
        }
        .transaction-item {
            background: white;
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 8px;
            border-left: 3px solid #667eea;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .transaction-item.payment { border-left-color: #f5576c; }
        .transaction-item.deposit { border-left-color: #00f2fe; }
        .transaction-info { flex: 1; }
        .transaction-type {
            font-weight: bold;
            color: #667eea;
            text-transform: uppercase;
            font-size: 0.8em;
        }
        .transaction-user { color: #666; margin: 4px 0; font-size: 0.95em; }
        .transaction-time { color: #999; font-size: 0.85em; }
        .transaction-amount { font-size: 1.4em; font-weight: bold; color: #333; }
        .user-stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .user-stat-box { background: #f9f9f9; padding: 15px; border-radius: 10px; }
        .user-stat-box h3 { color: #667eea; margin-bottom: 12px; font-size: 1.1em; }
        .user-stat-item {
            background: white;
            padding: 10px;
            margin-bottom: 6px;
            border-radius: 6px;
            display: flex;
            justify-content: space-between;
            font-size: 0.9em;
        }
        .user-name { font-weight: 600; color: #333; }
        .user-total { color: #667eea; font-weight: bold; }
        .refresh-info {
            text-align: center;
            color: #999;
            font-size: 0.85em;
            padding: 15px;
            background: #f5f5f5;
            border-radius: 8px;
            margin-top: 25px;
        }
        @media (max-width: 768px) {
            .stats-grid, .user-stats-grid { grid-template-columns: 1fr; }
            h1 { font-size: 1.8em; }
            .container { padding: 15px; }
        }
    </style>
    <script>
        setTimeout(function(){ location.reload(); }, 30000);
    </script>
</head>
<body>
    <div class="container">
        <h1>💊 Blackpills Dashboard</h1>
        <div class="date">{{ date }}</div>
        
        <div class="stats-grid">
            <div class="stat-card payments">
                <div class="stat-label">💰 Total Payments</div>
                <div class="stat-value">GHS {{ payments }}</div>
            </div>
            <div class="stat-card deposits">
                <div class="stat-label">📥 Total Deposits</div>
                <div class="stat-value">GHS {{ deposits }}</div>
            </div>
        </div>
        
        <div class="section">
            <h2 class="section-title">📋 Recent Transactions</h2>
            <div class="transaction-list">
                {% if transactions %}
                    {% for txn in transactions %}
                    <div class="transaction-item {{ txn[3] }}">
                        <div class="transaction-info">
                            <div class="transaction-type">{{ txn[3] }}</div>
                            <div class="transaction-user">@{{ txn[0] }}{% if txn[3] == 'deposit' %} (Approved by @{{ txn[4] }}){% endif %}</div>
                            <div class="transaction-time">{{ txn[2] }}</div>
                        </div>
                        <div class="transaction-amount">GHS {{ "%.2f"|format(txn[1]) }}</div>
                    </div>
                    {% endfor %}
                {% else %}
                    <p style="text-align: center; color: #999; padding: 20px;">No transactions yet today</p>
                {% endif %}
            </div>
        </div>
        
        <div class="section">
            <h2 class="section-title">📊 User Statistics</h2>
            <div class="user-stats-grid">
                <div class="user-stat-box">
                    <h3>💰 Payment Leaders</h3>
                    {% if payment_stats %}
                        {% for stat in payment_stats %}
                        <div class="user-stat-item">
                            <span class="user-name">@{{ stat[0] }}</span>
                            <span class="user-total">{{ stat[1] }}x | GHS {{ "%.2f"|format(stat[2]) }}</span>
                        </div>
                        {% endfor %}
                    {% else %}
                        <p style="text-align: center; color: #999; padding: 15px;">No payments yet</p>
                    {% endif %}
                </div>
                <div class="user-stat-box">
                    <h3>📥 Deposit Approvers</h3>
                    {% if deposit_stats %}
                        {% for stat in deposit_stats %}
                        <div class="user-stat-item">
                            <span class="user-name">@{{ stat[0] }}</span>
                            <span class="user-total">{{ stat[1] }}x | GHS {{ "%.2f"|format(stat[2]) }}</span>
                        </div>
                        {% endfor %}
                    {% else %}
                        <p style="text-align: center; color: #999; padding: 15px;">No deposits yet</p>
                    {% endif %}
                </div>
            </div>
        </div>
        
        <div class="refresh-info">
            🔄 Auto-refreshes every 30 seconds<br>
            Last updated: {{ time }}
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def dashboard():
    total_payments = get_today_total_payments()
    total_deposits = get_today_total_deposits()
    current_time = datetime.now(GHANA_TZ).strftime('%I:%M:%S %p')
    current_date = datetime.now(GHANA_TZ).strftime('%A, %B %d, %Y')
    
    transactions = get_all_transactions_today()
    formatted_transactions = []
    for txn in transactions:
        user, amount, timestamp, txn_type, approved_by = txn
        time_obj = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
        formatted_time = time_obj.strftime('%I:%M %p')
        formatted_transactions.append((user, amount, formatted_time, txn_type, approved_by))
    
    payment_stats, deposit_stats = get_user_statistics_today()
    
    return render_template_string(
        DASHBOARD_HTML,
        payments=f"{total_payments:.2f}",
        deposits=f"{total_deposits:.2f}",
        time=current_time,
        date=current_date,
        transactions=formatted_transactions,
        payment_stats=payment_stats,
        deposit_stats=deposit_stats
    )

@app.route('/api/stats')
def api_stats():
    """API endpoint that returns JSON data"""
    try:
        total_payments = get_today_total_payments()
        total_deposits = get_today_total_deposits()
        transactions = get_all_transactions_today()
        payment_stats, deposit_stats = get_user_statistics_today()
        current_date = datetime.now(GHANA_TZ).strftime('%A, %B %d, %Y')
        current_time = datetime.now(GHANA_TZ).strftime('%I:%M:%S %p')
        
        formatted_transactions = []
        for txn in transactions[:20]:
            user, amount, timestamp, txn_type, approved_by = txn
            time_obj = datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S')
            formatted_time = time_obj.strftime('%I:%M %p')
            formatted_transactions.append({
                'user': user,
                'amount': float(amount),
                'time': formatted_time,
                'type': txn_type,
                'approved_by': approved_by
            })
        
        return {
            'success': True,
            'date': current_date,
            'time': current_time,
            'totals': {
                'payments': float(total_payments),
                'deposits': float(total_deposits)
            },
            'transactions': formatted_transactions,
            'user_stats': {
                'payments': [[user, count, float(total)] for user, count, total in payment_stats],
                'deposits': [[user, count, float(total)] for user, count, total in deposit_stats]
            }
        }
    except Exception as e:
        return {'success': False, 'error': str(e)}, 500

# ==================== MAIN ====================
def run_telegram_bot():
    """Run Telegram bot in background"""
    application = Application.builder().token(BOT_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("test", test_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("userstats", userstats_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("addadmin", add_admin_command))
    application.add_handler(CommandHandler("removeadmin", remove_admin_command))
    application.add_handler(CommandHandler("listadmins", list_admins_command))
    application.add_handler(MessageHandler((filters.TEXT | filters.CAPTION) & ~filters.COMMAND, handle_group_messages))
    application.add_handler(CallbackQueryHandler(handle_button_callback))
    
    async def post_init_setup(app):
        scheduler = AsyncIOScheduler(timezone=GHANA_TZ)
        scheduler.add_job(send_daily_summary, trigger='cron', hour=20, minute=30, args=[app])
        scheduler.start()
        logging.info("Scheduler started")
    
    application.post_init = post_init_setup
    application.run_polling(allowed_updates=[Update.MESSAGE, Update.CALLBACK_QUERY, Update.EDITED_MESSAGE])

def main():
    """Main entry point - Flask runs as primary process"""
    init_db()
    
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    
    print("🤖 Starting Blackpills Bot...")
    
    # Start Telegram bot in background thread
    bot_thread = threading.Thread(target=run_telegram_bot, daemon=True)
    bot_thread.start()
    
    print("✅ Bot thread started")
    print("📊 Starting Flask dashboard...")
    
    # Run Flask as main process (this is what Choreo sees)
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

if __name__ == '__main__':
    main()
