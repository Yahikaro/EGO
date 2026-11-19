"""Deleting a chat in Core removes its messages and its dialog memory (and nothing of other chats)."""
import secrets
import uuid

import pytest

from server.ego_core.memory import ChatMemory
from server.ego_core.store import MainStore

KEY = secrets.token_bytes(32)


@pytest.fixture
def mstore(tmp_path):
    store = MainStore(tmp_path / 'main.db', KEY)
    yield store
    store.close()


def _chat_with_exchange(store, text):
    chat = store.create_chat(str(uuid.uuid4()), 'title')
    listing = store.list_chats()
    rows = listing['chats'] if isinstance(listing, dict) else listing
    version = [c for c in rows if c['id'] == chat['id']][0]['version']
    generation = store.begin_generation(str(uuid.uuid4()), chat['id'], text, version)
    store.set_generation_state(generation['id'], 'started')
    store.set_generation_state(generation['id'], 'completed', text='reply ' + text)
    return chat['id']


def test_delete_chat_removes_messages_and_memory_only_for_that_chat(mstore):
    memory = ChatMemory(mstore)
    gone, kept = _chat_with_exchange(mstore, 'one'), _chat_with_exchange(mstore, 'two')
    memory.add(gone, 'fact about the deleted chat', pinned=True)
    memory.add(kept, 'fact about the kept chat', pinned=False)
    mstore.delete_chat(gone)
    listing = mstore.list_chats()
    rows = listing['chats'] if isinstance(listing, dict) else listing
    assert [c['id'] for c in rows] == [kept]
    assert memory.view(kept)['items'][0]['text'] == 'fact about the kept chat'
    with pytest.raises(Exception):
        memory.view(gone)
