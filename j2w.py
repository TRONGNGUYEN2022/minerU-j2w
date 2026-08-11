import base64
import io
import json
import os
import re
import zipfile
import time
import shutil
import tempfile
import logging
from datetime import datetime
from bs4 import BeautifulSoup
import requests
import streamlit as st
import streamlit.components.v1 as components
import pypandoc

# --- CẤU HÌNH GHI FILE LOG ---
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "app.log")

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    encoding="utf-8"
)

def log_info(msg):
    logging.info(msg)

def log_error(msg):
    logging.error(msg)

# Import thư viện google-genai chính thức mới nhất
try:
    from google import genai
    from google.genai import types
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

# Import thư viện mistralai SDK 2.0
try:
    from mistralai.client import Mistral
    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False

# --- CẤU HÌNH LƯU KEY RA FILE TRÊN SERVER ---
CONFIG_FILE = "config_keys.json"

def load_saved_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_config(gemini_key, mistral_key, mineru_key):
    config_data = {
        "gemini_key": gemini_key,
        "mistral_key": mistral_key,
        "mineru_key": mineru_key
    }
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
    except:
        pass

saved_config = load_saved_config()
DEFAULT_MINERU_KEY = saved_config.get("mineru_key", "sk-IDb81Oj2W6pHrODooHN0xtKTxEXNzipsnZP6OxAqAl65Kz9O")
DEFAULT_GEMINI_KEY = saved_config.get("gemini_key", "AQ.Ab8RN6IiVh_ufztKik5rSMrl39c-U6_L6v5oy_Qru1-YNUBdRg")
DEFAULT_MISTRAL_KEY = saved_config.get("mistral_key", "Asht2uDLjH8WTWnU06dBWdPbpcVQrbt5")

st.set_page_config(page_title="RPG Spiritual Document Converter", page_icon="⚔️", layout="wide")
MINERU_BASE_URL = "https://mineru.net"

DEFAULT_DOWNLOAD_DIR = "downloaded_mineru_files"
os.makedirs(DEFAULT_DOWNLOAD_DIR, exist_ok=True)
os.makedirs(os.path.join(DEFAULT_DOWNLOAD_DIR, "images"), exist_ok=True)

# --- KHỞI TẠO SESSION STATE ---
if "api_key_editable" not in st.session_state:
    st.session_state.api_key_editable = False
if "gemini_key_editable" not in st.session_state:
    st.session_state.gemini_key_editable = False
if "mistral_key_editable" not in st.session_state:
    st.session_state.mistral_key_editable = False

if "active_json" not in st.session_state:
    st.session_state.active_json = None
if "active_images_dict" not in st.session_state:
    st.session_state.active_images_dict = {}
if "active_file_name" not in st.session_state:
    st.session_state.active_file_name = "Document"

# Trạng thái game RPG
if "spiritual_journey_started" not in st.session_state:
    st.session_state.spiritual_journey_started = False

if "saved_gemini_key" not in st.session_state:
    st.session_state.saved_gemini_key = DEFAULT_GEMINI_KEY
if "saved_mistral_key" not in st.session_state:
    st.session_state.saved_mistral_key = DEFAULT_MISTRAL_KEY
if "saved_mineru_key" not in st.session_state:
    st.session_state.saved_mineru_key = DEFAULT_MINERU_KEY

if "mistral_preview_markdown" not in st.session_state:
    st.session_state.mistral_preview_markdown = ""
if "mistral_docx_bytes" not in st.session_state:
    st.session_state.mistral_docx_bytes = None
if "mistral_raw_zip_bytes" not in st.session_state:
    st.session_state.mistral_raw_zip_bytes = None


def cleanup_old_temp_files():
    root_dir = "."
    for f_name in os.listdir(root_dir):
        if f_name.lower().endswith((".jpeg", ".jpg", ".png", ".docx", ".zip")) or f_name == "temp_input.md":
            try:
                os.remove(os.path.join(root_dir, f_name))
            except:
                pass

def clean_and_wrap_latex(latex_str):
    if not latex_str: return ""
    clean_str = latex_str.strip()
    if clean_str.startswith("$") and clean_str.endswith("$"):
        clean_str = clean_str[1:-1].strip()
    return f"${clean_str}$"

def extract_zip_and_get_data(zip_bytes):
    images_dict = {}
    json_data = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        for filename in z.namelist():
            if "images/" in filename and not filename.endswith("/"):
                img_name = os.path.basename(filename)
                images_dict[img_name] = z.read(filename)
            elif filename.endswith("layout.json") and not filename.startswith("__MACOSX"):
                try:
                    json_data = json.loads(z.read(filename).decode("utf-8"))
                except Exception as e:
                    log_error(f"Lỗi đọc layout.json từ ZIP: {e}")
    return json_data, images_dict

