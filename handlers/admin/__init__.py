# START OF FILE handlers/admin/__init__.py
from telegram.ext import (
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import (
    dashboard,
    user_management,
    country_management,
    financials,
    messaging,
    settings,
    file_manager,
    session_vault,
    system,
)
from ..filters import admin_filter

def get_admin_handlers():
    """Aggregates and returns all admin-related handlers."""

    # Combine all conversation handlers from different modules
    all_conv_handlers_raw = [
        user_management.get_conv_handler(),
        country_management.get_conv_handler(),
        financials.get_conv_handler(), # Added missing handler
        messaging.get_conv_handler(),
        settings.get_conv_handler(),
        file_manager.get_conv_handler(),
        system.get_conv_handler(),
    ]
    
    # FIX: Filter out any 'None' results from get_conv_handler() functions
    all_conv_handlers = [h for h in all_conv_handlers_raw if h is not None]

    # Combine all callback query handlers
    all_callback_handlers = [
        CallbackQueryHandler(dashboard.admin_panel, pattern=r"^admin_panel$"),
        CallbackQueryHandler(dashboard.stats_panel, pattern=r"^admin_stats$"),
        *user_management.get_callback_handlers(),
        *country_management.get_callback_handlers(),
        *financials.get_callback_handlers(),
        *messaging.get_callback_handlers(),
        *settings.get_callback_handlers(),
        *file_manager.get_callback_handlers(),
        *session_vault.get_callback_handlers(),
        *system.get_callback_handlers(),
    ]

    return [
        CommandHandler("admin", dashboard.admin_panel, filters=admin_filter),
        *all_conv_handlers,
        *all_callback_handlers,
    ]

# END OF FILE handlers/admin/__init__.py