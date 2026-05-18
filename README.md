# Image App

AI 生圖提示詞管理工具。收錄社群優質提示詞並整理分類。

## 技術棧

- **後端：** Python / Flask
- **資料庫：** SQLite（prompts.db）
- **前端：** HTML/CSS/JS（index.html）
- **策展來源：** 808aiworkshop.com

## 目錄結構

```
image-app/
├── app.py                 # Flask 主程式
├── prompts.db            # SQLite 資料庫
│   ├── prompts           # 自有提示詞（53筆）
│   └── community_prompts  # 社群提示詞（162筆）
├── 808ai_prompts_data.json  # 原始 JSON 備份
├── index.html             # 前端頁面
├── templates/
├── Design/
└── requirements.txt
```

## 資料庫結構

| 欄位 | 說明 |
|---|---|
| `id` | 流水號 |
| `title` | 標題 |
| `category` | 分類（海報插畫/UI截圖/人像攝影等） |
| `platform` | 來源平台 |
| `author` | 作者 |
| `prompt` | 提示詞內容 |
| `source_url` | 原始連結 |
| `image_url` | 參考圖片 |
| `score` | 評分 |

## 啟動

```bash
cd ~/projects/image-app
pip install -r requirements.txt
python app.py
```