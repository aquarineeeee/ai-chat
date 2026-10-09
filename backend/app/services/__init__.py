"""Public service helpers, imported lazily to avoid provider/service cycles."""

from importlib import import_module

_EXPORTS = {
    "authenticate_user": "app.services.auth",
    "export_conversation": "app.services.conversation_export",
    "activate_conversation_branch": "app.services.branches",
    "create_conversation_branch": "app.services.branches",
    "delete_conversation_branch": "app.services.branches",
    "list_conversation_branches": "app.services.branches",
    "update_conversation_branch": "app.services.branches",
    "create_conversation": "app.services.conversations",
    "delete_conversation": "app.services.conversations",
    "get_conversation": "app.services.conversations",
    "import_markdown_conversation": "app.services.conversations",
    "list_conversations": "app.services.conversations",
    "update_conversation": "app.services.conversations",
    "create_message_pair": "app.services.messages",
    "list_conversation_messages": "app.services.messages",
}

__all__ = list(_EXPORTS)


def __getattr__(name: str):
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(name)
    value = getattr(import_module(module), name)
    globals()[name] = value
    return value
