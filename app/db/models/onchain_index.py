"""On-chain index models — indexed $U transfers and agent NFT events.

The indexer worker scans BSC via Alchemy RPC and populates these tables.
Queries against these tables are local (no RPC) and support marketplace
analytics: hire trends, wallet activity, agent trading history.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_ADDRESS_LEN: int = 42
_TX_HASH_LEN: int = 66


class TransferType(str, enum.Enum):
    """Type of on-chain transfer."""

    ERC20_U = "erc20_u"
    ERC721_AGENT = "erc721_agent"


TRANSFER_TYPE_ENUM_NAME: str = "transfer_type"


class OnchainTransfer(Base):
    """A single $U (ERC-20) or agent NFT (ERC-721) transfer indexed from BSC.

    Queried by the on-chain stats API for hire verification and analytics.
    The `linked_agent_id` is resolved by the indexer when the `to_address`
    matches a known agent wallet in `agent_cache`.
    """

    __tablename__ = "onchain_transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ---- Transfer parties ------------------------------------------------
    from_address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), nullable=False)
    to_address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), nullable=False)

    # ---- Value -----------------------------------------------------------
    #: $U amount (18 decimals) for ERC-20, or token_id for ERC-721.
    value: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

    # ---- Chain metadata --------------------------------------------------
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(_TX_HASH_LEN), nullable=False)

    # ---- Classification --------------------------------------------------
    transfer_type: Mapped[TransferType] = mapped_column(
        SAEnum(
            TransferType,
            name=TRANSFER_TYPE_ENUM_NAME,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        server_default=text("'erc20_u'"),
    )

    # ---- Agent linkage (nullable) ----------------------------------------
    #: Resolved to agent_cache.agent_id when the to_address matches an
    #: agent wallet. NULL for transfers to unknown wallets.
    linked_agent_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        # Prevent duplicate indexing of the same tx
        UniqueConstraint("tx_hash", "from_address", "to_address", name="uq_transfer_tx"),
        # Fast lookups by agent
        Index("ix_onchain_transfers_agent", "linked_agent_id"),
        # Fast lookups by block range
        Index("ix_onchain_transfers_block", "block_number"),
        # Fast lookups by wallet
        Index("ix_onchain_transfers_from", "from_address"),
        Index("ix_onchain_transfers_to", "to_address"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"OnchainTransfer(block={self.block_number}, value={self.value})"


class OnchainAgentEvent(Base):
    """Agent NFT lifecycle event (mint, transfer/trade) indexed from BSC.

    Tracks the full history of each agent NFT: who created it, who bought it,
    and when. Used for marketplace trust signals and analytics.
    """

    __tablename__ = "onchain_agent_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # ---- Agent identification --------------------------------------------
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # ---- Event details ---------------------------------------------------
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)  # 'mint' | 'transfer'
    from_address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), nullable=False)
    to_address: Mapped[str] = mapped_column(String(_ADDRESS_LEN), nullable=False)

    # ---- Chain metadata --------------------------------------------------
    block_number: Mapped[int] = mapped_column(BigInteger, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(_TX_HASH_LEN), nullable=False)

    __table_args__ = (
        Index("ix_onchain_agent_events_agent", "agent_id"),
        Index("ix_onchain_agent_events_block", "block_number"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"OnchainAgentEvent(agent={self.agent_id}, type={self.event_type})"
