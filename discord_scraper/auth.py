"""Token acquisition: keyring storage with env fallback + browser capture."""

import os

import keyring
from keyring.errors import NoKeyringError, PasswordDeleteError

SERVICE = "discord-chat-scraper"
USERNAME = "user-token"
ENV_VAR = "DISCORD_TOKEN"


def store_token(token):
    keyring.set_password(SERVICE, USERNAME, token)


def load_token():
    """Return the stored token, or the DISCORD_TOKEN env var, or None."""
    try:
        token = keyring.get_password(SERVICE, USERNAME)
    except NoKeyringError:
        token = None
    if token:
        return token
    return os.environ.get(ENV_VAR)


def clear_token():
    try:
        keyring.delete_password(SERVICE, USERNAME)
    except (PasswordDeleteError, NoKeyringError):
        pass
