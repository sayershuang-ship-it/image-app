# Image App

AI 生圖提示詞管理工具。收錄社群優質提示詞並整理分類，支援 GPT Image 2 / GPT-4o 圖像生成。

## 技術棧

- **後端：** Python / Flask
- **資料庫：** SQLite（prompts.db，含 prompts 與 community_prompts 兩表）
- **前端：** 6 個 Jinja 模板（index 首頁、studio 生成器、gallery 畫廊、history 歷史、templates 提示詞庫、spark 實驗性介面）
- **生成模型：** OpenAI gpt-image-2 / gpt-4o

## 目錄結構

```
image-app/
├── app.py                    # Flask 主程式
├── prompts.db               # SQLite 資料庫（gitignored）
├── requirements.txt          # Python 依賴
├── start.sh                  # 啟動腳本
├── templates/                # Jinja 模板
│   ├── index.html            # 首頁（獨立設計）
│   ├── studio.html           # 生成器
│   ├── gallery.html          # 畫廊
│   ├── history.html          # 歷史記錄
│   ├── templates.html        # 提示詞庫
│   └── spark.html            # 實驗性介面
├── static/                   # 靜態資源（CSS/JS）
├── data/
│   └── fpd_styles.json       # FPD 風格參考
├── sync_awesome_prompts.py   # 社群提示詞同步腳本
├── import_fpd.py             # FPD 風格匯入腳本
├── tests/                    # pytest 測試
└── plans/                    # 開發計畫
```

## 主要路由

| 路由 | 說明 |
|------|------|
| `/` | 首頁 |
| `/studio` | 生成器 |
| `/gallery` | 畫廊 |
| `/history-view` | 歷史記錄 |
| `/templates` | 提示詞庫 |
| `/spark` | 實驗性介面 |
| `/generate` | 單張生成 |
| `/batch-generate` | 批量生成 |
| `/api/v1/generate` | REST API 生成 |
| `/api/search-prompts` | 提示詞搜尋 |
| `/api/stats` | 統計資訊 |
| `/history` | JSON 歷史列表 |
| `/export-zip` | 匯出 ZIP |
| `/health` | 健康檢查 |

## 啟動

```bash
cd ~/projects/image-app
./start.sh
# 或手動：
OPENAI_API_KEY=sk-... venv/bin/python app.py
# → http://localhost:5001
```

## 測試

```bash
venv/bin/pip install pytest
venv/bin/python -m pytest tests/ -q
```
