import sqlite3
import pandas as pd
import os

DB_PATH = 'horses.db'

def init_db():
    """建表（有除錯）"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('''
    CREATE TABLE IF NOT EXISTS current_horses (
        horse_name TEXT, horse_id TEXT, trainer TEXT, 
        recent_form TEXT, total_races INTEGER, wins INTEGER,
        updated_date TEXT
    )
    ''')
    conn.commit()
    print("✅ DB 初始化：current_horses 表就緒")
    conn.close()

def save_horses(horses_list):
    """🔥 修復版：強制存 + 驗證"""
    print(f"📥 準備存 {len(horses_list)} 匹馬")
    
    if not horses_list:
        print("❌ 空資料，跳過")
        return
    
    df = pd.DataFrame(horses_list)
    print(f"📊 DataFrame：{df.shape}，欄位：{list(df.columns)}")
    print("📋 前 2 匹：", df.head(2).to_dict())
    
    conn = sqlite3.connect(DB_PATH)
    try:
        # 🔥 清空重存
        conn.execute("DELETE FROM current_horses")
        df.to_sql('current_horses', conn, if_exists='append', index=False)
        conn.commit()
        
        # 🔥 立即驗證
        count = conn.execute("SELECT COUNT(*) FROM current_horses").fetchone()[0]
        print(f"✅ 存入成功！DB 總數：{count} 匹")
        
        # 存 CSV 備份
        df.to_csv('horses.csv', index=False, encoding='utf-8-sig')
        print("💾 horses.csv 備份完成")
        
    except Exception as e:
        print(f"❌ 存檔失敗：{e}")
    finally:
        conn.close()

def search_horse(name):
    """搜馬（有除錯）"""
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM current_horses WHERE horse_name LIKE ? LIMIT 20",
            conn, params=[f'%{name}%']
        )
        print(f"🔍 搜'{name}' → {len(df)} 筆：{[row['horse_name'] for _, row in df.iterrows()]}")
        return df.to_dict('records')
    except Exception as e:
        print(f"❌ 搜尋失敗：{e}")
        return []
    finally:
        conn.close()