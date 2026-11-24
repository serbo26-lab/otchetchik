-- Schema version 10, extracted from db.py at V1.6.0 (4913bd1).
-- Historical structure only: no user records or sample data.
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    ogrn_inn TEXT NOT NULL DEFAULT '',
    reception_address TEXT NOT NULL DEFAULT '',
    report_person TEXT NOT NULL DEFAULT '',
    report_contact TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS houses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    address TEXT NOT NULL DEFAULT '',
    total_area REAL NOT NULL DEFAULT 0,
    management_start TEXT NOT NULL DEFAULT '',
    management_end TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'В управлении',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_id INTEGER REFERENCES work_categories(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT '',
    section TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'усл.',
    default_price REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS work_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT NOT NULL DEFAULT '',
    subgroup_name TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'усл.',
    default_price REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT 'Содержание',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS template_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id INTEGER NOT NULL REFERENCES templates(id) ON DELETE CASCADE,
    work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'усл.',
    unit_price REAL NOT NULL DEFAULT 0,
    plan_qty REAL NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
    house_id INTEGER REFERENCES houses(id) ON DELETE SET NULL,
    year INTEGER NOT NULL,
    period_start TEXT NOT NULL DEFAULT '',
    period_end TEXT NOT NULL DEFAULT '',
    show_period_note INTEGER NOT NULL DEFAULT 0,
    publish_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Черновик',
    management_cost REAL NOT NULL DEFAULT 0,
    repair_start_balance REAL NOT NULL DEFAULT 0,
    repair_accrual REAL NOT NULL DEFAULT 0,
    repair_work_cost REAL NOT NULL DEFAULT 0,
    repair_end_balance REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(house_id, year, period_start, period_end)
);

CREATE TABLE IF NOT EXISTS report_content_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'усл.',
    unit_price REAL NOT NULL DEFAULT 0,
    plan_qty REAL NOT NULL DEFAULT 0,
    plan_cost REAL NOT NULL DEFAULT 0,
    actual_qty REAL NOT NULL DEFAULT 0,
    actual_cost REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'ручной ввод',
    is_manual INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS report_repair_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    basis TEXT NOT NULL DEFAULT '',
    cost REAL NOT NULL DEFAULT 0,
    volume TEXT NOT NULL DEFAULT '',
    act_details TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'ручной ввод',
    is_manual INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS report_management_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    work_item_id INTEGER REFERENCES work_items(id) ON DELETE SET NULL,
    name TEXT NOT NULL DEFAULT '',
    unit TEXT NOT NULL DEFAULT 'усл.',
    unit_price REAL NOT NULL DEFAULT 0,
    qty REAL NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'ручной ввод',
    is_manual INTEGER NOT NULL DEFAULT 0,
    position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS report_financials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE,
    payer_type TEXT NOT NULL DEFAULT 'Собственники',
    debt_start REAL NOT NULL DEFAULT 0,
    accrued REAL NOT NULL DEFAULT 0,
    paid REAL NOT NULL DEFAULT 0,
    debt_end REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    UNIQUE(report_id, payer_type)
);

CREATE TABLE IF NOT EXISTS report_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(id) ON DELETE CASCADE UNIQUE,
    pretensions_count INTEGER NOT NULL DEFAULT 0,
    lawsuits_count INTEGER NOT NULL DEFAULT 0,
    recovered_amount REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);
