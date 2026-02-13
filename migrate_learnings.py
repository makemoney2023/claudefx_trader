"""
Migration script: Create fresh DB for live trading with demo learnings only.

Discards all real account data (tainted by pre-fix bugs) and starts fresh
with only the 48 high-quality trade learnings from the demo account.
"""
import os
import sys
import sqlite3
import shutil


def main():
    workspace = os.path.dirname(os.path.abspath(__file__))
    demo_db_path = os.path.join(workspace, "trading_bot_demo_backup.db")
    target_db_path = os.path.join(workspace, "trading_bot.db")
    
    print("=" * 60)
    print("MIGRATION: Fresh DB with Demo Learnings Only")
    print("=" * 60)
    
    # 1. Verify demo backup exists
    if not os.path.exists(demo_db_path):
        print(f"ERROR: Demo backup not found at {demo_db_path}")
        sys.exit(1)
    
    # 2. Count learnings in demo backup
    demo_conn = sqlite3.connect(demo_db_path)
    demo_count = demo_conn.execute("SELECT COUNT(*) FROM trade_learnings").fetchone()[0]
    print(f"\nDemo DB: {demo_count} trade learnings found")
    
    if demo_count == 0:
        print("ERROR: No learnings found in demo backup!")
        demo_conn.close()
        sys.exit(1)
    
    # 3. Remove existing target DB if present
    if os.path.exists(target_db_path):
        backup_name = target_db_path + ".pre_migration_backup"
        print(f"\nBacking up existing DB to {backup_name}")
        shutil.copy2(target_db_path, backup_name)
        os.remove(target_db_path)
        print("Existing DB removed")
    
    # 4. Create fresh DB by copying demo DB structure
    print("\nCreating fresh database with demo schema...")
    
    # Get the full schema from demo DB (all CREATE TABLE statements)
    schema_rows = demo_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"
    ).fetchall()
    
    # Get all index definitions
    index_rows = demo_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
    ).fetchall()
    
    # Create fresh target DB
    target_conn = sqlite3.connect(target_db_path)
    
    for (schema_sql,) in schema_rows:
        target_conn.execute(schema_sql)
    
    for (index_sql,) in index_rows:
        try:
            target_conn.execute(index_sql)
        except sqlite3.OperationalError:
            pass  # Index may already exist from table creation
    
    target_conn.commit()
    print("Fresh schema created with all tables and indexes")
    
    # 5. Copy ONLY trade_learnings from demo DB
    print(f"\nMigrating {demo_count} trade learnings from demo DB...")
    
    # Get all columns
    columns = [
        row[1] for row in demo_conn.execute("PRAGMA table_info(trade_learnings)").fetchall()
    ]
    columns_str = ", ".join(columns)
    placeholders = ", ".join(["?" for _ in columns])
    
    rows = demo_conn.execute(f"SELECT {columns_str} FROM trade_learnings").fetchall()
    
    target_conn.executemany(
        f"INSERT INTO trade_learnings ({columns_str}) VALUES ({placeholders})",
        rows
    )
    target_conn.commit()
    
    # 6. Verify
    migrated_count = target_conn.execute("SELECT COUNT(*) FROM trade_learnings").fetchone()[0]
    trades_count = target_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    analysis_count = target_conn.execute("SELECT COUNT(*) FROM analysis_logs").fetchone()[0]
    kb_count = target_conn.execute("SELECT COUNT(*) FROM knowledge_base").fetchone()[0]
    reviews_count = target_conn.execute("SELECT COUNT(*) FROM weekly_reviews").fetchone()[0]
    
    print("\n" + "=" * 60)
    print("VERIFICATION - Fresh DB Contents:")
    print("=" * 60)
    print(f"  trade_learnings: {migrated_count} (from demo)")
    print(f"  trades:          {trades_count} (should be 0 - fresh start)")
    print(f"  analysis_logs:   {analysis_count} (should be 0 - fresh start)")
    print(f"  knowledge_base:  {kb_count} (should be 0 - will rebuild)")
    print(f"  weekly_reviews:  {reviews_count} (should be 0 - will generate)")
    
    # 7. Show sample learnings
    print("\nSample learnings migrated:")
    samples = target_conn.execute(
        "SELECT symbol, direction, outcome, grade, r_multiple FROM trade_learnings LIMIT 5"
    ).fetchall()
    for s in samples:
        print(f"  {s[0]} {s[1]} -> {s[2]} (grade: {s[3]}, R: {s[4]:.2f})")
    
    # Cleanup
    demo_conn.close()
    target_conn.close()
    
    success = (
        migrated_count == demo_count and
        trades_count == 0 and
        analysis_count == 0
    )
    
    if success:
        print("\n" + "=" * 60)
        print("MIGRATION SUCCESSFUL!")
        print(f"Fresh DB created with {migrated_count} demo learnings")
        print("All other tables empty for fresh live trading start")
        print("=" * 60)
    else:
        print("\nWARNING: Verification failed!")
        print(f"Expected {demo_count} learnings, got {migrated_count}")
        print(f"Expected 0 trades, got {trades_count}")
        sys.exit(1)


if __name__ == "__main__":
    main()
