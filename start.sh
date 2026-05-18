#!/bin/bash
# Image Studio 啟動腳本
# 用法: ./start.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 檢查 API Key
if [ -z "$OPENAI_API_KEY" ]; then
    # 從 ~/.hermes/.env 讀取
    if [ -f "$HOME/.hermes/.env" ]; then
        source "$HOME/.hermes/.env"
    fi
fi

if [ -z "$OPENAI_API_KEY" ]; then
    echo "⚠️  未設定 OPENAI_API_KEY"
    echo "  請執行: export OPENAI_API_KEY=sk-..."
    echo "  或在 ~/.hermes/.env 中設定"
fi

echo "🚀 啟動 Image Studio http://localhost:5001"
python3 app.py
