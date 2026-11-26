"""Ask the database each question once while a page that only reads is put together (B-370).

A page that gathers thirty sections from thirty modules asks the same thing again and again: the firm's people, a household's mandates, a
person's guardian. The Licences & SEBI page sent 1,191 statements of which 139 were different. Each module is right to ask for what it needs,
so the answer is remembered here instead, for the length of one read on one session, and every module is unchanged.

Only a SELECT through the session is remembered, keyed by its SQL and its parameters. Anything else (a flush, an update, an insert, a
delete) forgets everything, and a session holding unflushed changes is never served from memory. Nothing outlives the block.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import loading


@asynccontextmanager
async def read_once(session: AsyncSession):
    sync = session.sync_session
    if sync.info.get("read_once"):          # already inside one: the outer block owns the memory
        yield
        return
    memory: dict = {}

    def remember(state):
        if not state.is_select:
            memory.clear()
            return None
        if state.is_relationship_load or state.is_column_load or sync.new or sync.dirty or sync.deleted \
                or state.execution_options.get("populate_existing") or getattr(state.statement, "_for_update_arg", None) is not None:
            return None
        # The key SQLAlchemy's own statement cache uses (as its caching example does): compiling the SQL to read it costs more than the query.
        shape = state.statement._generate_cache_key()
        if shape is None:
            return None
        key = (shape.key, repr([b.effective_value for b in shape.bindparams]), repr(state.parameters))
        frozen = memory.get(key)
        if frozen is None:
            frozen = memory[key] = state.invoke_statement().freeze()
        return loading.merge_frozen_result(sync, state.statement, frozen, load=False)()

    def forget(*_):
        memory.clear()

    event.listen(sync, "do_orm_execute", remember)
    event.listen(sync, "after_flush", forget)
    sync.info["read_once"] = True
    try:
        yield
    finally:
        sync.info.pop("read_once", None)
        event.remove(sync, "do_orm_execute", remember)
        event.remove(sync, "after_flush", forget)