def get_image_bytes(img_path_str, images_dict, json_upload_dir=""):
    if not img_path_str: return None
    clean_name = os.path.basename(img_path_str)
    if images_dict and clean_name in images_dict:
        return io.BytesIO(images_dict[clean_name])
    return None

def upload_temp_file_robust(uploaded_file):
    upload_services = [
        {"name": "Catbox", "url": "https://catbox.moe/user/api.php", "data": {"reqtype": "fileupload"}, "file_key": "fileToUpload"},
        {"name": "Litterbox", "url": "https://litterbox.catbox.moe/resources/api.php", "data": {"reqtype": "fileupload", "time": "24h"}, "file_key": "fileToUpload"},
        {"name": "TmpFiles", "url": "https://tmpfiles.org/api/v1/upload", "data": {}, "file_key": "file"}
    ]
    file_bytes = uploaded_file.getvalue()
    file_name = uploaded_file.name
    file_type = uploaded_file.type

    for service in upload_services:
        try:
            files = {service["file_key"]: (file_name, file_bytes, file_type)}
            res = requests.post(service["url"], data=service["data"], files=files, timeout=30)
            if res.status_code == 200:
                result_text = res.text.strip()
                if service["name"] == "TmpFiles":
                    try:
                        res_json = res.json()
                        if res_json.get("status") == "success":
                            raw_url = res_json.get("data", {}).get("url", "")
                            if "tmpfiles.org/" in raw_url and not "tmpfiles.org/dl/" in raw_url:
                                raw_url = raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
                            return raw_url
                    except:
                        pass
                elif result_text.startswith("http"):
                    return result_text
        except Exception:
            continue
    return None

def start_mineru_task_by_url(api_token, file_url):
    url = f"{MINERU_BASE_URL}/api/v4/extract/task"
    headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
    payload = {"url": file_url, "model_version": "vlm", "is_ocr": True}
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("code") == 0:
                return res_json.get("data", {}).get("task_id")
    except: pass
    return None

def check_task_status_v4(api_token, task_id):
    url = f"{MINERU_BASE_URL}/api/v4/extract/task/{task_id}"
    headers = {"Authorization": f"Bearer {api_token}"}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.json().get("data", {})
    except: pass
    return {}

def fallback_process_with_gemini(uploaded_file, gemini_api_key, selected_model):
    if not GEMINI_AVAILABLE: return None, {}
    try:
        client = genai.Client(api_key=gemini_api_key)
        file_bytes = uploaded_file.getvalue()
        mime_type = uploaded_file.type
        prompt = "Bạn là chuyên gia OCR tài liệu toán học. Hãy đọc chính xác, toán học đặt trong $...$ hoặc $$...$$. Trình bày bằng HTML sạch sẽ dùng thẻ <p>, <h3>, bảng dùng <table>."
        response = client.models.generate_content(
            model=selected_model,
            contents=[types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt]
        )
        html_content = re.sub(r"^```html\s*", "", response.text, flags=re.IGNORECASE)
        html_content = re.sub(r"\s*```$", "", html_content)
        return {"pdf_info": [{"para_blocks": [{"type": "text", "lines": [{"spans": [{"type": "text", "content": html_content}]}]}]}]}, {}
    except:
        return None, {}

def render_pure_math_preview(json_data, images_dict, json_upload_dir="", file_name="document"):
    preview_inner_html = '<div id="content-to-copy" style="font-family: Arial, sans-serif; line-height: 1.8; color: #2d3748; font-size: 16px;">'
    pages = json_data if isinstance(json_data, list) else json_data.get("pdf_info", [])
    for page in pages:
        if not isinstance(page, dict): continue
        for block in page.get("para_blocks", page.get("blocks", [])):
            if not isinstance(block, dict): continue
            b_type = block.get("type")
            if b_type in ["text", "title", "paragraph", "header", "footer"]:
                p_text = ""
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        content = span.get("content", span.get("text", ""))
                        p_text += clean_and_wrap_latex(content) if span.get("type") in ["inline_equation", "equation", "math"] else content
                if p_text.strip():
                    preview_inner_html += f"<p style='margin-bottom: 10px;'>{p_text}</p>"
    preview_inner_html += '</div>'
    st.markdown("### 👁️ Bản xem trước Nội dung")
    components.html(f'<div style="padding:20px; background:#fff; border:1px solid #ccc; max-height:500px; overflow-y:auto;">{preview_inner_html}</div>', height=550)


