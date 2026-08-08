from decimal import Decimal
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from app.schemas.conversation import ConversationUpdate
from app.services.conversations import update_conversation


class ConversationTemperatureTests(IsolatedAsyncioTestCase):
    async def test_update_conversation_persists_temperature(self) -> None:
        conversation = SimpleNamespace(id=1, temperature=Decimal("0.70"), current_branch_id=None)
        session = AsyncMock()

        with patch("app.services.conversations.get_conversation", AsyncMock(return_value=conversation)):
            updated = await update_conversation(
                session=session,
                user_id=1,
                conversation_id=conversation.id,
                payload=ConversationUpdate(temperature=Decimal("0.9")),
            )

        self.assertIs(updated, conversation)
        self.assertEqual(conversation.temperature, Decimal("0.9"))
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once_with(conversation)
