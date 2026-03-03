import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import os
from datetime import datetime, timedelta
import base64
from pathlib import Path
import logging
from functools import lru_cache
from io import BytesIO

# === 1. 页面配置 ===
st.set_page_config(
    page_title="lhr-timeline", 
    layout="wide", 
    page_icon="🎬",
    initial_sidebar_state="collapsed"
)

# === 2. 核心工具函数 ===
# 配置日志
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent

def get_safe_path(path):
    """安全处理文件路径 - 只保留文件名，去除Windows绝对路径"""
    if pd.isna(path) or str(path) in ['nan', '']:
        return None
    try:
        # 如果是Windows绝对路径，只取文件名
        path_str = str(path).strip().strip('"').strip("'")
        # 统一替换反斜杠为正斜杠，然后提取文件名
        path_str = path_str.replace('\\', '/')
        # 提取文件名（去掉目录路径）
        filename = os.path.basename(path_str)
        return filename
    except Exception:
        return None

def find_file_with_extensions(base_path, extensions):
    """查找带不同扩展名的文件 - 支持assets目录"""
    if not base_path:
        return None

    p = Path(str(base_path))
    candidates = []

    candidates.append(p)
    if not p.is_absolute():
        # 当前工作目录
        candidates.append(Path.cwd() / p)
        # 脚本所在目录
        candidates.append(APP_DIR / p)
        # assets目录查找路径
        candidates.append(Path.cwd() / "assets" / "背景图" / p)
        candidates.append(Path.cwd() / "assets" / "素材" / p)
        candidates.append(Path.cwd() / "assets" / "logo" / p)
        candidates.append(APP_DIR / "assets" / "背景图" / p)
        candidates.append(APP_DIR / "assets" / "素材" / p)
        candidates.append(APP_DIR / "assets" / "logo" / p)

    for c in candidates:
        if c.exists():
            return str(c)

    for ext in extensions:
        for c in candidates:
            test_path = Path(str(c) + ext)
            if test_path.exists():
                return str(test_path)

    return None

def detect_image_format(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, 'rb') as f:
            h = f.read(32)
    except Exception:
        return "unknown"

    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp"
    if h.startswith(b"GIF87a") or h.startswith(b"GIF89a"):
        return "gif"

    if (b"ftypheic" in h) or (b"ftypheif" in h) or (b"ftypmif1" in h and (b"heic" in h or b"heif" in h)):
        return "heic"

    return "unknown"

@st.cache_data(ttl=3600)  # 缓存1小时
def get_base64_cached(file_path, file_mtime):
    """带缓存的base64编码"""
    if not file_path or not os.path.exists(file_path):
        return None
    
    try:
        with open(file_path, 'rb') as f:
            data = f.read()
        return base64.b64encode(data).decode()
    except Exception as e:
        logger.error(f"Error encoding file {file_path}: {e}")
        return None


