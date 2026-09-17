"""Seed test data for Phase 3 Workspace UI testing.

Creates:
- Test firm "Astra Test Firm"
- Test user (adviser)
- Test household
- Test thread for conversation testing

Run from backend: python -m seed.seed_phase3_test
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models.user import User, Firm
from app.models.household import Household, Person
from app.models.thread import Thread, ThreadKind
from app.models.enums import UserRole


async def seed_phase3():
    """Create test data for Phase 3 Workspace UI."""

    # Get database URL (use local SQLite for testing)
    db_url = "sqlite+aiosqlite:///./test_phase3.db"

    # Create engine and tables
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Check if test firm already exists
        stmt = select(Firm).where(Firm.name == "Astra Test Firm")
        result = await session.execute(stmt)
        firm = result.scalars().first()

        if not firm:
            print("Creating test firm...")
            firm = Firm(
                id=uuid.uuid4(),
                name="Astra Test Firm",
                domain="test.astra.local",
            )
            session.add(firm)
            await session.flush()
        else:
            print(f"Test firm exists: {firm.id}")

        # Check if test user exists
        stmt = select(User).where(User.email == "adviser@test.astra.local")
        result = await session.execute(stmt)
        user = result.scalars().first()

        if not user:
            print("Creating test user (adviser)...")
            user = User(
                id=uuid.uuid4(),
                firm_id=firm.id,
                email="adviser@test.astra.local",
                name="Test Adviser",
                role=UserRole.ADVISER,
                is_active=True,
            )
            session.add(user)
            await session.flush()
        else:
            print(f"Test user exists: {user.id}")

        # Check if test household exists
        stmt = select(Household).where(Household.name == "Test Household")
        result = await session.execute(stmt)
        household = result.scalars().first()

        if not household:
            print("Creating test household...")
            household = Household(
                id=uuid.uuid4(),
                firm_id=firm.id,
                name="Test Household",
                status="active",
            )
            session.add(household)
            await session.flush()

            # Add a primary contact
            person = Person(
                id=uuid.uuid4(),
                household_id=household.id,
                first_name="John",
                last_name="Doe",
                email="john@example.com",
                is_primary=True,
            )
            session.add(person)
            await session.flush()
        else:
            print(f"Test household exists: {household.id}")

        # Check if test thread exists
        stmt = select(Thread).where(Thread.title == "Phase 3 Test Thread")
        result = await session.execute(stmt)
        thread = result.scalars().first()

        if not thread:
            print("Creating test thread...")
            thread = Thread(
                id=uuid.uuid4(),
                firm_id=firm.id,
                kind=ThreadKind.ASK_ASTRA,
                created_by_user_id=user.id,
                title="Phase 3 Test Thread",
            )
            session.add(thread)
            await session.flush()
        else:
            print(f"Test thread exists: {thread.id}")

        # Commit all changes
        await session.commit()

        print("\n✅ Phase 3 test data seeded successfully!")
        print(f"\nTest credentials:")
        print(f"  Firm ID: {firm.id}")
        print(f"  Firm Name: {firm.name}")
        print(f"  User ID: {user.id}")
        print(f"  User Email: {user.email}")
        print(f"  User Role: {user.role.value}")
        print(f"  Household ID: {household.id}")
        print(f"  Household Name: {household.name}")
        print(f"  Thread ID: {thread.id}")
        print(f"  Thread Title: {thread.title}")
        print(f"\nStart a conversation by opening:")
        print(f"  http://localhost:3000/workspace/{thread.id}")


if __name__ == "__main__":
    asyncio.run(seed_phase3())