# ==========================================
# MÀN HÌNH GAME NHẬP VAI TÂM LINH (CÓ ÂM THANH CHUÔNG & TIẾNG CHIM HÓT)
# ==========================================
if not st.session_state.spiritual_journey_started:
    
    # Nhúng giao diện game RPG kết hợp âm thanh tự động
    rpg_audio_component = """
    <!DOCTYPE html>
    <html lang="vi">
    <head>
        <meta charset="UTF-8">
        <style>
            .rpg-box {
                background: linear-gradient(135deg, #1e1b4b, #0f172a);
                border: 2px solid #f59e0b;
                border-radius: 12px;
                padding: 25px;
                color: #f3f4f6;
                box-shadow: 0 10px 30px rgba(0,0,0,0.8);
                font-family: sans-serif;
                text-align: center;
            }
            .rpg-title {
                color: #fbbf24;
                font-size: 24px;
                font-weight: bold;
                margin-bottom: 8px;
                text-transform: uppercase;
                letter-spacing: 1px;
            }
            .rpg-dialogue {
                background: rgba(15, 23, 42, 0.95);
                border-left: 5px solid #f59e0b;
                padding: 15px 20px;
                border-radius: 8px;
                margin: 15px 0;
                font-size: 15px;
                line-height: 1.6;
                color: #e2e8f0;
                text-align: left;
            }
            .audio-panel {
                margin-top: 15px;
                display: flex;
                justify-content: center;
                gap: 10px;
            }
            button.rpg-btn {
                background: #0d9488;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 6px;
                cursor: pointer;
                font-weight: bold;
                font-size: 13px;
                transition: 0.3s;
            }
            button.rpg-btn:hover { background: #0f766e; }
            button.stop-btn { background: #b91c1c; }
            button.stop-btn:hover { background: #991b1b; }
        </style>
    </head>
    <body>
        <div class="rpg-box">
            <div class="rpg-title">⚔️ Lữ Trình Tâm Linh: Cổng Khai Sáng Tri Thức ⚔️</div>
            <div style="color: #94a3b8; font-style: italic; font-size: 13px;">Chương I: Tiếng Chuông Ngân & Sự Tỉnh Thức Giữa Vô Thường</div>
            
            <div class="rpg-dialogue">
                <b>📜 Hiền Giả (Thiền Sư / Đức Cha):</b><br>
                <i>"Chào lữ khách phương xa! Hãy lắng nghe tiếng chuông đồng vọng và tiếng chim hót thanh bình để trút bỏ mọi muộn phiền văn bản. Hãy chọn thánh địa và thắp nén tâm hương để mở khóa cánh cổng chính..."</i>
            </div>

            <!-- Phát âm thanh tiếng chim hót & thiên nhiên ngầm -->
            <audio autoplay loop id="rpg-nature-audio">
                <source src="https://actions.google.com/sounds/v1/ambiences/morning_birds.ogg" type="audio/ogg">
            </audio>

            <div class="audio-panel">
                <button class="rpg-btn" onclick="document.getElementById('rpg-nature-audio').play()">🔊 Bật Âm Thanh Chim Hót & Chuông</button>
                <button class="rpg-btn stop-btn" onclick="document.getElementById('rpg-nature-audio').pause()">🔇 Tắt Âm Thanh</button>
            </div>
        </div>
    </body>
    </html>
    """
    components.html(rpg_audio_component, height=270)

    st.markdown("<br>", unsafe_allow_html=True)

    col_rpg1, col_rpg2 = st.columns(2)
    with col_rpg1:
        st.markdown("### 🛡️ Lựa Chọn Tông Tôn / Môn Phái")
        realm_choice = st.selectbox(
            "Chọn không gian thánh địa khởi đầu:",
            [
                "🏛️ Cổ Tự Thiền Môn (Sư Tôn dẫn dắt, tiếng chuông đồng vọng)",
                "⛪ Thánh Đường Ánh Sáng (Đức Cha dẫn dắt, ánh hào quang kính màu)",
                "🌿 Thâm Sơn Cốc Ẩn (Đạo sĩ hòa mình cùng thiên nhiên cỏ cây)"
            ]
        )
        
        class_choice = st.selectbox(
            "Chọn Class Nhân Vật Của Bạn:",
            [
                "🧘 Hành Giả Tĩnh Lặng (Buff độ tập trung cao độ)",
                "📚 Học Giả Thông Thái (Tăng tốc độ giải mã văn bản)",
                "⚔️ Hiệp Sĩ Thiện Nguyện (Chuyên gieo hạt phước báu tích cực)"
            ]
        )

    with col_rpg2:
        st.markdown("### 🕯️ Nghi Thức Khấn Nguyện (Quest Oath)")
        player_oath = st.text_area(
            "Viết lời thề / Câu niệm phật / Tâm nguyện hôm nay của bạn:",
            placeholder="Ví dụ: Nam Mô Bản Sư Thích Ca Mâu Ní Phật / Amen / Hôm nay ta quyết tâm hoàn thành trọn vẹn văn bản này..."
        )
        
        accept_quest = st.checkbox("⚡ Ta đã sẵn sàng chấp nhận thử thách và cam kết hành động thiện lương.")

    st.markdown("<br>", unsafe_allow_html=True)
    
    col_b1, col_b2, col_b3 = st.columns([1, 2, 1])
    with col_b2:
        if accept_quest:
            if st.button("🔮 KHAI MỞ CỔNG TRI THỨC (START GAME)", use_container_width=True):
                st.session_state.spiritual_journey_started = True
                log_info(f"Người chơi chọn Thánh Địa: {realm_choice} | Class: {class_choice} | Tâm nguyện: {player_oath}")
                st.success("🎉 Nghi thức thành công! Năng lượng phước báu đã được kích hoạt. Đang dịch chuyển vào thế giới chính...")
                st.rerun()
        else:
            st.warning("🔒 Bạn cần hoàn thành lời thề và tích chọn ô xác nhận để mở khóa cửa ải!")

    st.stop()


