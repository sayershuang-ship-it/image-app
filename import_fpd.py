#!/usr/bin/env python3
"""
import_fpd.py — Sync 14 female-portrait-director styles to image-studio community_prompts

Run: python import_fpd.py
The script will UPSERT (replace) the 14 FPD styles by route_id.

NOTE: data/fpd_styles.json is the reference copy; the STYLES list below is the
authoritative import source.
"""

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "prompts.db")

STYLES = [
    {
        "route_id": "clean-lifestyle", "style_name_zh": "清纯生活照", "style_name_en": "Clean Lifestyle",
        "category": "portrait", "tags": "清纯,温柔,自然,咖啡馆,窗边,生活剧照",
        "prompt": "清纯生活照风格：柔和自然光，奶油暖白滤镜，咖啡馆/窗边场景，日常服装，清纯干净，电影生活剧照感。20-28岁年轻成年东方女性，成年气质明确，五官自然，不过度欧美化，不幼态化。"
    },
    {
        "route_id": "pure-desire-curve", "style_name_zh": "纯欲曲线生活照", "style_name_en": "Pure Desire Curve",
        "category": "portrait", "tags": "纯欲,曲线,锁骨,腰线,小腹,大腿,身形吸引力,冷白",
        "prompt": "纯欲曲线生活照：清纯脸×成年女性身形吸引力，克制欲感，冷白低反差自然光滤镜。贴身服装显肩颈/锁骨/腰线/小腹/大腿线条。20-28岁年轻成年东方女性，冷白生活剧照感。"
    },
    {
        "route_id": "urban-fashion", "style_name_zh": "都市时尚写真", "style_name_en": "Urban Fashion",
        "category": "ad-creative", "tags": "都市,时尚,街拍,OOTD,通勤,西装,风衣,高级感",
        "prompt": "都市时尚写真：城市街边/玻璃橱窗背景，高级穿搭感，街边自然光，松弛自信都市女性。20-28岁年轻成年东方女性，不过度欧美化，高级感街拍穿搭。"
    },
    {
        "route_id": "gufeng-xianxia", "style_name_zh": "古风仙侠美人图", "style_name_en": "Gufeng Xianxia",
        "category": "poster", "tags": "古风,仙侠,唐风,古偶,披帛,大袖衫,宫殿,云雾山水",
        "prompt": "古风仙侠美人图：宫殿回廊/云雾山水/古风庭院，披帛大袖衫，珠玉头饰，梦幻柔光，精修角色海报感。20-28岁年轻成年东方女性，唐风幻想审美，东方幻想角色感。"
    },
    {
        "route_id": "ecommerce-tryon", "style_name_zh": "电商服装模特图", "style_name_en": "Ecommerce Try-On",
        "category": "ecommerce", "tags": "电商,服装展示,试衣,主图,不要色差,商品展示",
        "prompt": "电商服装模特图：服装还原优先，商品展示导向，简洁摄影棚/浅色纯背景，均匀柔和电商摄影光，低色差，高清，服装颜色版型材质准确。"
    },
    {
        "route_id": "retro-hongkong", "style_name_zh": "复古港风写真", "style_name_en": "Retro Hong Kong",
        "category": "portrait", "tags": "港风,港片女主,旧香港,茶餐厅,霓虹,胶片",
        "prompt": "复古港风写真：旧香港场景/茶餐厅/霓虹灯，胶片质感，复古色调，港风穿搭。20-28岁年轻成年东方女性，港片女主感，怀旧电影氛围。"
    },
    {
        "route_id": "french-lazy", "style_name_zh": "法式慵懒写真", "style_name_en": "French Lazy",
        "category": "portrait", "tags": "法式,慵懒,松弛,公寓,阳台,奶油暖白,法式生活",
        "prompt": "法式慵懒写真：法式公寓/阳台场景，奶油暖白滤镜，轻熟松弛感，自然妆发，法式穿搭。20-28岁年轻成年东方女性，轻熟温柔松弛。"
    },
    {
        "route_id": "new-chinese", "style_name_zh": "新中式东方写真", "style_name_en": "New Chinese",
        "category": "poster", "tags": "新中式,东方美学,茶室,屏风,竹影,留白,克制,素色",
        "prompt": "新中式东方写真：茶室/屏风/竹影/木质空间，大片留白，新中式服装(盘扣/棉麻)，柔和侧光，东方克制清雅美学。20-28岁年轻成年东方女性，高级清雅。"
    },
    {
        "route_id": "sporty-active", "style_name_zh": "活力运动写真", "style_name_en": "Sporty Active",
        "category": "ad-creative", "tags": "运动,活力,健身,网球,跑步,运动背心,健康线条,阳光",
        "prompt": "活力运动写真：户外运动场/跑道/网球场，阳光明亮自然光，运动背心/短裤穿搭，健康清爽元气。20-28岁年轻成年东方女性，健康阳光运动生活感。"
    },
    {
        "route_id": "travel-vacation", "style_name_zh": "旅行假日写真", "style_name_en": "Travel Vacation",
        "category": "portrait", "tags": "旅行,假日,度假,海岛,民宿,酒店阳台,松弛感,旅拍",
        "prompt": "旅行假日写真：海边街道/酒店阳台/民宿场景，傍晚柔和自然光或明亮假日光，旅行穿搭草编包，明亮假日滤镜，自然松弛旅途瞬间。20-28岁年轻成年东方女性。"
    },
    {
        "route_id": "studio-retouched", "style_name_zh": "影楼精修写真", "style_name_en": "Studio Retouched",
        "category": "portrait", "tags": "影楼,精修,棚拍,写真馆,商业写真,柔光",
        "prompt": "影楼精修写真：低饱和背景布/简洁棚拍，柔光箱，精致妆发，精修但保留肤质纹理，高级干净商业完成度。20-28岁年轻成年东方女性，端正精致棚拍人像。"
    },
    {
        "route_id": "oriental-voluptuous", "style_name_zh": "东方丰腴写真", "style_name_en": "Oriental Voluptuous",
        "category": "portrait", "tags": "东方丰腴,丰润,柔润,成熟曲线,旗袍曲线,轻熟",
        "prompt": "东方丰腴写真：东方成熟柔润身形，丰腴曲线，旗袍/新中式服装，柔润光线，自然健康身形表达。20-28岁年轻成年东方女性，成熟柔润。"
    },
    {
        "route_id": "cold-xianxia-enhanced", "style_name_zh": "清冷仙气古风增强版", "style_name_en": "Cold Ethereal Gufeng",
        "category": "poster", "tags": "清冷仙气,冷白,疏离,空灵,月白,冰蓝,克制",
        "prompt": "清冷仙气古风增强版：冷白低饱和色调，月白/冰蓝服装，空灵克制，宫灯回廊/竹林薄雾，清冷仙气光。20-28岁年轻成年东方女性，清冷疏离东方幻想。"
    },
    {
        "route_id": "bright-luxury-gufeng", "style_name_zh": "明媚华贵古风增强版", "style_name_en": "Bright Luxury Gufeng",
        "category": "poster", "tags": "明媚华贵,盛唐,红金,宫廷,华服,重工头饰,女主感",
        "prompt": "明媚华贵古风增强版：红金唐风/盛唐宫廷，华服重工刺绣，珠玉头饰，明媚明亮光，女主登场感。20-28岁年轻成年东方女性，明艳华贵东方幻想。"
    },
]

