"""Baseline schema: every table and constraint in PRD sections 10, 19.7, 20.2 and 21.8.

Generated from app.models with alembic autogenerate, then reviewed by hand.
DATA-01 runs upgrade -> downgrade -> upgrade on a seeded database.

Revision ID: 0001
Revises:
Create Date: 2026-10-09 03:45:41.720563
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS citext')
    op.execute('CREATE EXTENSION IF NOT EXISTS pg_trgm')
    op.create_table('otp_codes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('destination_hash', sa.Text(), nullable=False),
    sa.Column('code_hash', sa.Text(), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("channel IN ('email', 'phone')", name=op.f('ck_otp_codes_channel_enum')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_otp_codes_attempts_nonnegative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_otp_codes'))
    )
    op.create_index(op.f('ix_otp_codes_channel_destination_hash_created_at'), 'otp_codes', ['channel', 'destination_hash', 'created_at'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=True),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('phone_e164', sa.Text(), nullable=True),
    sa.Column('phone_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('avatar_url', sa.Text(), nullable=True),
    sa.Column('theme', sa.Text(), server_default=sa.text("'system'"), nullable=False),
    sa.Column('notify_email', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('timezone', sa.Text(), server_default=sa.text("'America/New_York'"), nullable=False),
    sa.Column('share_by_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('notify_kitchen_digest', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('quiet_start', sa.Time(), server_default=sa.text("'22:00'"), nullable=True),
    sa.Column('quiet_end', sa.Time(), server_default=sa.text("'08:00'"), nullable=True),
    sa.Column('private_lock_screen', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('prompt_snoozed_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("phone_e164 IS NULL OR phone_e164 ~ '^\\+[1-9][0-9]{6,14}$'", name=op.f('ck_users_phone_e164_format')),
    sa.CheckConstraint("theme IN ('system', 'light', 'dark')", name=op.f('ck_users_theme_enum')),
    sa.CheckConstraint('char_length(display_name) BETWEEN 1 AND 60', name=op.f('ck_users_display_name_length')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name=op.f('uq_users_email')),
    sa.UniqueConstraint('phone_e164', name=op.f('uq_users_phone_e164'))
    )
    op.create_table('auth_identities',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('provider', sa.Text(), nullable=False),
    sa.Column('provider_subject', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("provider IN ('google', 'email', 'phone')", name=op.f('ck_auth_identities_provider_enum')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_identities_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_identities')),
    sa.UniqueConstraint('provider', 'provider_subject', name=op.f('uq_auth_identities_provider_provider_subject'))
    )
    op.create_index(op.f('ix_auth_identities_user_id'), 'auth_identities', ['user_id'], unique=False)
    op.create_table('houses',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('invite_token_hash', sa.Text(), nullable=False),
    sa.Column('invite_rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('timezone', sa.Text(), server_default=sa.text("'America/New_York'"), nullable=False),
    sa.Column('event_seq', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('archived_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 40', name=op.f('ck_houses_name_length')),
    sa.CheckConstraint('event_seq >= 0', name=op.f('ck_houses_event_seq_nonnegative')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_houses_created_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_houses')),
    sa.UniqueConstraint('invite_token_hash', name=op.f('uq_houses_invite_token_hash'))
    )
    op.create_table('idempotency_keys',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('request_hash', sa.Text(), nullable=False),
    sa.Column('status_code', sa.Integer(), nullable=True),
    sa.Column('response_body', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_idempotency_keys_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'key', name=op.f('pk_idempotency_keys'))
    )
    op.create_index(op.f('ix_idempotency_keys_created_at'), 'idempotency_keys', ['created_at'], unique=False)
    op.create_table('notification_prefs',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.CheckConstraint("channel IN ('push', 'email')", name=op.f('ck_notification_prefs_channel_enum')),
    sa.CheckConstraint("type IN ('asks', 'answers', 'share', 'tab', 'pay', 'remind', 'soon', 'settled', 'joined', 'digest')", name=op.f('ck_notification_prefs_type_enum')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_prefs_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'type', 'channel', name=op.f('pk_notification_prefs'))
    )
    op.create_table('payment_handles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('method', sa.Text(), nullable=False),
    sa.Column('handle', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("method IN ('venmo', 'cashapp', 'paypal', 'zelle')", name=op.f('ck_payment_handles_method_enum')),
    sa.CheckConstraint('char_length(handle) BETWEEN 1 AND 120', name=op.f('ck_payment_handles_handle_length')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_payment_handles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_handles')),
    sa.UniqueConstraint('user_id', 'method', name=op.f('uq_payment_handles_user_id_method'))
    )
    op.create_table('push_subscriptions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('endpoint', sa.Text(), nullable=False),
    sa.Column('p256dh', sa.Text(), nullable=False),
    sa.Column('auth', sa.Text(), nullable=False),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('device_label', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_success_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_count', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('stale_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint('failure_count >= 0', name=op.f('ck_push_subscriptions_failure_count_nonnegative')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_push_subscriptions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_push_subscriptions')),
    sa.UniqueConstraint('endpoint', name=op.f('uq_push_subscriptions_endpoint'))
    )
    op.create_index(op.f('ix_push_subscriptions_user_id'), 'push_subscriptions', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('user_agent', sa.Text(), nullable=True),
    sa.Column('ip_hash', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('members',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('color', sa.Text(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('role', sa.Text(), server_default=sa.text("'member'"), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'placeholder'"), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('anonymized_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status <> 'placeholder' OR user_id IS NULL) AND (status <> 'active' OR user_id IS NOT NULL)", name=op.f('ck_members_status_matches_user')),
    sa.CheckConstraint("role IN ('admin', 'member')", name=op.f('ck_members_role_enum')),
    sa.CheckConstraint("status IN ('placeholder', 'active', 'left', 'removed')", name=op.f('ck_members_status_enum')),
    sa.CheckConstraint('char_length(display_name) BETWEEN 1 AND 24', name=op.f('ck_members_display_name_length')),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_members_house_id_houses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_members_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_members')),
    sa.UniqueConstraint('house_id', 'user_id', name=op.f('uq_members_house_id_user_id'))
    )
    op.create_index('uq_members_house_id_lower_display_name', 'members', ['house_id', sa.literal_column('lower(display_name)')], unique=True, postgresql_where=sa.text("status <> 'removed' AND anonymized_at IS NULL"))
    op.create_index('uq_members_one_admin_per_house', 'members', ['house_id'], unique=True, postgresql_where=sa.text("role = 'admin' AND status = 'active'"))
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=True),
    sa.Column('event_seq', sa.BigInteger(), nullable=True),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('channel', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('dedupe_key', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("channel IN ('inapp', 'push', 'email')", name=op.f('ck_notifications_channel_enum')),
    sa.CheckConstraint("status IN ('pending', 'held', 'sent', 'failed', 'suppressed')", name=op.f('ck_notifications_status_enum')),
    sa.CheckConstraint("type IN ('asks', 'answers', 'share', 'tab', 'pay', 'remind', 'soon', 'settled', 'joined', 'digest')", name=op.f('ck_notifications_type_enum')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_notifications_attempts_nonnegative')),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_notifications_house_id_houses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('dedupe_key', name=op.f('uq_notifications_dedupe_key'))
    )
    op.create_index(op.f('ix_notifications_status_next_attempt_at'), 'notifications', ['status', 'next_attempt_at'], unique=False)
    op.create_index(op.f('ix_notifications_user_id_read_at'), 'notifications', ['user_id', 'read_at'], unique=False)
    op.create_table('bills',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('payer_member_id', sa.UUID(), nullable=False),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('store_name', sa.Text(), nullable=True),
    sa.Column('purchase_date', sa.Date(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), server_default=sa.text("'USD'"), nullable=False),
    sa.Column('subtotal_cents', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('tax_cents', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('total_cents', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('printed_total_cents', sa.BigInteger(), nullable=True),
    sa.Column('reconcile_override_reason', sa.Text(), nullable=True),
    sa.Column('state', sa.Text(), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('extraction_status', sa.Text(), server_default=sa.text("'none'"), nullable=False),
    sa.Column('link_token_hash', sa.Text(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('settled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('settled_by', sa.UUID(), nullable=True),
    sa.Column('void_reason', sa.Text(), nullable=True),
    sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name=op.f('ck_bills_currency_iso')),
    sa.CheckConstraint("extraction_status IN ('none', 'pending', 'running', 'done', 'failed')", name=op.f('ck_bills_extraction_status_enum')),
    sa.CheckConstraint("state <> 'voided' OR coalesce(char_length(void_reason), 0) BETWEEN 3 AND 200", name=op.f('ck_bills_void_has_reason')),
    sa.CheckConstraint("state = 'draft' OR sent_at IS NOT NULL", name=op.f('ck_bills_sent_bills_have_sent_at')),
    sa.CheckConstraint("state IN ('draft', 'open', 'settled', 'voided')", name=op.f('ck_bills_state_enum')),
    sa.CheckConstraint('printed_total_cents IS NULL OR (printed_total_cents = total_cents AND reconcile_override_reason IS NOT NULL)', name=op.f('ck_bills_override_has_reason')),
    sa.CheckConstraint('tax_cents >= 0', name=op.f('ck_bills_tax_nonnegative')),
    sa.CheckConstraint('total_cents BETWEEN 0 AND 10000000', name=op.f('ck_bills_total_cents_range')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_bills_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_bills_house_id_houses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['payer_member_id'], ['members.id'], name=op.f('fk_bills_payer_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['settled_by'], ['members.id'], name=op.f('fk_bills_settled_by_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bills')),
    sa.UniqueConstraint('link_token_hash', name=op.f('uq_bills_link_token_hash'))
    )
    op.create_index('ix_bills_house_id_state_purchase_date', 'bills', ['house_id', 'state', sa.literal_column('purchase_date DESC')], unique=False)
    op.create_index('ix_bills_store_name_trgm', 'bills', ['store_name'], unique=False, postgresql_using='gin', postgresql_ops={'store_name': 'gin_trgm_ops'})
    op.create_table('house_events',
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('aggregate_type', sa.Text(), nullable=False),
    sa.Column('aggregate_id', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=True),
    sa.Column('actor_member_id', sa.UUID(), nullable=True),
    sa.Column('client_mutation_id', sa.Text(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('seq > 0', name=op.f('ck_house_events_seq_positive')),
    sa.ForeignKeyConstraint(['actor_member_id'], ['members.id'], name=op.f('fk_house_events_actor_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_house_events_house_id_houses'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('house_id', 'seq', name=op.f('pk_house_events'))
    )
    op.create_index(op.f('ix_house_events_created_at'), 'house_events', ['created_at'], unique=False)
    op.create_table('activity_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=True),
    sa.Column('actor_user_id', sa.UUID(), nullable=True),
    sa.Column('actor_member_id', sa.UUID(), nullable=True),
    sa.Column('type', sa.Text(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('house_event_seq', sa.BigInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_member_id'], ['members.id'], name=op.f('fk_activity_events_actor_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['actor_user_id'], ['users.id'], name=op.f('fk_activity_events_actor_user_id_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_activity_events_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_activity_events_house_id_houses'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_activity_events'))
    )
    op.create_index('ix_activity_events_bill_id_created_at', 'activity_events', ['bill_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_activity_events_house_id_created_at', 'activity_events', ['house_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('bill_exclusions',
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_bill_exclusions_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_bill_exclusions_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('bill_id', 'member_id', name=op.f('pk_bill_exclusions'))
    )
    op.create_table('bill_fees',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('label', sa.Text(), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('position', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("kind = 'adjustment' OR amount_cents >= 0", name=op.f('ck_bill_fees_amount_sign')),
    sa.CheckConstraint("kind IN ('fee', 'tip', 'order_discount', 'adjustment')", name=op.f('ck_bill_fees_kind_enum')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_bill_fees_bill_id_bills'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bill_fees'))
    )
    op.create_index(op.f('ix_bill_fees_bill_id'), 'bill_fees', ['bill_id'], unique=False)
    op.create_index('uq_bill_fees_one_adjustment_per_bill', 'bill_fees', ['bill_id'], unique=True, postgresql_where=sa.text("kind = 'adjustment'"))
    op.create_table('guests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.Text(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('link_token_hash', sa.Text(), nullable=False),
    sa.Column('sort_order', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('char_length(display_name) BETWEEN 1 AND 24', name=op.f('ck_guests_display_name_length')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_guests_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_guests_user_id_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_guests')),
    sa.UniqueConstraint('link_token_hash', name=op.f('uq_guests_link_token_hash'))
    )
    op.create_index(op.f('ix_guests_bill_id'), 'guests', ['bill_id'], unique=False)
    op.create_table('items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=10, scale=3), server_default=sa.text('1'), nullable=False),
    sa.Column('unit_price_cents', sa.BigInteger(), nullable=True),
    sa.Column('discount_cents', sa.BigInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('line_total_cents', sa.BigInteger(), nullable=False),
    sa.Column('taxable', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('confidence', sa.Numeric(precision=3, scale=2), nullable=True),
    sa.Column('flagged', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('storage', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('icon_key', sa.Text(), nullable=True),
    sa.Column('shelf_life_days', sa.Integer(), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("category IS NULL OR category IN ('produce', 'dairy', 'eggs', 'meat_fish', 'plant_protein', 'drinks', 'frozen', 'grains', 'bakery', 'snacks', 'condiments', 'canned', 'coffee_tea', 'prepared', 'household', 'other')", name=op.f('ck_items_category_enum')),
    sa.CheckConstraint("storage IN ('fridge', 'freezer', 'pantry', 'none')", name=op.f('ck_items_storage_enum')),
    sa.CheckConstraint('char_length(name) BETWEEN 1 AND 120', name=op.f('ck_items_name_length')),
    sa.CheckConstraint('confidence IS NULL OR confidence BETWEEN 0 AND 1', name=op.f('ck_items_confidence_range')),
    sa.CheckConstraint('discount_cents >= 0', name=op.f('ck_items_discount_nonnegative')),
    sa.CheckConstraint('quantity > 0', name=op.f('ck_items_quantity_positive')),
    sa.CheckConstraint('shelf_life_days IS NULL OR shelf_life_days > 0', name=op.f('ck_items_shelf_life_positive')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_items_bill_id_bills'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_items'))
    )
    op.create_index('ix_items_bill_id_position', 'items', ['bill_id', 'position'], unique=False)
    op.create_index('ix_items_name_trgm', 'items', ['name'], unique=False, postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.create_table('outbox',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('seq', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'processing', 'done', 'failed')", name=op.f('ck_outbox_status_enum')),
    sa.ForeignKeyConstraint(['house_id', 'seq'], ['house_events.house_id', 'house_events.seq'], name=op.f('fk_outbox_house_id_house_events'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox'))
    )
    op.create_index(op.f('ix_outbox_status_next_attempt_at'), 'outbox', ['status', 'next_attempt_at'], unique=False)
    op.create_table('receipt_extractions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('model', sa.Text(), nullable=False),
    sa.Column('prompt_version', sa.Text(), nullable=False),
    sa.Column('raw_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('cost_usd', sa.Numeric(precision=12, scale=6), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name=op.f('ck_receipt_extractions_status_enum')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_receipt_extractions_bill_id_bills'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_receipt_extractions'))
    )
    op.create_index(op.f('ix_receipt_extractions_bill_id'), 'receipt_extractions', ['bill_id'], unique=False)
    op.create_table('receipts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('storage_key', sa.Text(), nullable=False),
    sa.Column('mime', sa.Text(), nullable=False),
    sa.Column('bytes', sa.Integer(), nullable=False),
    sa.Column('page_index', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('uploaded_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("mime IN ('image/jpeg', 'image/png', 'image/heic', 'image/webp', 'application/pdf')", name=op.f('ck_receipts_mime_enum')),
    sa.CheckConstraint('bytes > 0 AND bytes <= 15728640', name=op.f('ck_receipts_bytes_max_15mb')),
    sa.CheckConstraint('page_index >= 0', name=op.f('ck_receipts_page_index_nonnegative')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_receipts_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['uploaded_by'], ['users.id'], name=op.f('fk_receipts_uploaded_by_users'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_receipts')),
    sa.UniqueConstraint('storage_key', name=op.f('uq_receipts_storage_key'))
    )
    op.create_index(op.f('ix_receipts_bill_id'), 'receipts', ['bill_id'], unique=False)
    op.create_table('seen_markers',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('last_seen_version', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_seen_markers_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_seen_markers_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'bill_id', name=op.f('pk_seen_markers'))
    )
    op.create_table('allocations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('item_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=True),
    sa.Column('guest_id', sa.UUID(), nullable=True),
    sa.Column('weight', sa.Numeric(precision=6, scale=3), server_default=sa.text('1'), nullable=False),
    sa.Column('quantity', sa.Numeric(precision=10, scale=3), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by_member_id', sa.UUID(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('num_nonnulls(member_id, guest_id) = 1', name=op.f('ck_allocations_exactly_one_participant')),
    sa.CheckConstraint('quantity IS NULL OR quantity > 0', name=op.f('ck_allocations_quantity_positive')),
    sa.CheckConstraint('weight > 0', name=op.f('ck_allocations_weight_positive')),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], name=op.f('fk_allocations_created_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['guest_id'], ['guests.id'], name=op.f('fk_allocations_guest_id_guests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_allocations_item_id_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_allocations_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by_member_id'], ['members.id'], name=op.f('fk_allocations_updated_by_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_allocations'))
    )
    op.create_index('ix_allocations_item_id', 'allocations', ['item_id'], unique=False)
    op.create_index('uq_allocations_item_id_guest_id', 'allocations', ['item_id', 'guest_id'], unique=True, postgresql_where=sa.text('guest_id IS NOT NULL'))
    op.create_index('uq_allocations_item_id_member_id', 'allocations', ['item_id', 'member_id'], unique=True, postgresql_where=sa.text('member_id IS NOT NULL'))
    op.create_table('inventory_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=True),
    sa.Column('item_id', sa.UUID(), nullable=True),
    sa.Column('place', sa.Text(), nullable=False),
    sa.Column('category', sa.Text(), nullable=True),
    sa.Column('icon_key', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('short_name', sa.Text(), nullable=False),
    sa.Column('quantity_text', sa.Text(), nullable=True),
    sa.Column('price_cents', sa.BigInteger(), nullable=True),
    sa.Column('stocked_on', sa.Date(), nullable=False),
    sa.Column('shelf_life_days', sa.Integer(), nullable=False),
    sa.Column('snoozed_until', sa.Date(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'in_stock'"), nullable=False),
    sa.Column('used_up_by_member_id', sa.UUID(), nullable=True),
    sa.Column('used_up_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("category IS NULL OR category IN ('produce', 'dairy', 'eggs', 'meat_fish', 'plant_protein', 'drinks', 'frozen', 'grains', 'bakery', 'snacks', 'condiments', 'canned', 'coffee_tea', 'prepared', 'household', 'other')", name=op.f('ck_inventory_items_category_enum')),
    sa.CheckConstraint("place IN ('fridge', 'freezer', 'pantry')", name=op.f('ck_inventory_items_place_enum')),
    sa.CheckConstraint("status <> 'used_up' OR (used_up_at IS NOT NULL AND used_up_by_member_id IS NOT NULL)", name=op.f('ck_inventory_items_used_up_stamped')),
    sa.CheckConstraint("status IN ('in_stock', 'used_up', 'removed')", name=op.f('ck_inventory_items_status_enum')),
    sa.CheckConstraint('(bill_id IS NULL) = (item_id IS NULL)', name=op.f('ck_inventory_items_bill_and_item_together')),
    sa.CheckConstraint('char_length(short_name) BETWEEN 1 AND 14', name=op.f('ck_inventory_items_short_name_length')),
    sa.CheckConstraint('shelf_life_days > 0', name=op.f('ck_inventory_items_shelf_life_positive')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_inventory_items_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by'], ['members.id'], name=op.f('fk_inventory_items_created_by_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_inventory_items_house_id_houses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_inventory_items_item_id_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['used_up_by_member_id'], ['members.id'], name=op.f('fk_inventory_items_used_up_by_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inventory_items'))
    )
    op.create_index('ix_inventory_items_house_id_status_place', 'inventory_items', ['house_id', 'status', 'place'], unique=False)
    op.create_index('uq_inventory_items_item_id', 'inventory_items', ['item_id'], unique=True, postgresql_where=sa.text('item_id IS NOT NULL'))
    op.create_table('payments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=True),
    sa.Column('guest_id', sa.UUID(), nullable=True),
    sa.Column('to_member_id', sa.UUID(), nullable=False),
    sa.Column('direction', sa.Text(), server_default=sa.text("'to_payer'"), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('method', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('reject_note', sa.Text(), nullable=True),
    sa.Column('recorded_by', sa.UUID(), nullable=False),
    sa.Column('confirmed_by', sa.UUID(), nullable=True),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("direction IN ('to_payer', 'refund')", name=op.f('ck_payments_direction_enum')),
    sa.CheckConstraint("method IN ('venmo', 'cashapp', 'paypal', 'zelle', 'cash', 'other')", name=op.f('ck_payments_method_enum')),
    sa.CheckConstraint("status IN ('pending_confirmation', 'confirmed', 'rejected')", name=op.f('ck_payments_status_enum')),
    sa.CheckConstraint('amount_cents BETWEEN 1 AND 10000000', name=op.f('ck_payments_amount_range')),
    sa.CheckConstraint('note IS NULL OR char_length(note) <= 280', name=op.f('ck_payments_note_length')),
    sa.CheckConstraint('num_nonnulls(member_id, guest_id) = 1', name=op.f('ck_payments_exactly_one_participant')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_payments_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['confirmed_by'], ['users.id'], name=op.f('fk_payments_confirmed_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['guest_id'], ['guests.id'], name=op.f('fk_payments_guest_id_guests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_payments_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recorded_by'], ['users.id'], name=op.f('fk_payments_recorded_by_users'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['to_member_id'], ['members.id'], name=op.f('fk_payments_to_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payments'))
    )
    op.create_index('ix_payments_bill_id', 'payments', ['bill_id'], unique=False)
    op.create_table('share_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=True),
    sa.Column('guest_id', sa.UUID(), nullable=True),
    sa.Column('share_cents', sa.BigInteger(), nullable=False),
    sa.Column('bill_version', sa.Integer(), nullable=False),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.CheckConstraint('num_nonnulls(member_id, guest_id) = 1', name=op.f('ck_share_snapshots_exactly_one_participant')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_share_snapshots_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['guest_id'], ['guests.id'], name=op.f('fk_share_snapshots_guest_id_guests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_share_snapshots_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_share_snapshots'))
    )
    op.create_index(op.f('ix_share_snapshots_bill_id_computed_at'), 'share_snapshots', ['bill_id', 'computed_at'], unique=False)
    op.create_table('inventory_owners',
    sa.Column('inventory_item_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=False),
    sa.Column('open_to_share', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('updated_by_member_id', sa.UUID(), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], name=op.f('fk_inventory_owners_inventory_item_id_inventory_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_inventory_owners_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['updated_by_member_id'], ['members.id'], name=op.f('fk_inventory_owners_updated_by_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('inventory_item_id', 'member_id', name=op.f('pk_inventory_owners'))
    )
    op.create_index(op.f('ix_inventory_owners_member_id'), 'inventory_owners', ['member_id'], unique=False)
    op.create_table('share_requests',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('inventory_item_id', sa.UUID(), nullable=False),
    sa.Column('requester_member_id', sa.UUID(), nullable=False),
    sa.Column('amount', sa.Text(), nullable=False),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('resolution', sa.Text(), nullable=True),
    sa.Column('chip_in_cents', sa.BigInteger(), nullable=True),
    sa.Column('answered_by_member_id', sa.UUID(), nullable=True),
    sa.Column('answered_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('undo_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(resolution IS NOT DISTINCT FROM 'chip_in') = (chip_in_cents IS NOT NULL)", name=op.f('ck_share_requests_chip_in_amount')),
    sa.CheckConstraint("(status = 'approved') = (resolution IS NOT NULL)", name=op.f('ck_share_requests_resolution_only_when_approved')),
    sa.CheckConstraint("amount IN ('a_bit', 'half', 'all')", name=op.f('ck_share_requests_amount_enum')),
    sa.CheckConstraint("resolution IS NULL OR resolution IN ('free', 'split', 'chip_in')", name=op.f('ck_share_requests_resolution_enum')),
    sa.CheckConstraint("status IN ('pending', 'approved', 'declined', 'expired', 'cancelled')", name=op.f('ck_share_requests_status_enum')),
    sa.CheckConstraint('chip_in_cents IS NULL OR chip_in_cents > 0', name=op.f('ck_share_requests_chip_in_positive')),
    sa.CheckConstraint('note IS NULL OR char_length(note) <= 140', name=op.f('ck_share_requests_note_length')),
    sa.ForeignKeyConstraint(['answered_by_member_id'], ['members.id'], name=op.f('fk_share_requests_answered_by_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_share_requests_house_id_houses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], name=op.f('fk_share_requests_inventory_item_id_inventory_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['requester_member_id'], ['members.id'], name=op.f('fk_share_requests_requester_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_share_requests'))
    )
    op.create_index(op.f('ix_share_requests_house_id_status'), 'share_requests', ['house_id', 'status'], unique=False)
    op.create_index(op.f('ix_share_requests_requester_member_id_created_at'), 'share_requests', ['requester_member_id', 'created_at'], unique=False)
    op.create_index('uq_share_requests_one_pending', 'share_requests', ['inventory_item_id', 'requester_member_id'], unique=True, postgresql_where=sa.text("status = 'pending'"))
    op.create_table('inventory_grants',
    sa.Column('inventory_item_id', sa.UUID(), nullable=False),
    sa.Column('member_id', sa.UUID(), nullable=False),
    sa.Column('share_request_id', sa.UUID(), nullable=False),
    sa.Column('granted_by_member_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['granted_by_member_id'], ['members.id'], name=op.f('fk_inventory_grants_granted_by_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['inventory_item_id'], ['inventory_items.id'], name=op.f('fk_inventory_grants_inventory_item_id_inventory_items'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['member_id'], ['members.id'], name=op.f('fk_inventory_grants_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['share_request_id'], ['share_requests.id'], name=op.f('fk_inventory_grants_share_request_id_share_requests'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('inventory_item_id', 'member_id', name=op.f('pk_inventory_grants'))
    )
    op.create_table('ledger_entries',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('house_id', sa.UUID(), nullable=False),
    sa.Column('from_member_id', sa.UUID(), nullable=False),
    sa.Column('to_member_id', sa.UUID(), nullable=False),
    sa.Column('amount_cents', sa.BigInteger(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('bill_id', sa.UUID(), nullable=True),
    sa.Column('share_request_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("source <> 'chip_in' OR share_request_id IS NOT NULL", name=op.f('ck_ledger_entries_chip_in_has_request')),
    sa.CheckConstraint("source IN ('carryover', 'settle_up', 'chip_in', 'void_credit')", name=op.f('ck_ledger_entries_source_enum')),
    sa.CheckConstraint('amount_cents > 0', name=op.f('ck_ledger_entries_amount_positive')),
    sa.CheckConstraint('from_member_id <> to_member_id', name=op.f('ck_ledger_entries_distinct_parties')),
    sa.ForeignKeyConstraint(['bill_id'], ['bills.id'], name=op.f('fk_ledger_entries_bill_id_bills'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['from_member_id'], ['members.id'], name=op.f('fk_ledger_entries_from_member_id_members'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['house_id'], ['houses.id'], name=op.f('fk_ledger_entries_house_id_houses'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['share_request_id'], ['share_requests.id'], name=op.f('fk_ledger_entries_share_request_id_share_requests'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['to_member_id'], ['members.id'], name=op.f('fk_ledger_entries_to_member_id_members'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ledger_entries'))
    )
    op.create_index(op.f('ix_ledger_entries_house_id_from_member_id_to_member_id'), 'ledger_entries', ['house_id', 'from_member_id', 'to_member_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ledger_entries_house_id_from_member_id_to_member_id'), table_name='ledger_entries')
    op.drop_table('ledger_entries')
    op.drop_table('inventory_grants')
    op.drop_index('uq_share_requests_one_pending', table_name='share_requests', postgresql_where=sa.text("status = 'pending'"))
    op.drop_index(op.f('ix_share_requests_requester_member_id_created_at'), table_name='share_requests')
    op.drop_index(op.f('ix_share_requests_house_id_status'), table_name='share_requests')
    op.drop_table('share_requests')
    op.drop_index(op.f('ix_inventory_owners_member_id'), table_name='inventory_owners')
    op.drop_table('inventory_owners')
    op.drop_index(op.f('ix_share_snapshots_bill_id_computed_at'), table_name='share_snapshots')
    op.drop_table('share_snapshots')
    op.drop_index('ix_payments_bill_id', table_name='payments')
    op.drop_table('payments')
    op.drop_index('uq_inventory_items_item_id', table_name='inventory_items', postgresql_where=sa.text('item_id IS NOT NULL'))
    op.drop_index('ix_inventory_items_house_id_status_place', table_name='inventory_items')
    op.drop_table('inventory_items')
    op.drop_index('uq_allocations_item_id_member_id', table_name='allocations', postgresql_where=sa.text('member_id IS NOT NULL'))
    op.drop_index('uq_allocations_item_id_guest_id', table_name='allocations', postgresql_where=sa.text('guest_id IS NOT NULL'))
    op.drop_index('ix_allocations_item_id', table_name='allocations')
    op.drop_table('allocations')
    op.drop_table('seen_markers')
    op.drop_index(op.f('ix_receipts_bill_id'), table_name='receipts')
    op.drop_table('receipts')
    op.drop_index(op.f('ix_receipt_extractions_bill_id'), table_name='receipt_extractions')
    op.drop_table('receipt_extractions')
    op.drop_index(op.f('ix_outbox_status_next_attempt_at'), table_name='outbox')
    op.drop_table('outbox')
    op.drop_index('ix_items_name_trgm', table_name='items', postgresql_using='gin', postgresql_ops={'name': 'gin_trgm_ops'})
    op.drop_index('ix_items_bill_id_position', table_name='items')
    op.drop_table('items')
    op.drop_index(op.f('ix_guests_bill_id'), table_name='guests')
    op.drop_table('guests')
    op.drop_index('uq_bill_fees_one_adjustment_per_bill', table_name='bill_fees', postgresql_where=sa.text("kind = 'adjustment'"))
    op.drop_index(op.f('ix_bill_fees_bill_id'), table_name='bill_fees')
    op.drop_table('bill_fees')
    op.drop_table('bill_exclusions')
    op.drop_index('ix_activity_events_house_id_created_at', table_name='activity_events')
    op.drop_index('ix_activity_events_bill_id_created_at', table_name='activity_events')
    op.drop_table('activity_events')
    op.drop_index(op.f('ix_house_events_created_at'), table_name='house_events')
    op.drop_table('house_events')
    op.drop_index('ix_bills_store_name_trgm', table_name='bills', postgresql_using='gin', postgresql_ops={'store_name': 'gin_trgm_ops'})
    op.drop_index('ix_bills_house_id_state_purchase_date', table_name='bills')
    op.drop_table('bills')
    op.drop_index(op.f('ix_notifications_user_id_read_at'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_status_next_attempt_at'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index('uq_members_one_admin_per_house', table_name='members', postgresql_where=sa.text("role = 'admin' AND status = 'active'"))
    op.drop_index('uq_members_house_id_lower_display_name', table_name='members', postgresql_where=sa.text("status <> 'removed' AND anonymized_at IS NULL"))
    op.drop_table('members')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_push_subscriptions_user_id'), table_name='push_subscriptions')
    op.drop_table('push_subscriptions')
    op.drop_table('payment_handles')
    op.drop_table('notification_prefs')
    op.drop_index(op.f('ix_idempotency_keys_created_at'), table_name='idempotency_keys')
    op.drop_table('idempotency_keys')
    op.drop_table('houses')
    op.drop_index(op.f('ix_auth_identities_user_id'), table_name='auth_identities')
    op.drop_table('auth_identities')
    op.drop_table('users')
    op.drop_index(op.f('ix_otp_codes_channel_destination_hash_created_at'), table_name='otp_codes')
    op.drop_table('otp_codes')
    op.execute('DROP EXTENSION IF EXISTS pg_trgm')
    op.execute('DROP EXTENSION IF EXISTS citext')
