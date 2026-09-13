"""Energy management (E2): 1 common generation meter (M1) + 4 wing consumption meters (M2..M5) per Pi.

energy_meters            backend-authoritative meter config + Pi-reported health (one row per device x meter)
energy_meter_readings    latest accepted cumulative readings reported by the Pi (baseline/diagnostics, pruned 30 d)
energy_daily             AUTHORITATIVE daily ledger (operating_date, Pi-local) — measured values only, NULL = UNAVAILABLE
energy_bill_history      six-month electricity bill reference per wing (never live meter data)
energy_generation_targets daily generation target per wing = bill daily average x (1 + adjustment%/100)
energy_adjustments       manual / accounting entries (source MANUAL), never overwrite physical telemetry
pi_devices.energy_bus / energy_config_version  RS485 bus config + version delivered to the Pi via /api/pi/sync
"""
from alembic import op

revision = "0011_energy"
down_revision = "0010_lcd_messages_storage_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS energy_bus JSONB")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS energy_config_version INTEGER NOT NULL DEFAULT 0")
    op.execute("""
        CREATE TABLE IF NOT EXISTS energy_meters (
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            meter_id TEXT NOT NULL CHECK (meter_id IN ('M1','M2','M3','M4','M5')),
            role TEXT NOT NULL CHECK (role IN ('GENERATION','CONSUMPTION')),
            wing CHAR(1) CHECK (wing IN ('A','B','C','D')),
            serial TEXT,
            modbus_address INTEGER CHECK (modbus_address BETWEEN 1 AND 247),
            model TEXT,
            register_map JSONB,
            phases INTEGER,
            ct_ratio NUMERIC(10,3),
            max_kw NUMERIC(10,3),
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            comm_status TEXT NOT NULL DEFAULT 'DISABLED',
            last_seen TIMESTAMPTZ,
            last_kwh NUMERIC(14,4),
            power_kw NUMERIC(10,4),
            last_error TEXT,
            attribution JSONB,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (device_id, meter_id),
            CONSTRAINT energy_meters_role_matches_id CHECK (
                (meter_id = 'M1' AND role = 'GENERATION' AND wing IS NULL) OR
                (meter_id = 'M2' AND role = 'CONSUMPTION' AND wing = 'A') OR
                (meter_id = 'M3' AND role = 'CONSUMPTION' AND wing = 'B') OR
                (meter_id = 'M4' AND role = 'CONSUMPTION' AND wing = 'C') OR
                (meter_id = 'M5' AND role = 'CONSUMPTION' AND wing = 'D'))
        )
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS energy_meters_device_address_ux ON energy_meters (device_id, modbus_address) WHERE modbus_address IS NOT NULL AND enabled")
    op.execute("""
        CREATE TABLE IF NOT EXISTS energy_meter_readings (
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            meter_id TEXT NOT NULL,
            ts TIMESTAMPTZ NOT NULL,
            cumulative_kwh NUMERIC(14,4) NOT NULL,
            power_kw NUMERIC(10,4),
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (device_id, meter_id, ts)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS energy_daily (
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            operating_date DATE NOT NULL,
            reset_period TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('OPEN','CLOSED')),
            generation_kwh NUMERIC(14,6),
            generation_source TEXT NOT NULL DEFAULT 'UNAVAILABLE',
            wing_generation JSONB NOT NULL,
            unattributed_generation_kwh NUMERIC(14,6) NOT NULL DEFAULT 0,
            fault_generation_kwh NUMERIC(14,6) NOT NULL DEFAULT 0,
            wing_consumption JSONB NOT NULL,
            consumption_source JSONB NOT NULL,
            samples JSONB, attempts JSONB, gap_kwh JSONB, events JSONB,
            opened_at TIMESTAMPTZ, closed_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
            received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (device_id, operating_date)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS energy_daily_reset_period_idx ON energy_daily (device_id, reset_period)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS energy_bill_history (
            id BIGSERIAL PRIMARY KEY,
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            wing CHAR(1) NOT NULL CHECK (wing IN ('A','B','C','D')),
            bill_month DATE NOT NULL,
            consumption_kwh NUMERIC(14,3) NOT NULL CHECK (consumption_kwh >= 0),
            days INTEGER CHECK (days BETWEEN 1 AND 62),
            note TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (device_id, wing, bill_month)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS energy_generation_targets (
            id BIGSERIAL PRIMARY KEY,
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            wing CHAR(1) NOT NULL CHECK (wing IN ('A','B','C','D')),
            base_daily_average_kwh NUMERIC(14,4) NOT NULL,
            adjustment_percent NUMERIC(7,2) NOT NULL,
            target_kwh_per_day NUMERIC(14,4) NOT NULL,
            basis JSONB NOT NULL,
            effective_from DATE NOT NULL,
            reason TEXT,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS energy_targets_device_wing_idx ON energy_generation_targets (device_id, wing, effective_from DESC, id DESC)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS energy_adjustments (
            id BIGSERIAL PRIMARY KEY,
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            wing CHAR(1) CHECK (wing IN ('A','B','C','D')),
            operating_date DATE NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('MANUAL_GENERATION','MANUAL_CONSUMPTION','ACCOUNTING')),
            value_kwh NUMERIC(14,4) NOT NULL,
            unit TEXT NOT NULL DEFAULT 'kWh',
            reason TEXT NOT NULL,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS energy_adjustments_device_date_idx ON energy_adjustments (device_id, operating_date)")


def downgrade() -> None:
    for t in ("energy_adjustments", "energy_generation_targets", "energy_bill_history", "energy_daily", "energy_meter_readings", "energy_meters"):
        op.execute(f"DROP TABLE IF EXISTS {t}")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS energy_config_version")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS energy_bus")
