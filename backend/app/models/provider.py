from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class ProviderInstance(Base):
    __tablename__ = "provider_instances"
    __table_args__ = (UniqueConstraint("user_id", "display_name", name="uq_provider_instances_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    preset_id: Mapped[str] = mapped_column(String(50), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    default_adapter_id: Mapped[str] = mapped_column(String(80), nullable=False)
    default_model_id: Mapped[str | None] = mapped_column(String(150))
    base_url: Mapped[str | None] = mapped_column(String(500))
    credentials_encrypted_json: Mapped[str | None] = mapped_column(Text)
    credential_hint: Mapped[str | None] = mapped_column(String(16))
    settings_json: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_test_status: Mapped[str | None] = mapped_column(String(20))
    last_test_message: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), onupdate=func.current_timestamp(), nullable=False)

    models = relationship("ProviderModel", back_populates="provider_instance", cascade="all, delete-orphan")


class ProviderModel(Base):
    __tablename__ = "provider_models"
    __table_args__ = (UniqueConstraint("provider_instance_id", "model_id", name="uq_provider_models_instance_model"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    provider_instance_id: Mapped[int] = mapped_column(ForeignKey("provider_instances.id", ondelete="CASCADE"), nullable=False, index=True)
    model_id: Mapped[str] = mapped_column(String(150), nullable=False)
    remote_display_name: Mapped[str | None] = mapped_column(String(255))
    display_name_override: Mapped[str | None] = mapped_column(String(255))
    adapter_override: Mapped[str | None] = mapped_column(String(80))
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    remote_available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    metadata_json: Mapped[str | None] = mapped_column(Text)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    provider_instance = relationship("ProviderInstance", back_populates="models")
