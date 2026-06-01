-- ============================================================
--  F.L.E.X VAPE SHOP — Migration: Add missing columns & new tables
--  Run this in pgAdmin Query Tool on your flex_vape_db database
-- ============================================================

-- Add missing columns to product table (safe to run multiple times)
ALTER TABLE product ADD COLUMN IF NOT EXISTS discount   FLOAT        DEFAULT 0.0;
ALTER TABLE product ADD COLUMN IF NOT EXISTS code_name  VARCHAR(50);

-- Purchase Order table
CREATE TABLE IF NOT EXISTS purchase_order (
    id         SERIAL PRIMARY KEY,
    po_number  VARCHAR(30) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    status     VARCHAR(20) DEFAULT 'pending'
);

-- Purchase Order Items table
CREATE TABLE IF NOT EXISTS purchase_order_item (
    id           SERIAL PRIMARY KEY,
    po_id        INTEGER NOT NULL REFERENCES purchase_order(id) ON DELETE CASCADE,
    product_id   INTEGER REFERENCES product(id) ON DELETE SET NULL,
    name         VARCHAR(100),
    flavor       VARCHAR(100),
    type         VARCHAR(50),
    ordered_qty  INTEGER DEFAULT 0,
    received_qty INTEGER DEFAULT 0,
    status       VARCHAR(20) DEFAULT 'pending'
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_po_status   ON purchase_order (status);
CREATE INDEX IF NOT EXISTS idx_poi_po_id   ON purchase_order_item (po_id);

-- Verify
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' ORDER BY table_name;