@st.cache_data(ttl=3600)
def get_image_data_url_cached(file_path, file_mtime, max_width=1600, quality=75, target_max_b64_len=90_000):
    """生成更稳定的 data URL（对大图做缩放/压缩，避免 base64 过长导致浏览器加载失败）"""
    if not file_path or not os.path.exists(file_path):
        return None

    try:
        with open(file_path, 'rb') as f:
            header = f.read(64)
    except Exception:
        header = b""

    is_heic = (b"ftypheic" in header) or (b"ftypheif" in header) or (b"ftypmif1" in header and (b"heic" in header or b"heif" in header))

    # 优先用 Pillow 缩放压缩（更稳），没有 Pillow 就退化为原始 base64
    try:
        from PIL import Image, ImageOps

        if is_heic:
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except Exception:
                pass

        img = Image.open(file_path)
        img = ImageOps.exif_transpose(img)

        # 统一转 JPEG（背景图无需透明通道，体积更小）
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        elif img.mode == "L":
            img = img.convert("RGB")

        # 初次按 max_width 缩放
        if max_width and img.width and img.width > max_width:
            new_h = int(img.height * (max_width / img.width))
            img = img.resize((int(max_width), int(new_h)), Image.LANCZOS)

        # 保险：如果 base64 仍然偏大，继续逐步缩小（最多 3 次）
        cur_img = img
        cur_quality = int(quality)
        for _ in range(4):
            buf = BytesIO()
            cur_img.save(buf, format="JPEG", quality=cur_quality, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
            if len(b64) <= int(target_max_b64_len):
                return f"data:image/jpeg;base64,{b64}"

            # 继续缩小：尺寸 * 0.85，质量 - 7
            new_w = max(600, int(cur_img.width * 0.85))
            new_h = int(cur_img.height * (new_w / cur_img.width))
            cur_img = cur_img.resize((new_w, new_h), Image.LANCZOS)
            cur_quality = max(55, cur_quality - 7)

        return f"data:image/jpeg;base64,{b64}"

    except Exception as e:
        logger.error(f"Error creating optimized data url for {file_path}: {e}")
        # HEIC/HEIF：如果没有解码支持，不能伪装成 image/jpeg，否则浏览器必裂图
        if is_heic:
            return None

        # 退化：原始 base64
        raw_b64 = get_base64_cached(file_path, file_mtime)
        if not raw_b64:
            return None
        ext = Path(file_path).suffix.lower()
        mime = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
        return f"data:{mime};base64,{raw_b64}"

# === 3. VOGUE 风格 CSS ===
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+SC:wght@300;700&display=swap');

    .stApp {
        background-color: #FFFFFF !important;  /* 白底 */
        color: #333333 !important;  /* 深灰字体 */
    }
    
    h1, h2, h3, .serif-font, .hero-title, .quote-text {
        font-family: 'Noto Serif SC', 'Songti SC', 'SimSun', serif !important;
        font-weight: 300 !important;
        color: #1A1A1A !important;  /* 深黑 */
    }
    p, span, div, li {
        font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif;
        color: #666666;  /* 中灰 */
        letter-spacing: 0.5px;
    }

    .modebar {display: none !important;}
    
    .stButton > button {
        background: rgba(0,0,0,0.05);  /* 浅灰背景 */
        border: 1px solid rgba(0,0,0,0.1);  /* 浅灰边框 */
        color: #666666;  /* 中灰字体 */
        border-radius: 0px; 
        transition: all 0.3s;
    }
    .stButton > button:hover {
        border-color: #000000;  /* 黑色边框 */
        color: #000000;  /* 黑色字体 */
    }
    
    /* 移动端按钮样式优化 */
    @media only screen and (max-width: 768px) {
        .stButton > button {
            width: 100%;
            background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%);
            border: 1px solid rgba(0,0,0,0.08);
            color: #1A1A1A;
            border-radius: 8px;
            padding: 8px 16px;
            margin: 4px 0;
            font-family: 'Noto Serif SC', serif;
            font-weight: 500;
            font-size: 0.85rem;
            letter-spacing: 0.5px;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            box-shadow: 0 1px 4px rgba(0,0,0,0.05);
        }
        .stButton > button:hover {
            background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
            border-color: #4A5568;
            color: #4A5568;
            transform: translateY(-2px);
            box-shadow: 0 4px 16px rgba(0,0,0,0.12);
        }
        
        /* 小按钮专用样式 */
        .stColumns [data-testid="column"]:first-child .stButton > button {
            background: rgba(74, 85, 104, 0.1);
            border: 1px solid rgba(74, 85, 104, 0.2);
            color: #4A5568;
            padding: 6px 12px;
            margin: 2px 0;
            font-size: 0.8rem;
            font-weight: 400;
            min-height: auto;
            height: auto;
            line-height: 1.2;
            border-radius: 8px;
            width: 100%;
            opacity: 0.8;
            transition: all 0.2s ease;
            text-align: center;
        }
        
        .stColumns [data-testid="column"]:first-child .stButton > button:hover {
            background: rgba(74, 85, 104, 0.2);
            border-color: #4A5568;
            color: #1A1A1A;
            opacity: 1;
            transform: translateY(-1px);
        }
    }

    .media-container {
        display: flex;
        flex-direction: column;
        align-items: center;
        width: 100%;
        margin: 0 auto;
    }
    
    .vogue-media {
        width: 100%; max-width: 800px;
        margin: 0 auto; display: block;
        margin-top: -30px; margin-bottom: -30px;
        -webkit-mask-image: linear-gradient(to bottom, transparent 0%, black 5%, black 95%, transparent 100%);
        mask-image: linear-gradient(to bottom, transparent 0%, black 5%, black 95%, transparent 100%);
        opacity: 0.9; transition: opacity 0.5s;
    }
    .vogue-media:hover { opacity: 1; transform: scale(1.01); }

    /* 强制所有背景图上的文字为纯白 */
    .top-banner * {
        color: #FFFFFF !important;
        text-shadow: 2px 2px 4px rgba(0,0,0,0.5) !important;
    }
    
    /* 专门针对作品名 - 调整为正常粗细，添加多层阴影 */
    .top-banner h1 {
        font-weight: 400 !important;
        text-shadow: 
            1px 1px 0 rgba(0,0,0,0.95),
            2px 2px 0 rgba(0,0,0,0.85),
            3px 3px 2px rgba(0,0,0,0.75),
            4px 4px 4px rgba(0,0,0,0.65),
            5px 5px 6px rgba(0,0,0,0.55) !important;
    }
    
    /* 为其他文字也添加多层阴影 */
    .top-banner .vogue-subtitle {
        text-shadow: 
            1px 1px 0 rgba(0,0,0,0.9),
            2px 2px 2px rgba(0,0,0,0.8),
            3px 3px 4px rgba(0,0,0,0.7) !important;
    }
    
    .top-banner .vogue-meta {
        text-shadow: 
            1px 1px 0 rgba(0,0,0,0.9),
            2px 2px 2px rgba(0,0,0,0.8),
            3px 3px 4px rgba(0,0,0,0.7) !important;
    }
    
    /* Logo区域优化 */
    .logo-container {
        padding: 20px 0 10px 0 !important;
        margin: 0 auto;
        text-align: center;
    }
    
    .logo-container img {
        max-height: 120px !important;
        width: auto !important;
        object-fit: contain;
    }
    
    /* 台词区域升级 */
    .quote-container {
        position: relative;
        padding: 60px 40px;
        margin: 40px auto;
        max-width: 900px;
        background: linear-gradient(135deg, #fafafa 0%, #f5f5f5 100%);
        border-radius: 2px;
        overflow: hidden;
    }
    
    .quote-container::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 1px;
        background: linear-gradient(90deg, transparent, #333, transparent);
    }
    
    .quote-container::after {
        content: '';
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        height: 1px;
        background: linear-gradient(90deg, transparent, #333, transparent);
    }
    
    .quote-text {
        font-family: 'Noto Serif SC', serif;
        font-size: 1.6rem;
        line-height: 1.6;
        color: #1a1a1a;
        text-align: center;
        font-style: italic;
        font-weight: 300;
        letter-spacing: 1px;
        position: relative;
        margin: 0;
    }
    
    .quote-mark {
        font-size: 4rem;
        color: #ccc;
        position: absolute;
        opacity: 0.3;
        font-family: Georgia, serif;
    }
    
    .quote-mark.open {
        top: -20px;
        left: -30px;
    }
    
    .quote-mark.close {
        bottom: -40px;
        right: -30px;
    }
    
    .quote-source {
        text-align: center;
        margin-top: 20px;
        font-size: 0.9rem;
        color: #666;
        letter-spacing: 2px;
        text-transform: uppercase;
    }
    
    /* 页面切换动画 */
    .fade-in {
        animation: fadeIn 1.2s ease-in-out;
    }
    
    @keyframes fadeIn {
        from { opacity: 0; transform: translateY(20px); }
        to { opacity: 1; transform: translateY(0); }
    }
    
    /* 页面整体氛围提升 */
    .timeline-container {
        background: linear-gradient(135deg, #f8f8f8 0%, #ffffff 50%, #f5f5f5 100%);
        position: relative;
        overflow: hidden;
    }
    
    .timeline-container::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        bottom: 0;
        background-image: 
            radial-gradient(circle at 2px 2px, rgba(0,0,0,0.02) 1px, transparent 0),
            linear-gradient(45deg, transparent 49%, rgba(0,0,0,0.01) 50%, transparent 51%);
        background-size: 30px 30px, 100px 100px;
        pointer-events: none;
        z-index: 1;
    }
    
    /* 胶片边框装饰 */
    .film-border {
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 40px;
        background: repeating-linear-gradient(
            90deg,
            #333 0px,
            #333 2px,
            transparent 2px,
            transparent 8px,
            #333 8px,
            #333 10px,
            transparent 10px,
            transparent 16px
        );
        opacity: 0.1;
        z-index: 2;
    }
    
    .film-border::after {
        content: '';
        position: absolute;
        bottom: -40px;
        left: 0;
        right: 0;
        height: 40px;
        background: inherit;
    }
    
    /* 标题区域升级 */
    .timeline-header {
        position: relative;
        z-index: 10;
        text-align: center;
        padding: 60px 20px 40px 20px;
        background: linear-gradient(180deg, rgba(0,0,0,0.05) 0%, transparent 100%);
    }
    
    .main-title {
        font-family: 'Noto Serif SC', serif;
        font-size: 3.5rem;
        letter-spacing: 8px;
        color: #1A1A1A;
        font-weight: 300;
        text-shadow: 
            2px 2px 4px rgba(0,0,0,0.1),
            0px 1px 2px rgba(0,0,0,0.1);
        animation: titleFadeIn 2s ease-out;
        margin-bottom: 10px;
    }
    
    .subtitle {
        font-size: 0.85rem;
        text-transform: uppercase;
        letter-spacing: 4px;
        color: #888888;
        font-weight: 400;
        animation: titleFadeIn 2s ease-out 0.3s both;
    }
    
    .mobile-note {
        font-family: inherit;
        font-size: 0.75rem;
        font-style: italic;
        color: #666666;
        margin: 15px 20px 0 60px;
        text-align: right;
        animation: titleFadeIn 2s ease-out 0.6s both;
    }
    
    @keyframes titleFadeIn {
        from { 
            opacity: 0; 
            transform: translateY(-20px) scale(0.95);
        }
        to { 
            opacity: 1; 
            transform: translateY(0) scale(1);
        }
    }
    
    /* 时间轴图表容器 */
    .timeline-chart-container {
        position: relative;
        z-index: 5;
        padding: 0 20px 40px 20px;
    }
    
    /* 悬停卡片效果 */
    .work-tooltip {
        position: absolute;
        background: rgba(255, 255, 255, 0.95);
        border: 1px solid rgba(0,0,0,0.1);
        border-radius: 8px;
        padding: 15px;
        box-shadow: 0 8px 32px rgba(0,0,0,0.1);
        backdrop-filter: blur(10px);
        z-index: 1000;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.3s ease;
        max-width: 300px;
    }
    
    .work-tooltip.show {
        opacity: 1;
    }
    
    .work-tooltip h4 {
        margin: 0 0 8px 0;
        font-size: 1.1rem;
        color: #1A1A1A;
        font-weight: 600;
    }
    
    .work-tooltip p {
        margin: 4px 0;
        font-size: 0.9rem;
        color: #666;
    }
    
    /* 年份标记优化 */
    .year-marker {
        position: absolute;
        font-size: 0.8rem;
        color: #999;
        font-weight: 500;
        letter-spacing: 1px;
        text-transform: uppercase;
    }
    
    /* 图例区域优化 */
    .legend-container {
        background: rgba(255,255,255,0.8);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(0,0,0,0.05);
        border-radius: 8px;
        padding: 15px 25px;
        margin: 0 auto 30px auto;
        display: inline-block;
        box-shadow: 0 4px 16px rgba(0,0,0,0.05);
    }
    
    /* 媒体画廊样式 */
    .media-gallery {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
        gap: 30px;
        padding: 40px 20px;
        max-width: 1200px;
        margin: 0 auto;
    }
    
    .media-card {
        position: relative;
        background: #FFFFFF;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 
            0 4px 20px rgba(0,0,0,0.08),
            0 1px 3px rgba(0,0,0,0.02);
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        cursor: pointer;
    }
    
    .media-card:hover {
        transform: translateY(-8px) scale(1.02);
        box-shadow: 
            0 12px 40px rgba(0,0,0,0.15),
            0 4px 12px rgba(0,0,0,0.08);
    }
    
    .media-card img,
    .media-card video {
        display: block;
    }

    .media-frame {
        position: relative;
        width: 100%;
        aspect-ratio: 16 / 9;
        overflow: hidden;
        background: #000000;
    }

    .media-frame img,
    .media-frame video {
        position: absolute;
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }
    
    .media-info {
        padding: 20px;
        background: linear-gradient(135deg, #fafafa 0%, #ffffff 100%);
        border-top: 1px solid rgba(0,0,0,0.05);
    }
    
    .media-title {
        font-family: 'Noto Serif SC', serif;
        font-size: 1.1rem;
        font-weight: 600;
        color: #1A1A1A;
        margin: 0 0 8px 0;
        letter-spacing: 0.5px;
    }
    
    .media-desc {
        font-size: 0.9rem;
        color: #666666;
        line-height: 1.5;
        margin: 0;
    }
    
    /* 信息面板样式 */
    .info-panel {
        background: linear-gradient(135deg, #ffffff 0%, #f8f9fa 100%);
        border-radius: 16px;
        padding: 30px;
        margin: 40px auto;
        max-width: 1000px;
        box-shadow: 
            0 8px 32px rgba(0,0,0,0.06),
            inset 0 1px 0 rgba(255,255,255,0.9);
        border: 1px solid rgba(0,0,0,0.03);
    }
    
    .info-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 25px;
        margin-bottom: 20px;
    }
    
    .info-item {
        text-align: center;
        padding: 20px;
        background: rgba(255,255,255,0.8);
        border-radius: 12px;
        border: 1px solid rgba(0,0,0,0.04);
        transition: all 0.3s ease;
    }
    
    .info-item:hover {
        background: rgba(255,255,255,1);
        transform: translateY(-2px);
        box-shadow: 0 4px 16px rgba(0,0,0,0.08);
    }
    
    .info-label {
        font-size: 0.8rem;
        text-transform: uppercase;
        letter-spacing: 2px;
        color: #999999;
        margin-bottom: 8px;
        font-weight: 500;
    }
    
    .info-value {
        font-family: 'Noto Serif SC', serif;
        font-size: 1.2rem;
        color: #1A1A1A;
        font-weight: 600;
        letter-spacing: 0.5px;
    }
    
    /* 迷你时间轴 */
    .mini-timeline {
        margin: 40px auto;
        max-width: 1000px;
        padding: 30px;
        background: linear-gradient(90deg, rgba(74,85,104,0.05) 0%, rgba(139,115,85,0.05) 50%, rgba(107,142,35,0.05) 100%);
        border-radius: 16px;
        border: 1px solid rgba(0,0,0,0.03);
    }
    
    .mini-timeline-title {
        font-family: 'Noto Serif SC', serif;
        font-size: 1.4rem;
        color: #1A1A1A;
        margin-bottom: 20px;
        text-align: center;
        font-weight: 600;
    }
    
    .timeline-events {
        display: flex;
        justify-content: space-between;
        align-items: center;
        position: relative;
        padding: 20px 0;
    }
    
    .timeline-events::before {
        content: '';
        position: absolute;
        top: 50%;
        left: 10%;
        right: 10%;
        height: 2px;
        background: linear-gradient(90deg, #4A5568, #8B7355, #6B8E23);
        z-index: 1;
    }
    
    .timeline-event {
        position: relative;
        z-index: 2;
        text-align: center;
        flex: 1;
    }
    
    .timeline-dot {
        width: 16px;
        height: 16px;
        border-radius: 50%;
        background: #FFFFFF;
        border: 3px solid #4A5568;
        margin: 0 auto 10px auto;
        box-shadow: 0 2px 8px rgba(0,0,0,0.2);
    }
    
    .timeline-date {
        font-size: 0.9rem;
        color: #666666;
        margin-bottom: 4px;
    }
    
    .timeline-label {
        font-size: 0.8rem;
        color: #1A1A1A;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 1px;
    }

    /* 桌面端隐藏移动端垂直时间轴 */
    .mobile-timeline-container {
        display: none !important;
    }

    /* 桌面端注释样式 */
    .desktop-note {
        font-size: 0.75rem;
        font-style: italic;
        color: #666666;
        margin-top: 15px;
        text-align: center;
    }

    /* 桌面端隐藏手机端注释 */
    @media only screen and (min-width: 769px) {
        .mobile-note {
            display: none !important;
        }
    }

    /* [Mobile] 移动端适配 - 768px以下 */
    @media only screen and (max-width: 768px) {
        /* 页面容器过渡 */
        .timeline-container, .detail-container {
            transition: opacity 0.3s ease, transform 0.3s ease;
        }
        
        /* 隐藏桌面端横向时间轴，只显示移动端纵向时间轴 */
        .desktop-only {
            display: none !important;
        }
        
        /* 移动端隐藏桌面端注释 */
        .desktop-note {
            display: none !important;
        }
        
        /* 隐藏Streamlit Plotly容器 - 使用data-testid强制隐藏 */
        [data-testid="stPlotlyChart"],
        div[data-testid="stPlotlyChart"],
        .element-container:has([data-testid="stPlotlyChart"]),
        .stPlotlyChart,
        .stPlotlyChart > div,
        .stPlotlyChart > div > div {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
            visibility: hidden !important;
        }
        
        /* 隐藏桌面端图表容器 */
        .timeline-chart-container.desktop-only,
        div.timeline-chart-container.desktop-only {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
            visibility: hidden !important;
        }
        
        /* 确保移动端时间轴容器显示 */
        .mobile-timeline-container {
            display: block !important;
        }
        
        /* [Mobile] 标题字体大小调整 */
        .main-title {
            font-size: 2.0rem !important;
            letter-spacing: 6px !important;
        }
        
        .subtitle {
            font-size: 0.75rem !important;
            letter-spacing: 3px !important;
        }
        
        .mobile-note {
            font-size: 0.65rem !important;
            margin-top: 10px !important;
        }
        
        /* 升级版移动端时间轴样式 */
        .mobile-timeline-container {
            display: block !important;
            padding: 20px 2px !important;
            background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 50%, #f5f5f5 100%);
            border-radius: 20px;
            margin: 20px 0;
        }
        
        .mobile-timeline-item {
            display: flex;
            margin-bottom: 40px;
            position: relative;
            align-items: flex-start;
        }
        
        .mobile-timeline-date {
            flex: 0 0 60px;
            text-align: left;
            font-family: 'Noto Serif SC', serif;
            font-weight: 700;
            color: #1A1A1A;
            position: relative;
            padding-right: 15px;
            padding-top: 5px;
        }
        
        .mobile-timeline-year {
            font-size: 1.3rem;
            line-height: 1.2;
            margin-bottom: 5px;
            color: #2c3e50;
        }
        
        .mobile-timeline-month {
            font-size: 0.9rem;
            color: #7f8c8d;
            font-weight: 400;
        }
        
        .mobile-year-group {
            margin-bottom: 30px;
            position: relative;
        }
        
        .mobile-year-header {
            font-family: 'Noto Serif SC', serif;
            font-size: 1.8rem;
            font-weight: 800;
            color: #2c3e50;
            margin-bottom: 20px;
            padding-left: 8px;
            position: relative;
            text-shadow: 1px 1px 2px rgba(0,0,0,0.1);
            z-index: 1;
        }
        
        .mobile-year-header::before {
            content: '';
            position: absolute;
            left: 6px;
            top: 45%;
            transform: translateY(-50%);
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: linear-gradient(135deg, #e74c3c, #c0392b);
            box-shadow: 0 2px 8px rgba(231, 76, 60, 0.3);
            z-index: -1;
            opacity: 0.8;
        }
        
        .mobile-timeline-content {
            flex: 1;
            position: relative;
            margin-left: 15px;
        }
        
        .mobile-timeline-card {
            background: #ffffff;
            border-radius: 16px;
            padding: 25px;
            box-shadow: 
                0 8px 32px rgba(0,0,0,0.12),
                0 2px 8px rgba(0,0,0,0.08);
            border: 1px solid rgba(0,0,0,0.06);
            transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
            cursor: pointer;
            position: relative;
            overflow: hidden;
            min-height: 120px;
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
        }
        
        .mobile-timeline-card:hover {
            transform: translateY(-4px) scale(1.02);
            box-shadow: 
                0 16px 48px rgba(0,0,0,0.18),
                0 4px 16px rgba(0,0,0,0.12);
        }
        
        .mobile-card-content {
            position: relative;
            z-index: 2;
            padding: 15px;
        }
        
        .mobile-work-title {
            font-family: 'Noto Serif SC', serif;
            font-size: 1.2rem;
            font-weight: 700;
            color: #ffffff;
            margin: 0 0 10px 0;
            line-height: 1.3;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.8), 0 0 10px rgba(0,0,0,0.6);
        }
        
        .mobile-work-meta {
            font-size: 0.95rem;
            color: #ffffff;
            margin: 0;
            line-height: 1.4;
            text-shadow: 1px 1px 3px rgba(0,0,0,0.8), 0 0 8px rgba(0,0,0,0.5);
        }
        
        /* 垂直时间线优化 */
        .mobile-timeline-item::before {
            content: '';
            position: absolute;
            left: 45px;
            top: 35px;
            bottom: -40px;
            width: 3px;
            background: #4A5568;
            z-index: 1;
            border-radius: 2px;
            box-shadow: 0 0 10px rgba(74, 85, 104, 0.2);
        }
        
        .mobile-timeline-item:last-child::before {
            display: none;
        }
        
        .mobile-timeline-dot {
            position: absolute;
            left: 39px;
            top: 12px;
            width: 16px;
            height: 16px;
            border-radius: 50%;
            background: #ffffff;
            border: 4px solid #4A5568;
            box-shadow: 
                0 0 0 4px rgba(74, 85, 104, 0.2),
                0 4px 12px rgba(0,0,0,0.15);
            z-index: 3;
            transition: all 0.3s ease;
        }
        
        .mobile-timeline-card:hover .mobile-timeline-dot {
            border-color: #8B7355;
            box-shadow: 
                0 0 0 4px rgba(139, 115, 85, 0.2),
                0 6px 16px rgba(0,0,0,0.25);
        }
        
        /* 确保卡片容器不截断tooltip */
        .mobile-timeline-card {
            overflow: visible !important;
        }
        
        .mobile-timeline-content {
            overflow: visible !important;
            position: relative;
        }
        
        /* 移动端点击区域样式 */
        .mobile-click-area {
            position: absolute;
            top: 10px;
            right: 10px;
            z-index: 100;
        }
        
        .mobile-click-icon {
            display: inline-block;
            font-size: 14px;
            line-height: 26px;
            vertical-align: middle;
            color: #4A5568;
        }
        
        .mobile-click-link {
            display: inline-block;
            width: 28px;
            height: 28px;
            background: rgba(255, 255, 255, 0.9);
            border: 1px solid rgba(74, 85, 104, 0.3);
            border-radius: 50%;
            text-align: center;
            text-decoration: none;
            cursor: pointer;
            transition: all 0.2s ease;
            opacity: 0.8;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            position: relative;
        }
        
        .mobile-click-link:hover {
            background: rgba(255, 255, 255, 1);
            border-color: #4A5568;
            opacity: 1;
            transform: scale(1.1);
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        }
        
        .mobile-click-link:hover::after {
            content: attr(title);
            position: absolute;
            top: 100%;
            right: 0;
            margin-top: 8px;
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.98);
            color: #1A1A1A;
            font-size: 0.75rem;
            line-height: 1.5;
            border-radius: 8px;
            white-space: pre-line;
            z-index: 1000;
            box-shadow: 0 4px 20px rgba(0,0,0,0.08), 0 1px 3px rgba(0,0,0,0.05);
            backdrop-filter: blur(20px);
            border: 1px solid rgba(0,0,0,0.06);
            min-width: 140px;
            max-width: 200px;
            text-align: left;
            letter-spacing: 0.2px;
        }
        
        .mobile-click-link:hover::before {
            content: '';
            position: absolute;
            top: 100%;
            right: 14px;
            margin-top: 2px;
            border: 6px solid transparent;
            border-bottom-color: rgba(255, 255, 255, 0.98);
            z-index: 1001;
        }
        
        /* 页面切换过渡动画 */
        .page-transition {
            animation: fadeInUp 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        }
        
        @keyframes fadeInUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }
        
        /* 页面容器过渡 */
        .timeline-container, .detail-container {
            transition: opacity 0.3s ease, transform 0.3s ease;
        }
        
        /* [Mobile Detail View B] 背景图横版呈现 - 同时覆盖top-banner和top-banner-v2 */
        .top-banner, .top-banner-v2 {
            background-size: 100% auto !important;
            background-position: center center !important;
            background-repeat: no-repeat !important;
            width: 100% !important;
            height: auto !important;
            min-height: 300px !important;
        }
        
        /* [Mobile Detail View B] 作品名字体改小，更靠左 - 同时覆盖两个类 */
        .top-banner h1, .top-banner-v2 h1 {
            font-size: 2rem !important;
            letter-spacing: 3px !important;
            text-align: left !important;
            margin-left: 15px !important;
            margin-right: 15px !important;
        }
        
        /* banner内容区域左对齐 */
        .top-banner .banner-content, .top-banner-v2 .banner-content {
            align-items: flex-start !important;
            padding-left: 15px !important;
            text-align: left !important;
        }
        
        /* [Mobile Detail View B] 三段视频增加上下边界距离 */
        .vogue-media {
            margin-top: -10px !important;
            margin-bottom: -10px !important;
        }
        
        .media-container {
            padding: 5px 0 !important;
            margin-top: -20px !important;
        }
        
        /* [Mobile] 台词栏上移减少空白 */
        .texture-bg {
            margin-top: -10px !important;
            padding-top: 10px !important;
        }
        
        .quote-container {
            margin-top: 0 !important;
            padding-top: 15px !important;
        }
        
        /* [Mobile Detail Timeline] 详情页时间轴样式 - 扁长条设计 */
        .detail-timeline-container {
            display: block !important;
            margin: 20px 15px !important;
            padding: 15px !important;
            background: linear-gradient(135deg, #f8f9fa 0%, #ffffff 100%) !important;
            border-radius: 12px !important;
            box-shadow: 0 2px 12px rgba(0,0,0,0.06) !important;
            z-index: 100 !important;
            position: relative !important;
        }
        
        .detail-timeline-content {
            position: relative !important;
            padding: 10px 0 !important;
        }
        
        .detail-timeline-bar-wrapper {
            position: relative !important;
            height: 40px !important;
            margin: 0 10% !important;
        }
        
        .detail-timeline-bar {
            position: absolute !important;
            top: 50% !important;
            left: 0 !important;
            right: 0 !important;
            height: 8px !important;
            background: linear-gradient(90deg, #4A5568 0%, #6B7280 50%, #8B7355 100%) !important;
            transform: translateY(-50%) !important;
            border-radius: 4px !important;
            box-shadow: inset 0 1px 2px rgba(0,0,0,0.1) !important;
        }
        
        .detail-timeline-point {
            position: absolute !important;
            top: 50% !important;
            transform: translateY(-50%) !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
        }
        
        .detail-timeline-point::before {
            content: '' !important;
            width: 16px !important;
            height: 16px !important;
            border-radius: 50% !important;
            background: #ffffff !important;
            border: 3px solid #4A5568 !important;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2) !important;
            margin-bottom: 6px !important;
        }
        
        .detail-timeline-point.end-point::before {
            border-color: #8B7355 !important;
        }
        
        .detail-timeline-point-label {
            font-family: 'Noto Serif SC', serif !important;
            font-size: 0.75rem !important;
            font-weight: 600 !important;
            color: #2c3e50 !important;
            white-space: nowrap !important;
        }
        
        .detail-timeline-release {
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            margin-top: 15px !important;
        }
        
        .detail-timeline-dot.release {
            width: 14px !important;
            height: 14px !important;
            border-radius: 50% !important;
            background: #e74c3c !important;
            border: 3px solid #e74c3c !important;
            box-shadow: 0 0 0 4px rgba(231, 76, 60, 0.15) !important;
            margin-bottom: 6px !important;
        }
        
        .detail-timeline-release-label {
            font-family: 'Noto Serif SC', serif !important;
            font-size: 0.8rem !important;
            font-weight: 600 !important;
            color: #e74c3c !important;
        }
    }
    
    /* [Desktop] 桌面端强制隐藏详情页横向时间轴 */
    @media only screen and (min-width: 769px) {
        .detail-timeline-container {
            display: none !important;
            height: 0 !important;
            margin: 0 !important;
            padding: 0 !important;
            overflow: hidden !important;
            visibility: hidden !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

# === 4. 状态管理 ===
if 'page_view' not in st.session_state:
    st.session_state['page_view'] = 'timeline'
if 'selected_work' not in st.session_state:
    st.session_state['selected_work'] = None

# === 5. 数据读取 ===
FILE_PATH = "六元_作品时间轴.xlsx"

@st.cache_data(ttl=300)  # 缓存5分钟
def load_data(mtime): 
    """加载数据并优化处理"""
    try:
        if not os.path.exists(FILE_PATH):
            logger.error(f"Data file not found: {FILE_PATH}")
            return pd.DataFrame()
        
        df = pd.read_excel(FILE_PATH, engine='openpyxl')
        
        # 列名映射
        rename_map = {"图1": "素材1", "图2": "素材2", "图3": "素材3"}
        df = df.rename(columns=rename_map)
        
        # 将作品名强制转换为字符串，避免数字作品名（如1921）被识别为整数
        if "作品" in df.columns:
            df["作品"] = df["作品"].astype(str).str.strip()
        
        # 优化路径清洗 - 只保留文件名
        cols_to_clean = ["素材1", "素材2", "素材3", "logo", "背景图"]
        for col in cols_to_clean:
            if col in df.columns:
                df[col] = df[col].apply(lambda x: get_safe_path(x))
        
        # 日期处理
        date_cols = ["开机", "杀青", "上映"]
        for col in date_cols:
            if col in df.columns:
                df[f"{col}_dt"] = pd.to_datetime(df[col], errors='coerce')
        
        # 按开机时间排序
        df = df.sort_values("开机_dt", ascending=False)
        
        logger.info(f"Loaded {len(df)} records successfully")
        return df
        
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        return pd.DataFrame()

mtime = os.path.getmtime(FILE_PATH) if os.path.exists(FILE_PATH) else 0
df = load_data(mtime)

# === 6. URL参数处理 ===
# [Mobile] 处理URL参数中的作品选择 - 立即清除参数避免历史记录
query_params = st.query_params
if 'work' in query_params:
    selected_work_from_url = query_params['work']
    # 检查作品是否存在于数据中
    if not df.empty and selected_work_from_url in df["作品"].values:
        st.session_state['selected_work'] = selected_work_from_url
        st.session_state['page_view'] = 'detail'
    # 立即清除URL参数，避免产生历史记录
    st.query_params.clear()
    st.rerun()

# ==========================================
# 视图 A：时间轴主页（优化版）
# ==========================================
def show_timeline():
    """优化时间轴显示 - Vogue风格"""
    # 页面容器
    st.markdown("""
    <div class="timeline-container page-transition">
        <div class="film-border"></div>
        <div class="timeline-header">
            <div class="main-title">刘昊然作品集锦</div>
            <div class="subtitle">The Cinematic Universe</div>
        </div>
        <div class="timeline-chart-container desktop-only">
    """, unsafe_allow_html=True)
    
    if df.empty:
        st.error("无法加载数据")
        st.markdown("</div></div>", unsafe_allow_html=True)
        return
    
    # 创建图表
    fig = go.Figure()
    
    # 升级莫兰迪/电影色系
    unique_types = df['类型'].dropna().unique()
    colors = ['#4A5568', '#8B7355', '#6B8E23', '#B8860B', '#5F9EA0', '#CD853F']  # 更精致的色系
    type_color_map = {t: colors[i % len(colors)] for i, t in enumerate(unique_types)}
    
    # 优化数据结构
    work_data_map = {}
    valid_works = []
    
    for i, row in df.iterrows():
        if pd.isna(row.get("开机_dt")): 
            continue
        
        work_name = row["作品"]
        work_data_map[work_name] = {
            'row_index': i,
            'type': row.get('类型', '电影'),
            'start_date': row['开机_dt'],
            'end_date': row['杀青_dt'],
            'release_date': row.get('上映_dt')
        }
        valid_works.append(work_name)  
    
    # 批量添加轨迹
    for i, row in df.iterrows():
        if pd.isna(row.get("开机_dt")): 
            continue
        
        color = type_color_map.get(row['类型'], '#666')
        work_name = row["作品"]
        
        # 拍摄条 - 增强视觉效果
        fig.add_trace(go.Scatter(
            x=[row["开机_dt"], row["杀青_dt"]], 
            y=[work_name, work_name],
            mode="lines",
            line=dict(color=color, width=9, shape='spline'),
            customdata=[[work_name, "shooting"], [work_name, "shooting"]],
            hovertext=f"<b>{work_name}</b><br>🎬 拍摄: {row['开机_dt'].strftime('%Y-%m')} ~ {row['杀青_dt'].strftime('%Y-%m')}",
            hoverinfo="text",
            showlegend=False,
            text=[work_name, work_name],
            hoverlabel=dict(
                bgcolor="rgba(255,255,255,0.95)",
                font_size=13,
                font_color="#1A1A1A",
                bordercolor=color
            )
        ))
        
        # 上映处理
        if pd.notna(row.get("上映_dt")):
            # 连接线 - 更精细
            fig.add_trace(go.Scatter(
                x=[row["杀青_dt"], row["上映_dt"]],
                y=[work_name, work_name],
                mode="lines",
                line=dict(color="#666", width=1, dash="5,3", shape='spline'),
                hoverinfo="none",
                showlegend=False
            ))
            
            # 上映点 - 增强效果
            fig.add_trace(go.Scatter(
                x=[row["上映_dt"]], 
                y=[work_name],
                mode="markers",
                marker=dict(
                    color=color,
                    size=10,
                    symbol="circle",
                    line=dict(color="#FFFFFF", width=2),
                    opacity=1
                ),
                customdata=[[work_name, "release"]],
                hovertext=f"<b>{work_name}</b><br>🌟 上映: {row['上映_dt'].strftime('%Y-%m')}<br>点击进入详情",
                hoverinfo="text",
                showlegend=False,
                hoverlabel=dict(
                    bgcolor="rgba(255,255,255,0.95)",
                    font_size=13,
                    font_color="#1A1A1A",
                    bordercolor=color
                )
            ))
        else:
            # 未上映标记 - 更精致
            fig.add_trace(go.Scatter(
                x=[row["杀青_dt"]], 
                y=[work_name],
                mode="markers",
                marker=dict(
                    color=color,
                    size=10,
                    symbol="diamond",
                    line=dict(color="#FFFFFF", width=2),
                    opacity=0.9
                ),
                customdata=[[work_name, "unreleased"]],
                hovertext=f"<b>{work_name}</b><br>⏳ 已杀青，待上映<br>点击进入详情",
                hoverinfo="text",
                showlegend=False,
                hoverlabel=dict(
                    bgcolor="rgba(255,255,255,0.95)",
                    font_size=13,
                    font_color="#1A1A1A",
                    bordercolor=color
                )
            ))
    
    # 图例 - 使用新的容器样式
    legend_traces = []
    for type_name, color in type_color_map.items():
        legend_traces.append(go.Scatter(
            x=[None], y=[None],
            mode="lines",
            line=dict(width=10, color=color),
            name=type_name,
            showlegend=True
        ))
    
    for trace in legend_traces:
        fig.add_trace(trace)
    
    # 优化布局 - 更精致的设置
    fig.update_layout(
        height=max(450, len(valid_works) * 50 + 250),  # 稍微增加高度
        plot_bgcolor='rgba(255,255,255,0.8)',  # 半透明白色背景
        paper_bgcolor='rgba(255,255,255,0)',  # 透明背景
        xaxis=dict(
            title="",
            gridcolor='rgba(0,0,0,0.05)',  # 更淡的网格线
            tickformat="%Y",
            side="top",
            tickfont=dict(color="#666666", size=14, family="Noto Serif SC"),
            showline=True, 
            linecolor='rgba(0,0,0,0.1)',  # 更淡的轴线
            linewidth=1,
            showgrid=True,
            zeroline=False
        ),
        yaxis=dict(
            showgrid=True, 
            gridcolor='rgba(200,200,200,0.5)',  # 加深背景水平灰线颜色
            gridwidth=1,
            zeroline=False,
            showline=False,
            tickfont=dict(size=13, color='#666666', family='Noto Serif SC'),
            autorange=True
        ),
        margin=dict(l=20, r=20, t=100, b=60),  # 调整边距
        clickmode='event+select',
        dragmode=False,
        legend=dict(
            orientation="h", 
            yanchor="bottom", 
            y=1.02, 
            xanchor="center", 
            x=0.5,
            bgcolor='rgba(255,255,255,0.9)',
            font=dict(color="#666666", size=13, family="Noto Serif SC"),
            bordercolor="rgba(0,0,0,0.05)",
        ),
        hoverlabel=dict(
            bgcolor="rgba(255,255,255,0.95)",
            font_size=13, 
            font_color="#1A1A1A"
        )
    )
    
    # 显示图表
    event = st.plotly_chart(fig, use_container_width=True, on_select="rerun", key="timeline")
    
    # 优化事件处理
    if event and event.get('selection'):
        selected_points = event['selection'].get('points', [])
        
        if selected_points:
            point = selected_points[0]
            work_name = None
            
            # 多种方式获取作品名
            customdata = point.get('customdata')
            if customdata and isinstance(customdata, list) and len(customdata) > 0:
                if isinstance(customdata[0], list):
                    work_name = customdata[0][0]
                else:
                    work_name = customdata[0]
            
            if not work_name:
                work_name = point.get('y')
            
            if not work_name:
                text_data = point.get('text')
                if text_data:
                    work_name = text_data[0] if isinstance(text_data, list) else text_data
            
            if work_name and work_name in df["作品"].values:
                st.session_state['selected_work'] = work_name
                st.session_state['page_view'] = 'detail'
                st.rerun()
            elif work_name:
                st.sidebar.error(f"作品名 '{work_name}' 不在数据集中")
    
    # 关闭桌面端容器
    st.markdown("""
        </div>
        <div class="desktop-note">*仅列出主要作品，信息更新至2026.2.20，部分作品因拍摄时间无法确认，采用估算时间。</div>
    """, unsafe_allow_html=True)
    
    # [Mobile] 移动端垂直时间轴
    if not df.empty:
        # 添加手机端注释
        st.markdown("""
        <div class="mobile-note">*仅列出主要作品，信息更新至2026.2.20<br>下方所列月份为开机时间<br>部分作品因拍摄时间无法确认，采用估算时间</div>
        """, unsafe_allow_html=True)
        
        # 按开机时间排序，获取有效作品
        valid_works_mobile = []
        for i, row in df.iterrows():
            if pd.notna(row.get("开机_dt")): 
                valid_works_mobile.append({
                    'name': str(row["作品"]).strip(),
                    'year': row["开机_dt"].year,
                    'date': row["开机_dt"].strftime('%Y-%m'),
                    'role': str(row.get('角色', '')).strip(),
                    'type': str(row.get('类型', '')).strip(),
                    'row_index': i
                })
        
        # 按年份降序排列（最新的在前）
        valid_works_mobile.sort(key=lambda x: x['year'], reverse=True)
        
        # [Mobile] 生成移动端时间轴HTML - React兼容的点击事件
        html_parts = []
        
        # 月份映射
        month_map = {
            '01': '1月', '02': '2月', '03': '3月', '04': '4月',
            '05': '5月', '06': '6月', '07': '7月', '08': '8月',
            '09': '9月', '10': '10月', '11': '11月', '12': '12月'
        }
        
        # 按年份分组
        works_by_year = {}
        for work in valid_works_mobile:
            year = work['year']
            if year not in works_by_year:
                works_by_year[year] = []
            works_by_year[year].append(work)
        
        # 按年份降序排列（最新的在前）
        sorted_years = sorted(works_by_year.keys(), reverse=True)
        
        # 生成HTML - 使用data属性和事件监听器
        html_parts.append('<div class="mobile-timeline-container">')
        
        for year in sorted_years:
            year_works = works_by_year[year]
            # 按月份降序排列
            year_works.sort(key=lambda x: x['date'], reverse=True)
            
            # 添加年份标题
            html_parts.append(f'<div class="mobile-year-group">')
            html_parts.append(f'<div class="mobile-year-header">{year}</div>')
            
            # 添加该年份下的所有作品
            for work in year_works:
                # 安全转义特殊字符
                work_name = str(work['name']).replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                work_role = str(work['role']).replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                work_type = str(work['type']).replace('"', '&quot;').replace("'", '&#39;').replace('<', '&lt;').replace('>', '&gt;')
                
                # 获取背景图URL - 修复版本
                bg_style = ""
                try:
                    work_row = df[df["作品"] == work['name']].iloc[0]
                    bg_path = work_row.get("背景图")
                    if pd.isna(bg_path) or str(bg_path) == 'nan':
                        # 尝试使用第一个素材作为背景
                        mat1 = work_row.get("素材1")
                        if pd.notna(mat1) and not str(mat1).lower().endswith(('.mp4', '.mov', '.webm')):
                            bg_path = mat1
                    
                    if pd.notna(bg_path) and str(bg_path) != 'nan':
                        bg_path = find_file_with_extensions(bg_path, ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'])
                        if bg_path and os.path.exists(bg_path):
                            bg_mtime = os.path.getmtime(bg_path)
                            bg_data_url = get_image_data_url_cached(bg_path, bg_mtime, max_width=800, quality=70, target_max_b64_len=50_000)
                            if bg_data_url and bg_data_url.startswith('data:image/'):
                                # 使用background-image方案 - 移除引号，添加opacity
                                bg_style = f'background-image: url({bg_data_url}); background-size: cover; background-position: center center; background-repeat: no-repeat; opacity: 0.8;'
                except Exception as e:
                    # 调试信息
                    pass
                
                # 提取月份
                date_parts = work['date'].split('-')
                month_str = month_map.get(date_parts[1], date_parts[1]) if len(date_parts) > 1 else ''
                
                # 生成作品HTML - 添加可点击的小图标在卡片右上角
                html_parts.append(f'<div class="mobile-timeline-item">')
                html_parts.append('<div class="mobile-timeline-date">')
                html_parts.append('<div class="mobile-timeline-month">' + month_str + '</div>')
                html_parts.append('</div>')
                html_parts.append('<div class="mobile-timeline-dot"></div>')
                html_parts.append('<div class="mobile-timeline-content">')
                
                # 获取日期信息用于tooltip
                work_rows = df[df["作品"] == work['name']]
                if work_rows.empty:
                    # 如果找不到作品，使用默认值
                    shooting_start = ''
                    shooting_end = ''
                    release_date = ''
                else:
                    work_row = work_rows.iloc[0]
                    shooting_start = work_row.get('开机', '')
                    shooting_end = work_row.get('杀青', '')
                    release_date = work_row.get('上映', '')
                
                # 格式化日期信息（精确到月）
                def format_date_to_month(date_str):
                    """将日期格式化为年月"""
                    if not date_str or date_str == 'nan':
                        return ''
                    try:
                        # 尝试解析日期，只取年月
                        dt = pd.to_datetime(date_str, errors='coerce')
                        if pd.isna(dt):
                            return date_str
                        return dt.strftime('%Y.%m')
                    except:
                        return date_str
                
                shooting_start_fmt = format_date_to_month(shooting_start)
                shooting_end_fmt = format_date_to_month(shooting_end)
                release_date_fmt = format_date_to_month(release_date)
                
                shooting_info = f"{shooting_start_fmt} ~ {shooting_end_fmt}" if shooting_start_fmt and shooting_end_fmt else (shooting_start_fmt or '')
                release_info = release_date_fmt if release_date_fmt else "已杀青，待上映"
                
                # 构建tooltip内容（使用换行符分隔）
                tooltip_content = f"拍摄：{shooting_info}\n上映：{release_info}\n点击进入详情页"
                
                html_parts.append(f'<div class="mobile-timeline-card" style="{bg_style}">')
                html_parts.append('<div class="mobile-card-content">')
                html_parts.append('<div class="mobile-work-title">' + work_name + '</div>')
                html_parts.append('<div class="mobile-work-meta">' + work_role + ' • ' + work_type + '</div>')
                html_parts.append('</div>')
                
                # 添加可点击的小图标在卡片右上角 - 使用内联onclick事件
                import urllib.parse
                work_name_encoded = urllib.parse.quote(work['name'], safe='')
                html_parts.append(f'''
                <div class="mobile-click-area">
                    <a href="?work={work_name_encoded}" class="mobile-click-link" title="{tooltip_content}" onclick="window.location.replace(this.href); return false;">
                        <span class="mobile-click-icon">🔗</span>
                    </a>
                </div>
                ''')
                html_parts.append('</div>')
                html_parts.append('</div>')
                html_parts.append('</div>')
            
            # 关闭年份组
            html_parts.append('</div>')
        
        # 关闭容器
        html_parts.append('</div>')
        
        # 拼接最终HTML
        final_html = ''.join(html_parts)
        
        # [Mobile] 使用st.markdown渲染
        st.markdown(final_html, unsafe_allow_html=True)
    
    # 关闭主容器
    st.markdown("""
    </div>
    """, unsafe_allow_html=True)

# ==========================================
# 🌌 视图 B：详情页 (优化版)
# ==========================================
def show_detail():
    """优化详情页显示"""
    work_name = st.session_state['selected_work']
    try:
        work_info = df[df["作品"] == work_name].iloc[0]
    except Exception as e:
        logger.error(f"Error getting work info: {e}")
        st.session_state['page_view'] = 'timeline'
        st.rerun()

    # --- 背景图处理 ---
    bg_image_path = None
    if "背景图" in df.columns: 
        bg_image_path = work_info.get("背景图")
    if pd.isna(bg_image_path) or str(bg_image_path) == 'nan':
        # 如果没有背景图，尝试使用第一个素材作为背景
        mat1 = work_info.get("素材1")
        if pd.notna(mat1) and not str(mat1).lower().endswith(('.mp4', '.mov', '.webm')): 
            bg_image_path = mat1

    if pd.notna(bg_image_path) and str(bg_image_path) != 'nan':
        bg_image_path = find_file_with_extensions(bg_image_path, ['.jpg', '.jpeg', '.png', '.webp', '.heic', '.heif'])
    
    # Logo处理
    logo_path = work_info.get("logo") if "logo" in df.columns else None
    logo_html = ""
    
    if pd.notna(logo_path) and str(logo_path) != 'nan':
        logo_path = find_file_with_extensions(logo_path, ['.png', '.jpg', '.jpeg', '.webp'])
        if logo_path and os.path.exists(logo_path):
            logo_mtime = os.path.getmtime(logo_path)
            b64_logo = get_base64_cached(logo_path, logo_mtime)
            if b64_logo:
                logo_ext = Path(logo_path).suffix.lower()
                logo_mime = "image/png" if logo_ext == ".png" else ("image/webp" if logo_ext == ".webp" else "image/jpeg")
                logo_html = f'<div class="logo-container fade-in"><img src="data:{logo_mime};base64,{b64_logo}" alt="{work_name} Logo"></div>'
    
    # 删除logo部分，只保留作品名
    logo_html = f"<h1 class='serif-font' style='font-size: 2.8rem; margin: 30px 0; letter-spacing: 4px;'>{work_name}</h1>"

    # --- 返回按钮 ---
    st.markdown("""
    <div style="position: relative; z-index: 1000; margin-bottom: 20px;">
    """, unsafe_allow_html=True)
    
    if st.button("← 返回时间轴", key="back_button"):
        st.session_state['page_view'] = 'timeline'
        # 清除URL参数，返回干净的时间轴页面
        st.query_params.clear()
        st.rerun()
    
    st.markdown("</div>", unsafe_allow_html=True)
   
    # 详情页过渡容器
    st.markdown('<div class="detail-container page-transition">', unsafe_allow_html=True)
    
    # 使用Streamlit容器构建顶部区域
    with st.container():
        # 如果有背景图
        if pd.notna(bg_image_path) and str(bg_image_path) != 'nan' and bg_image_path and os.path.exists(bg_image_path):
            bg_fmt = detect_image_format(bg_image_path)
            bg_mtime = os.path.getmtime(bg_image_path)
            bg_data_url = get_image_data_url_cached(bg_image_path, bg_mtime, max_width=1400, quality=85, target_max_b64_len=150_000)
            try:
                logger.error(f"Detail background path={bg_image_path} sizeMB={os.path.getsize(bg_image_path)/1024/1024:.2f}")
                logger.error(f"Detail background detected_format={bg_fmt}")
                logger.error(f"Detail background data_url prefix={bg_data_url[:24] if bg_data_url else None} len={len(bg_data_url) if bg_data_url else 0}")
            except Exception as _e:
                pass
            if bg_data_url:
                # 获取台词数据
                quote_text = work_info.get('台词', '时光无声。')
                role_name = str(work_info.get('角色', '')).strip()
                quote_source = role_name if role_name else work_name
                
                # 创建CSS样式 - 文字在背景图内部
                bg_css = f'''
                <style>
                .top-banner-v2 {{
                    position: relative;
                    width: 100%;
                    height: 400px;
                    overflow: hidden;
                    margin-bottom: 40px;
                    background: #000000;
                    display: flex;
                    flex-direction: column;
                    justify-content: center;
                    align-items: flex-start;
                    padding: 60px 40px;
                }}
                .top-banner * {{
                    color: #FFFFFF !important;
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.5) !important;
                }}
                .banner-bg-img {{
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    object-fit: cover;
                    object-position: center center;
                    z-index: 0;
                }}
                .bg-overlay {{
                    position: absolute;
                    top: 0;
                    left: 0;
                    width: 100%;
                    height: 100%;
                    background: rgba(0,0,0,0.3);
                    z-index: 1;
                }}
                .banner-content {{
                    position: relative;
                    z-index: 2;
                    max-width: 800px;
                    width: 100%;
                    text-align: left;
                }}
                /* 桌面端文字上移 */
                @media only screen and (min-width: 769px) {{
                    .banner-content {{ margin-top: -120px; }}
                }}
                .vogue-title {{
                    font-family: 'Noto Serif SC', serif;
                    font-size: 2.9rem;
                    font-weight: 700;
                    margin: 0 0 10px 0;
                    letter-spacing: 4px;
                    line-height: 1.2;
                    color: #FFFFFF !important;
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                }}
                .vogue-subtitle {{
                    font-family: 'Times New Roman', 'SimSun', '宋体', serif;
                    font-size: 0.9rem;
                    font-weight: 400;
                    margin: 0 0 20px 0;
                    letter-spacing: 1px;
                    opacity: 0.9;
                    color: #FFFFFF !important;
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                    text-transform: uppercase;
                }}
                .vogue-meta {{
                    font-size: 0.9rem;
                    font-weight: 200;  /* 添加字体粗细控制 */
                    line-height: 1.6;
                    margin: 10px 0;
                    color: #FFFFFF !important;
                    text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
                }}
                .vogue-divider {{
                    width: 80px;
                    height: 1px;
                    background: #FFFFFF;
                    margin: 20px 0;
                    opacity: 0.8;
                }}
                
                /* [Mobile] 详情页背景图横版呈现 */
                @media (max-width: 768px) {{
                    .top-banner-v2 {{
                        height: auto !important;
                        min-height: 260px !important;
                        padding: 30px 20px !important;
                        margin-bottom: 0 !important;
                    }}
                    .banner-bg-img {{
                        object-fit: cover !important;
                        object-position: center center !important;
                        width: 100% !important;
                        height: 100% !important;
                    }}
                    .vogue-title {{
                        font-size: 1.6rem !important;
                        letter-spacing: 2px !important;
                        text-align: left !important;
                        margin-left: 0 !important;
                        margin-bottom: 5px !important;
                    }}
                    .vogue-subtitle {{
                        margin-bottom: 10px !important;
                        font-size: 0.75rem !important;
                    }}
                    .vogue-divider {{
                        margin: 10px 0 !important;
                    }}
                    .vogue-meta {{
                        margin: 5px 0 !important;
                        font-size: 0.8rem !important;
                    }}
                    .banner-content {{
                        padding-left: 0 !important;
                        padding-right: 20px !important;
                    }}
                }}
                </style>
                '''
                st.markdown(bg_css, unsafe_allow_html=True)
                
                # 创建背景图容器，文字在内部
                banner_html = f"""
<div class="top-banner-v2">
  <img class="banner-bg-img" src="{bg_data_url}" alt="{work_name} Background">
  <div class="bg-overlay"></div>
  <div class="banner-content">
    <div class="vogue-subtitle">
      NO.{work_info.name + 1} COLLECTION
    </div>
    <h1 class="vogue-title">
      {work_name}
    </h1>
    <div class="vogue-divider"></div>
    <div class="vogue-meta">
      <span style="font-weight: 600;">{work_info.get('角色', '')}</span>
      &nbsp;&nbsp;•&nbsp;&nbsp;
      <span style="font-weight: 600;">{work_info.get('类型', '')}</span>
    </div>
  </div>
</div>
"""

                components.html(bg_css + banner_html, height=380, scrolling=False)
              
                # 在背景图下方只显示台词（白色背景区域）
                st.markdown("""
                <div class="texture-bg" style="background: #FFFFFF;">
                """, unsafe_allow_html=True)
                
                # 台词（升级版呈现）
                quote_text = work_info.get('台词', '时光无声。')
                role_name = str(work_info.get('角色', '')).strip()
                quote_source = role_name if role_name else work_name
                
                st.markdown(f"""
                <div class="quote-container fade-in" style="padding: 30px 40px 20px 40px;">
    <span class="quote-mark open">"</span>
    <p class="quote-text" style="font-size: 1.2rem; line-height: 1.6; margin-bottom: 10px;">{quote_text}</p>
    <span class="quote-mark close">"</span>
    <div class="quote-source" style="font-size: 0.9rem; margin-top: 8px;">— {quote_source}</div>
</div>
</div>
                """, unsafe_allow_html=True)
            else:
                st.warning(f"背景图文件无法解码（检测格式={bg_fmt}）：请确认 Excel 的背景图列指向的是你新另存的真正 JPG/PNG 文件，而不是旧的 HEIC 文件或只是改了后缀名的文件。当前读取路径：{bg_image_path}")

                fallback_css = """
<style>
.top-banner {
  position: relative;
  width: 100%;
  height: 500px;
  overflow: hidden;
  margin-bottom: 40px;
  background: radial-gradient(1200px 600px at 30% 35%, rgba(255,255,255,0.12) 0%, rgba(0,0,0,0.96) 55%, rgba(0,0,0,1) 100%);
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  padding: 60px 40px;
}
.bg-overlay {
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  background: linear-gradient(90deg, rgba(0,0,0,0.70) 0%, rgba(0,0,0,0.45) 45%, rgba(0,0,0,0.55) 100%);
  z-index: 1;
}
.banner-content {
  position: relative;
  z-index: 2;
  max-width: 800px;
  width: 100%;
  padding-left: 40px;
  padding-right: 40px;
  text-align: left;
}
</style>
"""

                fallback_banner_html = f"""
<div class="top-banner">
  <div class="bg-overlay"></div>
  <div class="banner-content">
    <div class="vogue-subtitle" style="font-family: 'Noto Serif SC', serif; font-size: 11px; letter-spacing: 4px; text-transform: uppercase; margin-bottom: 15px; font-weight: 500; color: #FFFFFF; text-shadow: 2px 2px 5px rgba(0,0,0,0.6);">
      NO.{work_info.name + 1} COLLECTION
    </div>
    <h1 style="font-family: 'Noto Serif SC', serif; font-size: 4.5rem; letter-spacing: 8px; margin: 30px 0; line-height: 1.1; color: #FFFFFF; text-shadow: 3px 3px 8px rgba(0,0,0,0.8);">
      {work_name}
    </h1>
    <div style="width: 80px; height: 1px; background: #FFFFFF; margin: 20px 0; opacity: 0.8;"></div>
    <div class="vogue-meta" style="font-family: 'Noto Serif SC', serif; font-size: 14px; margin-top: 20px; letter-spacing: 2px; font-weight: 400; color: #FFFFFF; text-shadow: 2px 2px 5px rgba(0,0,0,0.6);">
      <span style="font-weight: 600;">{work_info.get('角色', '')}</span>
      &nbsp;&nbsp;•&nbsp;&nbsp;
      <span style="font-weight: 600;">{work_info.get('类型', '')}</span>
    </div>
  </div>
</div>
"""

                components.html(fallback_css + fallback_banner_html, height=490, scrolling=False)

                st.markdown("""
                <div class="texture-bg" style="background: #FFFFFF;">
                """, unsafe_allow_html=True)

                st.markdown(logo_html, unsafe_allow_html=True)

                quote_text = work_info.get('台词', '时光无声。')
                role_name = str(work_info.get('角色', '')).strip()
                quote_source = role_name if role_name else work_name

                st.markdown(f"""
                <div class="quote-container fade-in">
    <span class="quote-mark open">"</span>
    <p class="quote-text">{quote_text}</p>
    <span class="quote-mark close">"</span>
    <div class="quote-source">— {quote_source}</div>
</div>
</div>
                """, unsafe_allow_html=True)
        else:
            # 无背景图的版本
            st.markdown(f"""
            <div class="texture-bg fade-in" style="width: 100%; padding: 80px 0 50px 0; text-align: center; background: #FFFFFF;">
                <p style='font-size: 11px; letter-spacing: 4px; color: #666666; text-transform: uppercase; margin-bottom: 20px; font-weight: 500;'>
                    NO.{work_info.name + 1} COLLECTION
                </p>
                
                <h1 class='serif-font' style='font-size: 4.5rem; margin: 30px 0; letter-spacing: 8px; color: #1A1A1A;'>
                    {work_name}
                </h1>
                
                <div style='width: 80px; height: 1px; background: #000000; margin: 20px auto; opacity: 0.6;'></div>
                
                <p style='font-size: 14px; color: #666666; margin-top: 20px; letter-spacing: 2px; font-weight: 400;'>
                    <span style='color: #333333; font-weight: 600;'>{work_info.get('角色', '')}</span> 
                    &nbsp;&nbsp;•&nbsp;&nbsp; 
                    <span style='color: #333333; font-weight: 600;'>{work_info.get('类型', '')}</span>
                </p>
            </div>
            """, unsafe_allow_html=True)
            
            # Logo
            st.markdown(f"""
            <div class="texture-bg" style="background: #FFFFFF;">
                {logo_html}
            """, unsafe_allow_html=True)
            
            # 台词（升级版呈现）
            quote_text = work_info.get('台词', '时光无声。')
            role_name = str(work_info.get('角色', '')).strip()
            quote_source = role_name if role_name else work_name
            
            st.markdown(f"""
            <div class="quote-container fade-in">
    <span class="quote-mark open">"</span>
    <p class="quote-text">{quote_text}</p>
    <span class="quote-mark close">"</span>
    <div class="quote-source">— {quote_source}</div>
</div>
            """, unsafe_allow_html=True)

    # --- 媒体流（连贯居中版）---
    st.markdown('<div class="media-container">', unsafe_allow_html=True)

    for i, col in enumerate(["素材1", "素材2", "素材3"], 1):
        path = work_info.get(col)
        if pd.isna(path) or path == 'nan' or path == '':
            continue

        final_path = find_file_with_extensions(path, ['.mp4', '.mov', '.webm', '.jpg', '.jpeg', '.png', '.gif', '.webp'])
        if not final_path or not os.path.exists(final_path):
            continue

        file_mtime = os.path.getmtime(final_path)
        b64 = get_base64_cached(final_path, file_mtime)
        if not b64:
            continue

        ext = Path(final_path).suffix.lower()
        is_video = ext in ['.mp4', '.mov', '.webm']

        if is_video:
            video_mime = "video/mp4" if ext == ".mp4" else ("video/webm" if ext == ".webm" else "video/quicktime")
            st.markdown(
                f'<div class="vogue-media"><div class="media-frame"><video autoplay loop muted playsinline controls><source src="data:{video_mime};base64,{b64}" type="{video_mime}"></video></div></div>',
                unsafe_allow_html=True
            )
        else:
            img_mime = (
                "image/png" if ext == ".png" else
                ("image/webp" if ext == ".webp" else
                 ("image/gif" if ext == ".gif" else "image/jpeg"))
            )
            st.markdown(
                f'<div class="vogue-media"><div class="media-frame"><img src="data:{img_mime};base64,{b64}" alt="{work_name} - 素材{i}"></div></div>',
                unsafe_allow_html=True
            )

    st.markdown('</div>', unsafe_allow_html=True)
    
    # 详情页内容结束，关闭过渡容器
    st.markdown('</div>', unsafe_allow_html=True)

# === 入口 ===
if st.session_state['page_view'] == 'timeline':
    show_timeline()
else:
    show_detail()