# ==========================================
# 5. GIAO DIỆN CHÍNH (3 TABS)
# ==========================================
st.title("📐 Convert PDF/Image to word (MinerU - Mistral - Gemini)")

tab1, tab2, tab4 = st.tabs([
    "🚀 Gửi lên MinerU Server (API)", 
    "🌪️ Mistral OCR (API + Pandoc)",
    "📁 Tải file có sẵn (Offline)"
])

# ==========================================
# TAB 1: MINERU SERVER & GEMINI
# ==========================================
with tab1:
    st.subheader("Cấu hình API Keys (MinerU & Gemini dự phòng)")
    col_k1, col_k2 = st.columns(2)
    with col_k1:
        api_token_input = st.text_input("Nhập MinerU API Token:", value=st.session_state.saved_mineru_key, type="password", disabled=not st.session_state.api_key_editable)
        if st.button("Đổi MinerU Key"):
            st.session_state.api_key_editable = not st.session_state.api_key_editable
            st.rerun()
        if st.session_state.api_key_editable and st.button("Lưu MinerU Key"):
            st.session_state.saved_mineru_key = api_token_input
            save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)
            st.session_state.api_key_editable = False
            st.success("Đã lưu MinerU Key vào server!")
            log_info("Đã cập nhật MinerU API Key mới.")
            st.rerun()
            
    with col_k2:
        def update_gemini_key():
            st.session_state.saved_gemini_key = st.session_state.gemini_input_field
            save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)

        gemini_token_input = st.text_input("Nhập Gemini API Key (Dự phòng):", value=st.session_state.saved_gemini_key, type="password", disabled=not st.session_state.gemini_key_editable, key="gemini_input_field", on_change=update_gemini_key)
        if st.button("Đổi Gemini Key"):
            st.session_state.gemini_key_editable = not st.session_state.gemini_key_editable
            st.rerun()
        if st.session_state.gemini_key_editable and st.button("Lưu Gemini Key"):
            st.session_state.saved_gemini_key = gemini_token_input
            save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)
            st.session_state.gemini_key_editable = False
            st.success("Đã lưu Gemini Key vào server!")
            log_info("Đã cập nhật Gemini API Key mới.")
            st.rerun()

    selected_gemini_model = st.selectbox("Chọn Model Gemini dự phòng:", ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-1.5-flash", "gemini-1.5-pro"], index=0)
    api_file = st.file_uploader("Chọn file PDF hoặc ảnh cần phân tích qua MinerU", type=["pdf", "png", "jpg", "jpeg"], key="tab1_upload")
    
    if st.button("📤 Gửi & Phân tích qua MinerU"):
        if not api_file:
            st.warning("Vui lòng chọn file!")
        else:
            success_processed = False
            task_id = None
            log_info(f"Bắt đầu xử lý file qua Tab 1: {api_file.name}")
            
            with st.spinner("Đang tải file lên máy chủ trung gian..."):
                file_url = upload_temp_file_robust(api_file)
                
            if file_url:
                with st.spinner("Đang khởi tạo tác vụ xử lý trên MinerU..."):
                    task_id = start_mineru_task_by_url(st.session_state.saved_mineru_key, file_url)
                
                if task_id:
                    st.success(f"Khởi tạo thành công! Task ID: `{task_id}`. Đang chờ MinerU xử lý...")
                    progress_bar = st.progress(0)
                    status_text = st.empty()
                    
                    for i in range(60):
                        time.sleep(5)
                        task_data = check_task_status_v4(st.session_state.saved_mineru_key, task_id)
                        state = task_data.get("state")
                        status_text.text(f"Trạng thái MinerU: {state}")
                        
                        if state == "done":
                            full_zip_url = task_data.get("full_zip_url")
                            if full_zip_url:
                                log_info(f"MinerU hoàn thành task {task_id}. Đang tải file ZIP kết quả...")
                                r_zip = requests.get(full_zip_url)
                                if r_zip.status_code == 200:
                                    found_json, images_dict = extract_zip_and_get_data(r_zip.content)
                                    if found_json:
                                        st.session_state.active_json = found_json
                                        st.session_state.active_images_dict = images_dict
                                        st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                                        st.success("Đã hoàn tất phân tích bằng MinerU thành công!")
                                        log_info("Xử lý thành công bằng MinerU chính hãng.")
                                        success_processed = True
                                        st.rerun()
                            break
                        elif state == "failed":
                            log_error(f"MinerU báo lỗi xử lý thất bại (failed) đối với task {task_id}.")
                            st.warning("MinerU báo lỗi xử lý thất bại đối với file này.")
                            break
                        
                        progress_bar.progress(min((i + 1) * 1.5, 100))
                else:
                    log_warning_msg = "Không thể khởi tạo Task ID trên MinerU."
                    log_error(log_warning_msg)
                    st.warning(log_warning_msg)
            else:
                log_error("Không thể tải file lên máy chủ trung gian.")
                st.warning("Không thể tải file lên máy chủ trung gian.")

            if not success_processed:
                active_key = st.session_state.saved_gemini_key.strip()
                if active_key:
                    st.info(f"Đang chuyển sang trích xuất dự phòng bằng {selected_gemini_model} do MinerU không phản hồi...")
                    log_info(f"Chuyển hướng fallback sang Gemini model: {selected_gemini_model}")
                    with st.spinner(f"Đang xử lý bằng {selected_gemini_model}..."):
                        g_json, g_imgs = fallback_process_with_gemini(api_file, active_key, selected_gemini_model)
                        if g_json:
                            st.session_state.active_json = g_json
                            st.session_state.active_images_dict = g_imgs
                            st.session_state.active_file_name = api_file.name.rsplit(".", 1)[0]
                            st.success(f"Đã hoàn tất trích xuất thay thế bằng {selected_gemini_model}!")
                            st.rerun()
                else:
                    log_error("MinerU thất bại và thiếu Gemini API Key dự phòng.")
                    st.error("MinerU không khả dụng và chưa có Gemini API Key dự phòng để thay thế!")

# ==========================================
# TAB 2: MISTRAL OCR (TÍCH HỢP CỔNG PHƯỚC BÁU & TẢI WORD)
# ==========================================
with tab2:
    st.subheader("🌪️ Cấu hình Mistral OCR & Pandoc")
    def update_mistral_key():
        st.session_state.saved_mistral_key = st.session_state.mistral_input_field
        save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)

    mistral_token_input = st.text_input("Nhập Mistral API Key:", value=st.session_state.saved_mistral_key, type="password", disabled=not st.session_state.mistral_key_editable, key="mistral_input_field", on_change=update_mistral_key)
    if st.button("Đổi Mistral Key"):
        st.session_state.mistral_key_editable = not st.session_state.mistral_key_editable
        st.rerun()
    if st.session_state.mistral_key_editable and st.button("Lưu Mistral Key"):
        st.session_state.saved_mistral_key = mistral_token_input
        save_config(st.session_state.saved_gemini_key, st.session_state.saved_mistral_key, st.session_state.saved_mineru_key)
        st.session_state.mistral_key_editable = False
        st.success("Đã lưu Mistral Key vào server!")
        log_info("Đã cập nhật Mistral API Key mới.")
        st.rerun()

    mistral_file = st.file_uploader("Chọn file PDF hoặc ảnh xử lý qua Mistral OCR", type=["pdf", "png", "jpg", "jpeg"], key="mistral_upload")
    
    if st.button("🚀 Gửi PDF lên Mistral OCR & Phân tích"):
        active_m_key = st.session_state.saved_mistral_key.strip()
        if not mistral_file:
            st.warning("Vui lòng chọn file!")
        elif not active_m_key:
            st.error("Vui lòng nhập Mistral API Key!")
        elif not MISTRAL_AVAILABLE:
            st.error("Chưa cài đặt thư viện `mistralai`.")
        else:
            cleanup_old_temp_files()
            original_full_name = mistral_file.name
            base_name_only = original_full_name.rsplit('.', 1)[0]
            log_info(f"Bắt đầu xử lý Mistral OCR cho file: {original_full_name}")

            with st.spinner("Đang gửi PDF lên Mistral OCR API và xử lý nhúng ảnh chuẩn Pandoc..."):
                try:
                    client = Mistral(api_key=active_m_key)
                    file_bytes = mistral_file.getvalue()
                    base64_file = base64.b64encode(file_bytes).decode('utf-8')

                    ocr_response = client.ocr.process(
                        document={"type": "document_url", "document_url": f"data:application/pdf;base64,{base64_file}"},
                        model="mistral-ocr-latest",
                        include_image_base64=True,
                        include_blocks=True
                    )
                    
                    full_markdown = ""
                    images_dict = {}
                    
                    if hasattr(ocr_response, "pages"):
                        for idx, page in enumerate(ocr_response.pages):
                            page_md = page.markdown if hasattr(page, "markdown") else ""
                            page_md = re.sub(r'!\[(.*?)\]\([^)]*?(img[_-]\d+\.(?:jpeg|jpg|png))\)', r'![\1](\2)', page_md)
                            page_md_safe = re.sub(r'^\s*---\s*$', '<hr/>', page_md, flags=re.MULTILINE)
                            full_markdown += f"\n\n<hr/>\n<h3>Trang {idx+1}</h3>\n\n" + page_md_safe
                            
                            if hasattr(page, "images") and page.images:
                                for img in page.images:
                                    if hasattr(img, "id") and hasattr(img, "image_base64") and img.image_base64:
                                        img_id = img.id
                                        img_b64 = img.image_base64
                                        if "," in img_b64: img_b64 = img_b64.split(",")[1]
                                        try:
                                            img_data_decoded = base64.b64decode(img_b64)
                                            img_filename = img_id if img_id.lower().endswith((".jpeg", ".jpg", ".png")) else f"{img_id}.jpeg"
                                            images_dict[img_filename] = img_data_decoded
                                        except: 
                                            pass

                    zip_buffer = io.BytesIO()
                    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
                        zip_file.writestr("output.md", full_markdown)
                        for img_name, img_bytes in images_dict.items():
                            zip_file.writestr(f"images/{img_name}", img_bytes)
                    st.session_state.mistral_raw_zip_bytes = zip_buffer.getvalue()

                    with tempfile.TemporaryDirectory() as tmp_dir:
                        temp_md_path = os.path.join(tmp_dir, "temp_input.md")
                        with open(temp_md_path, "w", encoding="utf-8") as f:
                            f.write(full_markdown)
                        
                        for img_name, img_bytes in images_dict.items():
                            with open(os.path.join(tmp_dir, img_name), "wb") as img_f:
                                img_f.write(img_bytes)
                                
                        original_dir = os.getcwd()
                        os.chdir(tmp_dir)
                        
                        try:
                            output_docx = "Mistral_Output.docx"
                            pypandoc.convert_file(
                                "temp_input.md", 
                                'docx', 
                                outputfile=output_docx, 
                                extra_args=['--standalone', '--extract-media=.']
                            )
                            with open(output_docx, "rb") as f:
                                docx_bytes = f.read()
                            st.session_state.mistral_docx_bytes = docx_bytes
                        finally:
                            os.chdir(original_dir)

                    st.session_state.mistral_preview_markdown = full_markdown
                    st.session_state.active_images_dict = images_dict
                    st.session_state.active_file_name = base_name_only
                    
                    log_info("Xử lý Mistral OCR thành công.")
                    st.success("🎉 Xử lý Mistral OCR và nhúng ảnh vào Word thành công!")
                except Exception as e:
                    log_error(f"Lỗi Mistral OCR: {str(e)}")
                    st.error(f"Lỗi Mistral OCR: {e}")

    if st.session_state.mistral_preview_markdown:
        st.divider()
        current_file_name = st.session_state.get("active_file_name", "Document")
        
        st.subheader(f"👁️ Bản xem trước kết quả: {current_file_name}")

        # --- NÚT TẢI WORD LUÔN HIỂN THỊ ---
        if st.session_state.mistral_docx_bytes:
            st.download_button(
                label=f"📥 Tải xuống file Word chuẩn Pandoc ({current_file_name}.docx)",
                data=st.session_state.mistral_docx_bytes,
                file_name=f"{current_file_name}.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                use_container_width=True
            )
        else:
            st.warning("Chưa có dữ liệu file Word để tải.")

        # --- TÍNH NĂNG CỔNG THANH TOÁN TÂM LINH & NĂNG LƯỢNG TÍCH CỰC ---
        with st.expander("🌟 Gieo hạt phước báu & Lan tỏa năng lượng tích cực (Tùy tâm)"):
            st.info("Ứng dụng hoàn toàn miễn phí! Bạn có thể hoan hỷ chia sẻ một chút năng lượng tích cực bằng cách chọn hoặc tự viết một câu niệm, lời cầu nguyện hoặc việc tốt.")
            faith_choice = st.selectbox(
                "Bạn muốn gửi gắm năng lượng tích cực theo hình thức nào?",
                [
                    "🙏 Niệm Phật / Bồ Tát (Nam Mô A Di Đà Phật, Quan Thế Âm...)",
                    "✝️ Cầu nguyện theo Đức Chúa Trời / Thiên Chúa",
                    "📿 Lời khấn nguyện / Niệm thần thánh theo tôn giáo của tôi",
                    "🌿 Cam kết làm một việc tốt / Giúp ích cho đời trong hôm nay"
                ]
            )
            user_custom_prayer = st.text_area(
                "Viết câu niệm, lời khấn nguyện hoặc việc tốt của bạn vào đây:",
                placeholder="Ví dụ: Nam Mô Bản Sư Thích Ca Mâu Ni Phật / Amen / Hôm nay tôi sẽ giúp đỡ một người khó khăn..."
            )
            if st.button("✨ Gửi gắm phước báu & Nhận niệm lành"):
                st.success("🙏 Cảm ơn bạn rất nhiều vì đã gieo nhân duyên lành! Chúc bạn và gia đình một ngày ngập tràn bình an, may mắn và vạn sự hanh thông.")

        with st.expander("📦 Tùy chọn nâng cao: Tải gói file ZIP thô (Markdown + Thư mục Ảnh)"):
            if st.session_state.get("mistral_raw_zip_bytes"):
                st.download_button(
                    label="📥 Tải file ZIP thô về máy",
                    data=st.session_state.mistral_raw_zip_bytes,
                    file_name=f"{current_file_name}_Mistral_Raw.zip",
                    mime="application/zip"
                )

        raw_md = st.session_state.mistral_preview_markdown
        
        def replace_img_smart_html(match):
            alt_text = match.group(1)
            raw_path = match.group(2)
            target_name = os.path.basename(raw_path)
            matched_bytes = None
            for k, v in st.session_state.active_images_dict.items():
                if target_name in k or k in target_name:
                    matched_bytes = v
                    break
            if matched_bytes:
                b64_data = base64.b64encode(matched_bytes).decode('utf-8')
                return f'<div style="text-align: center; margin: 20px 0;"><img src="data:image/jpeg;base64,{b64_data}" style="max-width: 450px; border-radius: 8px; border: 1px solid #2d3748;" alt="{alt_text}" /></div>'
            return match.group(0)

        processed_html = re.sub(r'!\[(.*?)\]\((.*?)\)', replace_img_smart_html, raw_md)
        escaped_markdown_json = json.dumps(processed_html)

        mistral_component_html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.css">
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/katex.min.js"></script>
            <script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.8/dist/contrib/auto-render.min.js"></script>
            <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 10px; background-color: #ffffff; color: #2d3748; }}
                .btn-action {{ padding: 10px 20px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; margin-right: 10px; margin-bottom: 15px; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
                .btn-copy {{ background-color: #2b6cb0; }}
                .btn-copy:hover {{ background-color: #2c5282; }}
                #status-msg {{ margin-left: 10px; color: #2f855a; font-weight: bold; font-size: 13px; display: none; }}
                .preview-card {{ background-color: #ffffff; padding: 30px; border-radius: 10px; border: 1px solid #cbd5e0; max-height: 600px; overflow-y: auto; line-height: 1.8; }}
                table {{ border-collapse: collapse; width: auto; max-width: 100%; margin: 15px auto; border: 2px solid #2d3748; }}
                th {{ border: 2px solid #2d3748; padding: 6px 10px; background-color: #edf2f7; font-weight: bold; text-align: center; }}
                td {{ border: 2px solid #2d3748; padding: 6px 10px; vertical-align: middle; }}
            </style>
        </head>
        <body>
            <div>
                <button class="btn-action btn-copy" onclick="copyContentToClipboard()">📋 Sao chép nhanh (Dán vào Word)</button>
                <span id="status-msg">✔ Đã sao chép!</span>
            </div>
            <div class="preview-card" id="content-to-copy"></div>

            <script>
            const rawMarkdown = {escaped_markdown_json};
            document.getElementById('content-to-copy').innerHTML = marked.parse(rawMarkdown);

            function renderMath() {{
                if (typeof renderMathInElement === 'function') {{
                    renderMathInElement(document.getElementById('content-to-copy'), {{
                        delimiters: [
                            {{left: '$$', right: '$$', display: true}},
                            {{left: '$', right: '$', display: false}},
                            {{left: '\\\\[', right: '\\\\]', display: true}},
                            {{left: '\\\\(', right: '\\\\)', display: false}}
                        ],
                        throwOnError: false
                    }});
                }}
            }}

            document.addEventListener("DOMContentLoaded", renderMath);
            setTimeout(renderMath, 300);

            function copyContentToClipboard() {{
                const range = document.createRange();
                range.selectNode(document.getElementById('content-to-copy'));
                window.getSelection().removeAllRanges();
                window.getSelection().addRange(range);
                try {{
                    document.execCommand('copy');
                    showStatus("Đã sao chép vào bộ nhớ tạm! Mở Word và nhấn Ctrl+V");
                }} catch (err) {{ alert('Không thể sao chép tự động!'); }}
                window.getSelection().removeAllRanges();
            }}

            function showStatus(msg) {{
                const status = document.getElementById('status-msg');
                status.innerText = "✔ " + msg;
                status.style.display = 'inline';
                setTimeout(() => {{ status.style.display = 'none'; }}, 4000);
            }}
            </script>
        </body>
        </html>
        """
        components.html(mistral_component_html, height=780, scrolling=False)

# ==========================================
# TAB 4: TẢI FILE CÓ SẴN (OFFLINE)
# ==========================================
with tab4:
    st.subheader("📁 Nạp file layout.json hoặc file ZIP kết quả Offline")
    offline_file = st.file_uploader("Chọn file layout.json hoặc file ZIP kết quả", type=["json", "zip"], key="offline_all")
    image_files = st.file_uploader("Chọn các file ảnh liên quan (nếu dùng layout.json)", type=["png", "jpg", "jpeg"], accept_multiple_files=True, key="offline_imgs_all")
    
    if offline_file:
        try:
            file_bytes = offline_file.getvalue()
            if offline_file.name.endswith(".zip"):
                found_json, images_dict = extract_zip_and_get_data(file_bytes)
                if found_json:
                    st.session_state.active_json = found_json
                    st.session_state.active_images_dict = images_dict
                    st.session_state.active_file_name = offline_file.name.rsplit(".", 1)[0]
                    log_info(f"Nạp file ZIP offline thành công: {offline_file.name}")
                    st.success("Đã nạp file ZIP thành công!")
                    st.rerun()
            elif offline_file.name.endswith(".json"):
                st.session_state.active_json = json.loads(file_bytes.decode("utf-8"))
                st.session_state.active_file_name = offline_file.name.rsplit(".", 1)[0]
                if image_files:
                    st.session_state.active_images_dict = {img.name: img.getvalue() for img in image_files}
                log_info(f"Nạp file layout.json offline thành công: {offline_file.name}")
                st.success("Đã nạp file layout.json thành công!")
                st.rerun()
        except Exception as e:
            log_error(f"Lỗi khi đọc file offline: {e}")
            st.error(f"Lỗi khi đọc file: {e}")

# ==========================================
# HIỂN THỊ PREVIEW CHO MINERU / GEMINI
# ==========================================
if st.session_state.active_json is not None:
    st.divider()
    render_pure_math_preview(
        st.session_state.active_json, 
        st.session_state.active_images_dict,
        file_name=st.session_state.active_file_name
    )

# ==========================================
# KHUNG XEM NHẬT KÝ HỆ THỐNG (SYSTEM LOGS)
# ==========================================
st.divider()
with st.expander("🛠️ Xem Nhật ký hệ thống (System Logs)"):
    if os.path.exists("logs/app.log"):
        with open("logs/app.log", "r", encoding="utf-8") as log_file:
            log_content = log_file.read()
        st.text_area("Nội dung file app.log", log_content, height=300)
        if st.button("Xóa lịch sử log"):
            open("logs/app.log", "w", encoding="utf-8").close()
            st.success("Đã làm sạch file log!")
            st.rerun()
    else:
        st.info("Chưa có file log nào được tạo. Hãy thử thực hiện một tác vụ (như upload file) để sinh log.")