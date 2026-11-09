"""Helpers for updating GTK controls without treating changes as user input."""


def set_active_silently(button, handler_id, active):
    button.handler_block(handler_id)
    try:
        button.set_active(active)
    finally:
        button.handler_unblock(handler_id)