CATEGORY_MAP = {
    "portrait": "人像攝影",
    "ad-creative": "廣告創意",
    "poster": "海報設計",
    "ecommerce": "電商產品",
    "character": "角色設計",
}


def get_connection():
    return sqlite3.connect(DB_PATH)


def upsert_style(conn, style):
    now = datetime.now().isoformat()
    title = f"[FPD] {style['style_name_zh']} ({style['style_name_en']})"

    # Upsert by route_id (replace existing)
    conn.execute("""
        INSERT INTO community_prompts (title, category, platform, prompt, score, tags, collected_at, route_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(route_id) DO UPDATE SET
            title = excluded.title,
            category = excluded.category,
            platform = excluded.platform,
            prompt = excluded.prompt,
            score = excluded.score,
            tags = excluded.tags,
            collected_at = excluded.collected_at
    """, (
        title,
        CATEGORY_MAP[style["category"]],
        "fpd",
        style["prompt"],
        90,
        style["tags"],
        now,
        style["route_id"],
    ))


def main():
    conn = get_connection()

    # Ensure route_id column exists (fallback for older DBs)
    try:
        conn.execute("ALTER TABLE community_prompts ADD COLUMN route_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_route_id ON community_prompts(route_id)")
        conn.commit()
    except Exception:
        pass  # column may already exist

    count = 0
    for style in STYLES:
        upsert_style(conn, style)
        count += 1

    conn.commit()
    conn.close()

    print(f"✅ Synced {count} FPD styles to {DB_PATH}")
    print(f"   Categories: {dict(CATEGORY_MAP)}")


if __name__ == "__main__":
    main()